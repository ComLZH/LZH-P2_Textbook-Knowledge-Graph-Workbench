from __future__ import annotations

from dataclasses import dataclass, field

from ..review_document import (
    P2ReviewDocumentDTO,
    RELATION_FAMILY_MEMBERSHIP,
    RELATION_FAMILY_UNRESOLVED,
)
from ..review_views.projection import ReviewProjectionBuilder
from ..utils.review_status import normalize_review_status


@dataclass(frozen=True, slots=True)
class ReviewDocumentQualityIssue:
    dimension: str
    severity: str
    code: str
    message: str
    object_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class ReviewDocumentQualityReport:
    issues: list[ReviewDocumentQualityIssue] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.issues)

    def by_dimension(self) -> dict[str, list[ReviewDocumentQualityIssue]]:
        result: dict[str, list[ReviewDocumentQualityIssue]] = {}
        for issue in self.issues:
            result.setdefault(issue.dimension, []).append(issue)
        return result


class ReviewDocumentQualityChecker:
    """Checks independent structure, membership, semantic and display dimensions."""

    def check(self, document: P2ReviewDocumentDTO) -> ReviewDocumentQualityReport:
        issues: list[ReviewDocumentQualityIssue] = []
        for error in document.validate():
            dimension = (
                "structure"
                if error.startswith("structure_") or error.startswith("scope_")
                else "membership"
                if error.startswith("membership_") or error.startswith("occurrence_")
                else "integrity"
            )
            issues.append(
                ReviewDocumentQualityIssue(
                    dimension,
                    "error",
                    error.split(":", 1)[0],
                    "建设态引用或层级不完整。",
                    tuple(error.split(":")),
                )
            )
        scope_ids = {
            scope.scope_id for scope in document.scopes if scope.lifecycle_state == "active"
        }
        active_nodes = [node for node in document.nodes if node.lifecycle_state == "active"]
        memberships: dict[str, list[object]] = {}
        for relation in document.active_relations():
            status = normalize_review_status(relation.review_status)
            if relation.relation_family == RELATION_FAMILY_MEMBERSHIP and status != "rejected":
                memberships.setdefault(relation.target_node_id, []).append(relation)
            elif relation.relation_family == RELATION_FAMILY_UNRESOLVED and status != "rejected":
                issues.append(
                    ReviewDocumentQualityIssue(
                        "semantic",
                        "warning",
                        "unresolved_legacy_relation",
                        "知识对象之间的旧 contains 尚未完成语义裁决。",
                        (relation.relation_id,),
                    )
                )
        for node in active_nodes:
            if node.node_id in scope_ids:
                continue
            candidates = memberships.get(node.node_id, [])
            if not candidates:
                issues.append(
                    ReviewDocumentQualityIssue(
                        "membership",
                        "warning",
                        "membership_missing",
                        "知识对象没有有效教材归属。",
                        (node.node_id,),
                    )
                )
            elif len(candidates) > 1:
                issues.append(
                    ReviewDocumentQualityIssue(
                        "membership",
                        "warning",
                        "multiple_memberships",
                        "知识对象具有多个教材归属；建设态允许，P4 v1 发布需选主归属。",
                        (node.node_id, *(item.relation_id for item in candidates)),
                    )
                )
        projection = ReviewProjectionBuilder().build(document)
        covered_ids = {
            item.relation_id for item in projection.representation_ledger if item.relation_id
        }
        expected_ids = {relation.relation_id for relation in document.active_relations()}
        missing = sorted(expected_ids - covered_ids)
        if missing:
            issues.append(
                ReviewDocumentQualityIssue(
                    "displayability",
                    "error",
                    "relation_representation_missing",
                    "有业务关系未进入共享视图覆盖账本。",
                    tuple(missing),
                )
            )
        for projection_issue in projection.issues:
            issues.append(
                ReviewDocumentQualityIssue(
                    "displayability",
                    "warning",
                    projection_issue.code,
                    projection_issue.message,
                    tuple(projection_issue.object_ids),
                )
            )
        return ReviewDocumentQualityReport(issues)


__all__ = [
    "ReviewDocumentQualityChecker",
    "ReviewDocumentQualityIssue",
    "ReviewDocumentQualityReport",
]
