import pytest

from app.domain.review.entities import ChangedFile, FileSelection
from app.domain.review.enums import FileChangeStatus, FileSkipReason
from app.domain.review.file_filter import (
    FileFilterPolicy,
    apply_total_patch_budget,
    select_reviewable_files,
)


def _file(
    path: str,
    *,
    status: FileChangeStatus = FileChangeStatus.MODIFIED,
    changes: int = 1,
    patch: str | None = "@@ -1 +1 @@\n-old\n+new",
) -> ChangedFile:
    return ChangedFile(
        path=path,
        status=status,
        additions=1,
        deletions=0,
        changes=changes,
        patch=patch,
    )


def test_select_reviewable_files_keeps_supported_text_changes() -> None:
    python_file = _file("src/service.py")
    renamed_file = _file("src/new_name.py", status=FileChangeStatus.RENAMED)

    result = select_reviewable_files((python_file, renamed_file))

    assert result.reviewable == (python_file, renamed_file)
    assert result.skipped == ()


def test_select_reviewable_files_records_specific_skip_reasons() -> None:
    removed = _file("src/legacy.py", status=FileChangeStatus.REMOVED, patch=None)
    lock_file = _file("frontend/package-lock.json")
    missing_patch = _file("assets/logo.png", patch=None)
    large_file = _file("src/generated.py", changes=11)

    result = select_reviewable_files(
        (removed, lock_file, missing_patch, large_file),
        FileFilterPolicy(max_changes=10),
    )

    assert result.reviewable == ()
    assert tuple(item.reason for item in result.skipped) == (
        FileSkipReason.REMOVED,
        FileSkipReason.EXCLUDED_PATH,
        FileSkipReason.MISSING_PATCH,
        FileSkipReason.TOO_LARGE,
    )


def test_select_reviewable_files_matches_exclusions_case_insensitively() -> None:
    result = select_reviewable_files(
        (
            _file("Web/YARN.LOCK"),
            _file("public/BUNDLE.MIN.JS"),
        )
    )

    assert tuple(item.reason for item in result.skipped) == (
        FileSkipReason.EXCLUDED_PATH,
        FileSkipReason.EXCLUDED_PATH,
    )


def test_select_reviewable_files_applies_patch_character_budget() -> None:
    large_patch = _file("src/service.py", patch="x" * 11)

    result = select_reviewable_files(
        (large_patch,),
        FileFilterPolicy(max_patch_characters=10),
    )

    assert result.reviewable == ()
    assert result.skipped[0].reason == FileSkipReason.TOO_LARGE


def test_apply_total_patch_budget_keeps_files_that_fit() -> None:
    first = _file("src/first.py", patch="1234")
    second = _file("src/second.py", patch="5678")

    result = apply_total_patch_budget(
        FileSelection(reviewable=(first, second), skipped=()),
        max_patch_characters=4,
    )

    assert result.reviewable == (first,)
    assert result.skipped[0].file is second
    assert result.skipped[0].reason == FileSkipReason.TOTAL_BUDGET_EXCEEDED


def test_apply_total_patch_budget_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="positive"):
        apply_total_patch_budget(FileSelection(reviewable=(), skipped=()), 0)
