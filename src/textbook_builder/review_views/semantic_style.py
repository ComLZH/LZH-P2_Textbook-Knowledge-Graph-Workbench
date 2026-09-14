from __future__ import annotations

from dataclasses import asdict, dataclass

from ..review_document import RELATION_FAMILY_UNRESOLVED
from ..utils.graph_semantics import normalize_relation_type


@dataclass(frozen=True, slots=True)
class RelationDisplaySpec:
    key: str
    label: str
    stroke: str
    line_width: float = 2.0
    dash: tuple[int, ...] = ()
    arrow_mode: str = "end"
    directional: bool = True
    semantics_pending: bool = False

    def as_payload(self) -> dict[str, object]:
        value = asdict(self)
        value["dash"] = list(self.dash)
        return value


_KNOWN: dict[str, RelationDisplaySpec] = {
    "prerequisite": RelationDisplaySpec("prerequisite", "前置", "#1d4ed8", 4.0),
    "progressive": RelationDisplaySpec("progressive", "递进", "#0f766e", 4.0),
    "derives_to": RelationDisplaySpec("derives_to", "推导", "#7c3aed", 3.0),
    "explains": RelationDisplaySpec("explains", "解释", "#64748b", 2.0, (6, 6)),
    "equivalent": RelationDisplaySpec("equivalent", "等价", "#475467", 2.0, (), "both", False),
    "parallel": RelationDisplaySpec("parallel", "并列", "#7c3aed", 2.0, (8, 6), "none", False),
    "contrast": RelationDisplaySpec("contrast", "对比", "#b42318", 2.0, (8, 6), "both", False),
    "applies_to": RelationDisplaySpec("applies_to", "应用", "#b45309", 2.0, (8, 5)),
    "represented_by": RelationDisplaySpec("represented_by", "表征", "#0f766e", 2.0, (10, 4, 2, 4)),
}

_UNRESOLVED_CONTAINS = RelationDisplaySpec(
    "unresolved_contains",
    "包含·含义待确认",
    "#b45309",
    2.2,
    (4, 5),
    "end",
    True,
    True,
)

_UNKNOWN = RelationDisplaySpec(
    "unknown",
    "关系类型待确认",
    "#64748b",
    2.0,
    (3, 5),
    "end",
    True,
    True,
)


def relation_display_spec(
    relation_family: str,
    relation_type: str,
) -> RelationDisplaySpec:
    raw = str(relation_type or "").strip()
    normalized = normalize_relation_type(raw) if raw else ""
    if relation_family == RELATION_FAMILY_UNRESOLVED and normalized == "contains":
        return _UNRESOLVED_CONTAINS
    return _KNOWN.get(normalized) or RelationDisplaySpec(
        key="unknown",
        label=f"未知关系〔{raw}〕" if raw else _UNKNOWN.label,
        stroke=_UNKNOWN.stroke,
        line_width=_UNKNOWN.line_width,
        dash=_UNKNOWN.dash,
        arrow_mode=_UNKNOWN.arrow_mode,
        directional=_UNKNOWN.directional,
        semantics_pending=True,
    )


def relation_style_registry_payload() -> dict[str, dict[str, object]]:
    values = dict(_KNOWN)
    values[_UNRESOLVED_CONTAINS.key] = _UNRESOLVED_CONTAINS
    values[_UNKNOWN.key] = _UNKNOWN
    return {key: value.as_payload() for key, value in values.items()}


__all__ = [
    "RelationDisplaySpec",
    "relation_display_spec",
    "relation_style_registry_payload",
]
