import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_retry_maximum_must_not_be_lower_than_base_delay() -> None:
    with pytest.raises(ValidationError, match="REVIEW_RETRY_MAX_SECONDS"):
        Settings(
            _env_file=None,
            review_retry_base_seconds=30,
            review_retry_max_seconds=10,
        )
