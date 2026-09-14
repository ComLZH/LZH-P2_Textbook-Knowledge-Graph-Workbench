from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol
from uuid import uuid4


PROGRESS_MODE_OFF = "off"
PROGRESS_MODE_SUMMARY = "summary"
PROGRESS_MODE_FULL = "full"
PROGRESS_SCHEMA_VERSION = 1

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "secret",
    "password",
)
_DATA_URL_PATTERN = re.compile(
    r"data:(?:image|application)/[^;\s]+;base64,[A-Za-z0-9+/=]+",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|apikey|authorization|access[_-]?token|refresh[_-]?token|secret|password)[\"']?\s*[:=]\s*[\"']?)([^\"'\s,}\]]+)",
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


class ProgressRecorder(Protocol):
    mode: str
    run_id: str
    run_dir: Path | None

    @property
    def enabled(self) -> bool:
        ...

    def record_event(
        self,
        stage: str,
        event: str,
        payload: object | None = None,
        *,
        level: str = "info",
    ) -> None:
        ...

    def record_json(
        self,
        relative_path: str | Path,
        payload: object,
        *,
        detail: str = PROGRESS_MODE_SUMMARY,
    ) -> Path | None:
        ...

    def record_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        detail: str = PROGRESS_MODE_FULL,
    ) -> Path | None:
        ...

    def finish(self, status: str, summary: object | None = None) -> None:
        ...

    def next_model_call_id(self) -> str:
        ...


@dataclass(slots=True)
class NullProgressRecorder:
    mode: str = PROGRESS_MODE_OFF
    run_id: str = ""
    run_dir: Path | None = None

    @property
    def enabled(self) -> bool:
        return False

    def record_event(
        self,
        stage: str,
        event: str,
        payload: object | None = None,
        *,
        level: str = "info",
    ) -> None:
        return None

    def record_json(
        self,
        relative_path: str | Path,
        payload: object,
        *,
        detail: str = PROGRESS_MODE_SUMMARY,
    ) -> Path | None:
        return None

    def record_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        detail: str = PROGRESS_MODE_FULL,
    ) -> Path | None:
        return None

    def finish(self, status: str, summary: object | None = None) -> None:
        return None

    def next_model_call_id(self) -> str:
        return "call_0000"


