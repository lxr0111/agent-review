import re
from dataclasses import dataclass

_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?: .*)?$"
)


@dataclass(frozen=True, slots=True)
class UnifiedDiffLineMap:
    """Validated new-file coordinates derived from one GitHub patch."""

    annotated_patch: str
    added_lines: frozenset[int]

    def contains_added_range(self, start_line: int, end_line: int) -> bool:
        if start_line < 1 or end_line < start_line:
            return False
        return all(line in self.added_lines for line in range(start_line, end_line + 1))


def parse_unified_diff(patch: str | None) -> UnifiedDiffLineMap | None:
    """Parse a complete unified diff patch or return ``None`` when it is unsafe to map."""

    if patch is None or not patch.strip():
        return None

    annotated: list[str] = []
    added_lines: set[int] = set()
    in_hunk = False
    old_line = 0
    new_line = 0
    old_remaining = 0
    new_remaining = 0

    for line in patch.splitlines():
        header = _HUNK_HEADER.fullmatch(line)
        if header is not None:
            if in_hunk and (old_remaining != 0 or new_remaining != 0):
                return None
            old_line = int(header.group("old_start"))
            new_line = int(header.group("new_start"))
            old_remaining = _hunk_count(header.group("old_count"))
            new_remaining = _hunk_count(header.group("new_count"))
            in_hunk = True
            annotated.append(line)
            continue

        if not in_hunk:
            return None
        if line.startswith("\\ No newline at end of file"):
            annotated.append("[meta] \\ No newline at end of file")
            continue
        if not line:
            return None

        prefix = line[0]
        if prefix == " ":
            if old_remaining < 1 or new_remaining < 1:
                return None
            annotated.append(f"[old={old_line},new={new_line}] {line}")
            old_line += 1
            new_line += 1
            old_remaining -= 1
            new_remaining -= 1
        elif prefix == "-":
            if old_remaining < 1:
                return None
            annotated.append(f"[old={old_line},new=-] {line}")
            old_line += 1
            old_remaining -= 1
        elif prefix == "+":
            if new_remaining < 1:
                return None
            annotated.append(f"[old=-,new={new_line}] {line}")
            added_lines.add(new_line)
            new_line += 1
            new_remaining -= 1
        else:
            return None

    if not in_hunk or old_remaining != 0 or new_remaining != 0:
        return None
    return UnifiedDiffLineMap(
        annotated_patch="\n".join(annotated),
        added_lines=frozenset(added_lines),
    )


def _hunk_count(value: str | None) -> int:
    return 1 if value is None else int(value)
