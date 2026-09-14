from __future__ import annotations

from ..contracts import ALLOWED_REVIEW_STATUSES, REVIEW_STATUS_PENDING


def normalize_review_status(raw_value: object, default: str = REVIEW_STATUS_PENDING) -> str:
    value = str(raw_value or "").strip()
    legacy_map = {
        "confirmed": "accepted",
        "approve": "accepted",
        "approved": "accepted",
        "accept": "accepted",
        "deny": "rejected",
        "denied": "rejected",
        "reject": "rejected",
        "revision": "needs_revision",
        "needs_review": "needs_revision",
        "expert_review": "needs_expert_review",
    }
    if value in ALLOWED_REVIEW_STATUSES:
        return value
    return legacy_map.get(value, default)
