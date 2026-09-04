from dataclasses import dataclass

from app.domain.review.entities import ChangedFile, FileSelection, SkippedFile
from app.domain.review.enums import FileChangeStatus, FileSkipReason

DEFAULT_EXCLUDED_FILENAMES = frozenset(
    {
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "package-lock.json",
        "pipfile.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    }
)
DEFAULT_EXCLUDED_SUFFIXES = (
    ".generated.cs",
    ".generated.go",
    ".min.css",
    ".min.js",
    ".pb.go",
    ".pyc",
    ".source.map",
)


@dataclass(frozen=True, slots=True)
class FileFilterPolicy:
    max_changes: int = 2_000
    max_patch_characters: int = 100_000
    excluded_filenames: frozenset[str] = DEFAULT_EXCLUDED_FILENAMES
    excluded_suffixes: tuple[str, ...] = DEFAULT_EXCLUDED_SUFFIXES


def select_reviewable_files(
    files: tuple[ChangedFile, ...],
    policy: FileFilterPolicy | None = None,
) -> FileSelection:
    active_policy = policy or FileFilterPolicy()
    reviewable: list[ChangedFile] = []
    skipped: list[SkippedFile] = []

    for changed_file in files:
        reason = _get_skip_reason(changed_file, active_policy)
        if reason is None:
            reviewable.append(changed_file)
        else:
            skipped.append(SkippedFile(file=changed_file, reason=reason))

    return FileSelection(reviewable=tuple(reviewable), skipped=tuple(skipped))


def apply_total_patch_budget(
    selection: FileSelection,
    max_patch_characters: int,
) -> FileSelection:
    if max_patch_characters < 1:
        raise ValueError("Total patch character budget must be positive")

    total_characters = 0
    reviewable: list[ChangedFile] = []
    skipped = list(selection.skipped)
    for changed_file in selection.reviewable:
        patch_characters = len(changed_file.patch or "")
        if total_characters + patch_characters > max_patch_characters:
            skipped.append(
                SkippedFile(
                    file=changed_file,
                    reason=FileSkipReason.TOTAL_BUDGET_EXCEEDED,
                )
            )
            continue
        reviewable.append(changed_file)
        total_characters += patch_characters

    return FileSelection(reviewable=tuple(reviewable), skipped=tuple(skipped))


def _get_skip_reason(
    changed_file: ChangedFile,
    policy: FileFilterPolicy,
) -> FileSkipReason | None:
    if changed_file.status == FileChangeStatus.REMOVED:
        return FileSkipReason.REMOVED

    normalized_path = changed_file.path.casefold()
    filename = normalized_path.rsplit("/", maxsplit=1)[-1]
    if filename in policy.excluded_filenames or normalized_path.endswith(policy.excluded_suffixes):
        return FileSkipReason.EXCLUDED_PATH

    if not changed_file.patch:
        return FileSkipReason.MISSING_PATCH

    if (
        changed_file.changes > policy.max_changes
        or len(changed_file.patch) > policy.max_patch_characters
    ):
        return FileSkipReason.TOO_LARGE

    return None
