from __future__ import annotations

import json
import re
from dataclasses import replace

from textbook_builder.llm.candidate_extractor import LlmCandidatePayloadParser
from textbook_builder.llm.clients import ChatJsonClient
from textbook_builder.pipeline_contracts import PageContinuityResultDTO, PageEvidenceBundleDTO


class PageContinuityChecker:
    """Conservative, advisory-only continuity check for the current page order."""

    def __init__(self, *, review_threshold: float = 0.62) -> None:
        self.review_threshold = review_threshold

    def check(
        self,
        pages: list[PageEvidenceBundleDTO] | tuple[PageEvidenceBundleDTO, ...],
    ) -> list[PageContinuityResultDTO]:
        return [self._compare(previous, current) for previous, current in zip(pages, pages[1:])]

    def _compare(
        self,
        previous: PageEvidenceBundleDTO,
        current: PageEvidenceBundleDTO,
    ) -> PageContinuityResultDTO:
        score = 0.72
        signals: list[str] = []
        severe = False

        if current.page_index == previous.page_index + 1:
            score += 0.08
            signals.append("file_page_index_continuous")
        else:
            score -= 0.42
            signals.append("file_page_index_gap")
            severe = True

        previous_printed = _page_number(previous.printed_page_number)
        current_printed = _page_number(current.printed_page_number)
        if previous_printed is not None and current_printed is not None:
            if current_printed == previous_printed + 1:
                score += 0.12
                signals.append("printed_page_number_continuous")
            elif current_printed == previous_printed:
                score -= 0.12
                signals.append("printed_page_number_repeated")
            else:
                score -= 0.34
                signals.append("printed_page_number_gap_or_reverse")
                severe = True

        previous_text = _normalize_text(previous.combined_text())
        current_text = _normalize_text(current.combined_text())
        if previous.image_sha256 and previous.image_sha256 == current.image_sha256:
            score -= 0.58
            signals.append("duplicate_page_image")
            severe = True
        elif previous_text and current_text and previous_text == current_text:
            score -= 0.46
            signals.append("duplicate_page_text")
            severe = True

        if not previous_text or not current_text:
            score -= 0.18
            signals.append("text_evidence_missing")
        else:
            previous_tail = previous_text[-180:]
            current_head = current_text[:180]
            lexical_overlap = _character_ngram_overlap(previous_tail, current_head)
            if lexical_overlap >= 0.08:
                score += 0.05
                signals.append("boundary_vocabulary_related")
            if _ends_mid_sentence(previous_tail):
                if _starts_like_continuation(current_head):
                    score += 0.08
                    signals.append("sentence_boundary_continuous")
                else:
                    score -= 0.12
                    signals.append("possible_broken_sentence")
            if _starts_with_heading(current_head):
                signals.append("next_page_starts_with_heading")
                score += 0.02

        score = round(max(0.0, min(1.0, score)), 3)
        needs_review = severe or score < self.review_threshold
        return PageContinuityResultDTO(
            previous_page_id=previous.page_id,
            next_page_id=current.page_id,
            previous_page_index=previous.page_index,
            next_page_index=current.page_index,
            continuity_score=score,
            signals=signals,
            needs_model_review=needs_review,
            model_review_status="pending" if needs_review else "not_requested",
            model_reasoning="",
            suggested_action="manual_review" if needs_review else "keep_order",
            user_confirmation="pending",
        )


class PageContinuityModelReviewer:
    """Reviews only suspicious adjacent-page pairs using cached text evidence."""

    def __init__(self, client: ChatJsonClient) -> None:
        self._client = client

    def review(
        self,
        results: list[PageContinuityResultDTO],
        pages: list[PageEvidenceBundleDTO] | tuple[PageEvidenceBundleDTO, ...],
    ) -> list[PageContinuityResultDTO]:
        page_by_id = {page.page_id: page for page in pages}
        reviewed: list[PageContinuityResultDTO] = []
        for result in results:
            if not result.needs_model_review:
                reviewed.append(result)
                continue
            previous = page_by_id[result.previous_page_id]
            current = page_by_id[result.next_page_id]
            try:
                raw = self._client.complete_json(
                    system_prompt=(
                        "你是教材页序复核助手。仅判断给定的相邻页面按当前顺序是否连贯。"
                        "不要重排页面，只输出JSON。证据不足时必须要求人工复核。"
                    ),
                    user_prompt=json.dumps(
                        {
                            "task": "review_adjacent_textbook_page_continuity",
                            "output_schema": {
                                "is_continuous": "boolean|null",
                                "confidence": "0..1",
                                "reason": "string",
                                "suggested_action": "keep_order|manual_review",
                            },
                            "previous_page": {
                                "index": previous.page_index,
                                "printed_number": previous.printed_page_number,
                                "text_tail": previous.combined_text()[-700:],
                            },
                            "next_page": {
                                "index": current.page_index,
                                "printed_number": current.printed_page_number,
                                "text_head": current.combined_text()[:700],
                            },
                            "rule_signals": result.signals,
                        },
                        ensure_ascii=False,
                    ),
                )
                payload = LlmCandidatePayloadParser._load_payload(raw)
                if not isinstance(payload, dict):
                    raise ValueError("连续性复核响应不是JSON对象。")
                is_continuous = payload.get("is_continuous")
                action = str(payload.get("suggested_action") or "manual_review")
                if is_continuous is True and action == "keep_order":
                    status = "continuous"
                elif is_continuous is False:
                    status = "suspicious"
                    action = "manual_review"
                else:
                    status = "uncertain"
                    action = "manual_review"
                reviewed.append(
                    replace(
                        result,
                        model_review_status=status,
                        model_reasoning=str(payload.get("reason") or ""),
                        suggested_action=action,
                    )
                )
            except Exception as exc:
                reviewed.append(
                    replace(
                        result,
                        model_review_status="failed",
                        model_reasoning=f"模型复核失败：{type(exc).__name__}: {exc}",
                        suggested_action="manual_review",
                    )
                )
        return reviewed


def _page_number(value: str) -> int | None:
    match = re.search(r"\d{1,4}", value or "")
    return int(match.group()) if match else None


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _character_ngram_overlap(left: str, right: str, width: int = 2) -> float:
    if len(left) < width or len(right) < width:
        return 0.0
    left_ngrams = {left[index : index + width] for index in range(len(left) - width + 1)}
    right_ngrams = {right[index : index + width] for index in range(len(right) - width + 1)}
    union = left_ngrams | right_ngrams
    return len(left_ngrams & right_ngrams) / len(union) if union else 0.0


def _ends_mid_sentence(value: str) -> bool:
    stripped = value.rstrip()
    return bool(stripped) and stripped[-1] not in "。！？；：.!?;:）】』”\""


def _starts_like_continuation(value: str) -> bool:
    stripped = value.lstrip()
    if not stripped:
        return False
    if _starts_with_heading(stripped):
        return False
    return not bool(re.match(r"^(第[一二三四五六七八九十百\d]+[章节单元]|\d+(?:\.\d+)*\s+)", stripped))


def _starts_with_heading(value: str) -> bool:
    first_line = (value.splitlines() or [value])[0].strip()
    return bool(
        re.match(
            r"^(第[一二三四五六七八九十百\d]+[章节单元]|\d+(?:\.\d+){0,3}\s*[^，。；]{1,24})",
            first_line,
        )
    )
