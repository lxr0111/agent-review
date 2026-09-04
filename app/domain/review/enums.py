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