class FileProgressRecorder:
    def __init__(
        self,
        root_dir: str | Path,
        *,
        mode: str = PROGRESS_MODE_FULL,
        run_id: str = "",
        started_at: datetime | None = None,
    ) -> None:
        normalized_mode = normalize_progress_mode(mode)
        if normalized_mode == PROGRESS_MODE_OFF:
            raise ValueError("FileProgressRecorder cannot use off mode.")
        current_time = (started_at or datetime.now().astimezone()).astimezone()
        self.mode = normalized_mode
        self.run_id = run_id or f"P2-{uuid4().hex}"
        self.started_at = current_time
        self._lock = threading.RLock()
        self._finished = False
        self._model_call_index = 0
        root = Path(root_dir)
        date_dir = root / current_time.date().isoformat()
        base_name = f"{current_time.strftime('%H%M%S')}_{self.run_id}"
        self.run_dir = _unique_directory(date_dir / base_name)
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self._events_path = self.run_dir / "events.jsonl"
        self.record_json(
            "00_run/recording_manifest.json",
            {
                "schema_version": PROGRESS_SCHEMA_VERSION,
                "run_id": self.run_id,
                "mode": self.mode,
                "started_at": current_time.isoformat(timespec="seconds"),
                "status": "recording",
            },
        )
        self.record_event("run", "recording_started", {"mode": self.mode})

    @property
    def enabled(self) -> bool:
        return True

    def record_event(
        self,
        stage: str,
        event: str,
        payload: object | None = None,
        *,
        level: str = "info",
    ) -> None:
        entry = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "stage": str(stage or "unknown"),
            "event": str(event or "event"),
            "level": str(level or "info"),
            "payload": _sanitize(payload or {}),
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._lock:
            self._events_path.parent.mkdir(parents=True, exist_ok=True)
            with self._events_path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def record_json(
        self,
        relative_path: str | Path,
        payload: object,
        *,
        detail: str = PROGRESS_MODE_SUMMARY,
    ) -> Path | None:
        if not self._should_record(detail):
            return None
        target = self._target(relative_path)
        content = json.dumps(_sanitize(payload), ensure_ascii=False, indent=2)
        _atomic_write_text(target, content + "\n", lock=self._lock)
        return target

    def record_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        detail: str = PROGRESS_MODE_FULL,
    ) -> Path | None:
        if not self._should_record(detail):
            return None
        target = self._target(relative_path)
        safe_text = _redact_text(str(text or ""))
        _atomic_write_text(target, safe_text, lock=self._lock)
        return target

    def finish(self, status: str, summary: object | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        completed_at = datetime.now().astimezone()
        payload = {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "run_id": self.run_id,
            "mode": self.mode,
            "status": status,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "completed_at": completed_at.isoformat(timespec="seconds"),
            "duration_seconds": round(
                (completed_at - self.started_at).total_seconds(),
                3,
            ),
            "summary": summary or {},
        }
        self.record_json("00_run/final_status.json", payload)
        self.record_event(
            "run",
            "recording_finished",
            {"status": status, "summary": summary or {}},
            level="error" if status == "failed" else "info",
        )

    def next_model_call_id(self) -> str:
        with self._lock:
            self._model_call_index += 1
            return f"call_{self._model_call_index:04d}"

    def _should_record(self, detail: str) -> bool:
        normalized_detail = normalize_progress_mode(detail)
        if normalized_detail == PROGRESS_MODE_OFF:
            return False
        if self.mode == PROGRESS_MODE_FULL:
            return True
        return normalized_detail == PROGRESS_MODE_SUMMARY

    def _target(self, relative_path: str | Path) -> Path:
        if self.run_dir is None:
            raise RuntimeError("Progress recording directory is unavailable.")
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Progress artifact path must stay inside the run directory.")
        target = (self.run_dir / relative).resolve()
        target.relative_to(self.run_dir.resolve())
        return target


class RecordingChatJsonClient:
    """Records model I/O while preserving the ChatJsonClient contract."""

    def __init__(
        self,
        delegate: object,
        recorder: ProgressRecorder,
        *,
        model_metadata: dict[str, object] | None = None,
    ) -> None:
        self._delegate = delegate
        self._recorder = recorder
        self._model_metadata = dict(model_metadata or {})

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        method = getattr(self._delegate, "complete_json")
        return self._record_call(
            method=method,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_paths=[],
        )

    def complete_json_with_images(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_paths: list[str | Path],
    ) -> str:
        method = getattr(self._delegate, "complete_json_with_images", None)
        if not callable(method):
            raise AttributeError("Wrapped model client does not support image input.")
        return self._record_call(
            method=method,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_paths=image_paths,
        )

    def _record_call(
        self,
        *,
        method: object,
        system_prompt: str,
        user_prompt: str,
        image_paths: list[str | Path],
    ) -> str:
        try:
            call_id = self._recorder.next_model_call_id()
        except Exception:
            call_id = f"call_{uuid4().hex[:8]}"
        stage = _detect_model_stage(system_prompt, user_prompt)
        started = perf_counter()
        request_payload = {
            "call_id": call_id,
            "stage": stage,
            "model": self._model_metadata,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "images": [_image_reference(path) for path in image_paths],
        }
        _safe_recorder_call(
            self._recorder.record_event,
            stage,
            "model_call_started",
            {
                "call_id": call_id,
                "prompt_characters": len(system_prompt) + len(user_prompt),
                "image_count": len(image_paths),
                "model": self._model_metadata,
            },
        )
        try:
            if image_paths:
                response = method(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    image_paths=image_paths,
                )
            else:
                response = method(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
        except Exception as exc:
            duration = round(perf_counter() - started, 3)
            _safe_recorder_call(
                self._recorder.record_json,
                f"model_calls/{call_id}_{stage}/request.json",
                request_payload,
                detail=PROGRESS_MODE_FULL,
            )
            _safe_recorder_call(
                self._recorder.record_json,
                f"model_calls/{call_id}_{stage}/result.json",
                {
                    "call_id": call_id,
                    "stage": stage,
                    "status": "failed",
                    "duration_seconds": duration,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            _safe_recorder_call(
                self._recorder.record_event,
                stage,
                "model_call_failed",
                {
                    "call_id": call_id,
                    "duration_seconds": duration,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                level="error",
            )
            raise
        duration = round(perf_counter() - started, 3)
        response_text = str(response or "")
        call_metadata = getattr(self._delegate, "last_call_metadata", {})
        _safe_recorder_call(
            self._recorder.record_json,
            f"model_calls/{call_id}_{stage}/request.json",
            request_payload,
            detail=PROGRESS_MODE_FULL,
        )
        _safe_recorder_call(
            self._recorder.record_text,
            f"model_calls/{call_id}_{stage}/response_raw.txt",
            response_text,
            detail=PROGRESS_MODE_FULL,
        )
        _safe_recorder_call(
            self._recorder.record_json,
            f"model_calls/{call_id}_{stage}/result.json",
            {
                "call_id": call_id,
                "stage": stage,
                "status": "completed",
                "duration_seconds": duration,
                "prompt_characters": len(system_prompt) + len(user_prompt),
                "response_characters": len(response_text),
                "image_count": len(image_paths),
                "provider_metadata": call_metadata,
            },
        )
        _safe_recorder_call(
            self._recorder.record_event,
            stage,
            "model_call_completed",
            {
                "call_id": call_id,
                "duration_seconds": duration,
                "response_characters": len(response_text),
                "provider_metadata": call_metadata,
            },
        )
        return response_text


def _safe_recorder_call(method: object, *args: object, **kwargs: object) -> object | None:
    """Keep optional development recording failures out of the business pipeline."""
    try:
        return method(*args, **kwargs)  # type: ignore[operator]
    except Exception:
        return None


def create_progress_recorder(
    root_dir: str | Path,
    *,
    mode: str,
    run_id: str = "",
) -> ProgressRecorder:
    normalized_mode = normalize_progress_mode(mode)
    if normalized_mode == PROGRESS_MODE_OFF:
        return NullProgressRecorder()
    return FileProgressRecorder(root_dir, mode=normalized_mode, run_id=run_id)


def normalize_progress_mode(value: object) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "": PROGRESS_MODE_OFF,
        "0": PROGRESS_MODE_OFF,
        "false": PROGRESS_MODE_OFF,
        "disabled": PROGRESS_MODE_OFF,
        "关闭": PROGRESS_MODE_OFF,
        "1": PROGRESS_MODE_FULL,
        "true": PROGRESS_MODE_FULL,
        "enabled": PROGRESS_MODE_FULL,
        "完整": PROGRESS_MODE_FULL,
        "完整记录": PROGRESS_MODE_FULL,
        "摘要": PROGRESS_MODE_SUMMARY,
        "摘要记录": PROGRESS_MODE_SUMMARY,
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {PROGRESS_MODE_OFF, PROGRESS_MODE_SUMMARY, PROGRESS_MODE_FULL}:
        return PROGRESS_MODE_OFF
    return normalized


def write_progress_artifact(
    run_dir: str | Path | None,
    relative_path: str | Path,
    payload: object,
    *,
    detail: str = PROGRESS_MODE_SUMMARY,
) -> Path | None:
    if not run_dir:
        return None
    root = Path(run_dir)
    if not root.is_dir():
        return None
    recording_mode = _recording_mode_for_directory(root)
    normalized_detail = normalize_progress_mode(detail)
    if recording_mode == PROGRESS_MODE_OFF:
        return None
    if recording_mode != PROGRESS_MODE_FULL and normalized_detail == PROGRESS_MODE_FULL:
        return None
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Progress artifact path must stay inside the run directory.")
    target = (root / relative).resolve()
    target.relative_to(root.resolve())
    content = json.dumps(_sanitize(payload), ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(target, content)
    event = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "stage": "post_analysis",
        "event": "artifact_appended",
        "level": "info",
        "payload": {"path": str(relative).replace("\\", "/")},
    }
    with (root / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return target


def _recording_mode_for_directory(root: Path) -> str:
    manifest_path = root / "00_run" / "recording_manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PROGRESS_MODE_OFF
    if not isinstance(payload, dict):
        return PROGRESS_MODE_OFF
    return normalize_progress_mode(payload.get("mode"))


def _detect_model_stage(system_prompt: str, user_prompt: str) -> str:
    task = ""
    try:
        payload = json.loads(user_prompt)
        if isinstance(payload, dict):
            task = str(payload.get("task") or "")
    except (TypeError, json.JSONDecodeError):
        task = ""
    task_map = {
        "extract_independent_nodes_and_local_relation_claims": "03_first_pass",
        "consolidate_relations_without_creating_nodes": "05_relation_completion",
        "review_adjacent_textbook_page_continuity": "02_page_order_review",
        "advise_textbook_chapter_internal_layout": "06_layout_advice",
    }
    if task in task_map:
        return task_map[task]
    combined = f"{system_prompt}\n{user_prompt}".lower()
    if "关系整合" in combined or "consolidate_relations" in combined:
        return "05_relation_completion"
    if "章节内部结构" in combined or "layout" in combined:
        return "06_layout_advice"
    if "页序" in combined or "continuity" in combined:
        return "02_page_order_review"
    if "节点候选" in combined or "knowledge_points" in combined:
        return "03_first_pass"
    return "model_call"


def _image_reference(raw_path: str | Path) -> dict[str, object]:
    path = Path(raw_path)
    payload: dict[str, object] = {"path": str(path)}
    if not path.is_file():
        payload["exists"] = False
        return payload
    payload.update(
        {
            "exists": True,
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
    )
    return payload


def _sanitize(value: object) -> object:
    return _redact(_json_safe(value))


def _json_safe(value: object) -> object:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _redact(value: object) -> object:
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            normalized_key = str(key).lower().replace("-", "_")
            if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(value: str) -> str:
    redacted = _DATA_URL_PATTERN.sub("[REDACTED_DATA_URL]", value)
    redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", redacted)
    return _SENSITIVE_ASSIGNMENT_PATTERN.sub(r"\1[REDACTED]", redacted)


def _atomic_write_text(
    target: Path,
    content: str,
    *,
    lock: threading.RLock | None = None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    context = lock or _NoopLock()
    with context:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)


def _unique_directory(candidate: Path) -> Path:
    if not candidate.exists():
        return candidate
    for index in range(2, 10_000):
        alternate = candidate.with_name(f"{candidate.name}_{index}")
        if not alternate.exists():
            return alternate
    raise RuntimeError("Unable to allocate a unique progress recording directory.")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _NoopLock:
    def __enter__(self) -> "_NoopLock":
        return self

    def __exit__(self, *_args: object) -> None:
        return None
