from app.domain.review.unified_diff import parse_unified_diff


def test_parse_unified_diff_maps_added_lines_across_hunks() -> None:
    patch = """@@ -2,3 +2,4 @@
 keep
-old
+new
+another
 tail
@@ -10,0 +12,2 @@
+first
+second"""

    result = parse_unified_diff(patch)

    assert result is not None
    assert result.added_lines == frozenset({3, 4, 12, 13})
    assert result.contains_added_range(3, 4) is True
    assert result.contains_added_range(12, 13) is True
    assert result.contains_added_range(4, 12) is False
    assert "[old=-,new=3] +new" in result.annotated_patch
    assert "[old=3,new=-] -old" in result.annotated_patch
    assert "[old=4,new=5]  tail" in result.annotated_patch


def test_parse_unified_diff_accepts_no_newline_marker() -> None:
    result = parse_unified_diff("@@ -1 +1 @@\n-old\n+new\n\\ No newline at end of file")

    assert result is not None
    assert result.added_lines == frozenset({1})
    assert result.annotated_patch.endswith("[meta] \\ No newline at end of file")


def test_parse_unified_diff_rejects_truncated_hunk() -> None:
    assert parse_unified_diff("@@ -1,2 +1,2 @@\n-old\n+new") is None


def test_parse_unified_diff_rejects_content_outside_hunk() -> None:
    assert parse_unified_diff("untrusted text\n@@ -1 +1 @@\n-old\n+new") is None


def test_parse_unified_diff_rejects_missing_patch() -> None:
    assert parse_unified_diff(None) is None
    assert parse_unified_diff("") is None
