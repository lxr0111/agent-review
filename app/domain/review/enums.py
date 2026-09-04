from enum import StrEnum


class ReviewStatus(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"


class WebhookEventStatus(StrEnum):
    ACCEPTED = "accepted"
    IGNORED = "ignored"


class FileChangeStatus(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    REMOVED = "removed"
    RENAMED = "renamed"
    COPIED = "copied"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


class FileSkipReason(StrEnum):
    REMOVED = "removed"
    EXCLUDED_PATH = "excluded_path"
    MISSING_PATCH = "missing_patch"
    TOO_LARGE = "too_large"
    TOTAL_BUDGET_EXCEEDED = "total_budget_exceeded"
