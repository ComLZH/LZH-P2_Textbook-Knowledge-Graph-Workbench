from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SubjectMetadata:
    subject: str
    grade: str
    term: str
    education_stage: str
    grade_band: str
    subject_tags: tuple[str, ...]


def derive_subject_metadata(subject: str, grade: str, term: str) -> SubjectMetadata:
    normalized_subject = str(subject or "").strip().lower()
    normalized_grade = str(grade or "").strip().lower()
    normalized_term = str(term or "").strip().lower()
    stage = ""
    band = ""
    grade_number = _grade_number(normalized_grade)
    if grade_number is not None:
        if 1 <= grade_number <= 6:
            stage, band = "primary_school", "g1_g6"
        elif 7 <= grade_number <= 9:
            stage, band = "junior_middle_school", "g7_g9"
        elif 10 <= grade_number <= 12:
            stage, band = "senior_high_school", "g10_g12"
    tags: list[str] = []
    if normalized_subject:
        tags.append(normalized_subject)
    return SubjectMetadata(
        subject=normalized_subject,
        grade=normalized_grade,
        term=normalized_term,
        education_stage=stage,
        grade_band=band,
        subject_tags=tuple(tags),
    )


def missing_required_metadata(subject: str, grade: str, term: str) -> tuple[str, ...]:
    values = {"学科": subject, "年级": grade, "册次": term}
    return tuple(label for label, value in values.items() if not str(value or "").strip())


def _grade_number(grade: str) -> int | None:
    value = grade.removeprefix("grade").removeprefix("g")
    try:
        return int(value)
    except ValueError:
        return None


__all__ = ["SubjectMetadata", "derive_subject_metadata", "missing_required_metadata"]
