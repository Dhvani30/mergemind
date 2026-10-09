"""Tests for three-way AST node comparison and conflict classification."""

from tools.ast_diff import classify_conflict, diff_nodes


def node(kind: str, name: str, code: str) -> dict[str, object]:
    """Build a minimal parser node for tests."""
    return {"type": kind, "name": name, "code": code, "start_line": 1, "end_line": 2}


def test_diff_nodes_reports_added_modified_and_deleted_nodes() -> None:
    base = [node("function_definition", "kept", "def kept(): pass"),
            node("class_definition", "Removed", "class Removed: pass"),
            node("function_definition", "edited", "def edited(): return 1")]
    branch = [node("function_definition", "kept", "def kept(): pass"),
              node("function_definition", "edited", "def edited(): return 2"),
              node("function_definition", "new", "def new(): pass")]

    diff = diff_nodes(base, branch)

    assert [n["name"] for n in diff["added"]] == ["new"]
    assert [n["name"] for n in diff["modified"]] == ["edited"]
    assert [n["name"] for n in diff["deleted"]] == ["Removed"]


def test_disjoint_function_changes_are_trivial() -> None:
    base = [node("function_definition", "alpha", "def alpha(): return 0"),
            node("function_definition", "beta", "def beta(): return 0")]
    current = [node("function_definition", "alpha", "def alpha(): return 1"), base[1]]
    other = [base[0], node("function_definition", "beta", "def beta(): return 2")]

    report = classify_conflict(base, current, other)

    assert report.conflict_type == "trivial"
    assert report.changed_functions == ["alpha", "beta"]
    assert report.overlapping_nodes is False


def test_same_function_changes_are_semantic() -> None:
    base = [node("function_definition", "work", "def work(): return 0")]
    current = [node("function_definition", "work", "def work(): return 1")]
    other = [node("function_definition", "work", "def work(): return 2")]

    report = classify_conflict(base, current, other)

    assert report.conflict_type == "semantic"
    assert report.changed_functions == ["work"]
    assert report.overlapping_nodes is True


def test_additions_and_parser_statuses_are_classified() -> None:
    base = []
    current = [node("function_definition", "left", "def left(): pass")]
    other = [node("function_definition", "right", "def right(): pass")]

    assert classify_conflict(base, current, other).conflict_type == "trivial"
    assert classify_conflict(base, current, other, syntax_error=True).conflict_type == "syntax_error"
    assert classify_conflict(base, current, other, unsupported=True).conflict_type == "unsupported"
