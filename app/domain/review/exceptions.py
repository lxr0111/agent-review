class ReviewContextError(Exception):
    pass


class MissingGitHubInstallation(ReviewContextError):
    pass


class PullRequestIdentityMismatch(ReviewContextError):
    pass


class PullRequestHeadChanged(ReviewContextError):
    def __init__(self, expected_head_sha: str, actual_head_sha: str) -> None:
        super().__init__(f"Pull request head changed from {expected_head_sha} to {actual_head_sha}")
        self.expected_head_sha = expected_head_sha
        self.actual_head_sha = actual_head_sha


class DuplicateChangedFilePath(ReviewContextError):
    pass


class ReviewOutputValidationError(Exception):
    pass
