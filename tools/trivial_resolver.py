"""Apply non-overlapping Python AST changes from one merge branch."""

from __future__ import annotations

import ast
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.ast_diff import _node_key, diff_nodes
from tools.ast_parser import parse_python_source

ASTNode = Mapping[str, Any]


class TrivialMergeError(ValueError):
    """Raised when a trivial merge cannot be applied safely."""


def _newline(source: str) -> str:
    """Choose the source's newline convention."""
    return "\r\n" if "\r\n" in source else "\n"


def _import_insert_index(source: str, nodes: Sequence[ASTNode]) -> int:
    """Find an appropriate line to add imports without moving a file header."""
    imports = [node for node in nodes if str(node.get("type", "")).startswith("import")]
    if imports:
        return max(int(node["end_line"]) for node in imports)
    try:
        module = ast.parse(source)
    except SyntaxError as exc:
        raise TrivialMergeError(f"Current source is invalid Python: {exc}") from exc
    if (module.body and isinstance(module.body[0], ast.Expr)
            and isinstance(module.body[0].value, ast.Constant)
            and isinstance(module.body[0].value.value, str)):
        return module.body[0].end_lineno or module.body[0].lineno
    prefix_lines = source.splitlines()
    index = 0
    while index < len(prefix_lines) and (not prefix_lines[index].strip() or prefix_lines[index].lstrip().startswith("#")):
        index += 1
    return index


def merge_trivial(
    base_content: str,
    current_content: str,
    other_content: str,
    base_nodes: Sequence[ASTNode] | None = None,
    current_nodes: Sequence[ASTNode] | None = None,
    other_nodes: Sequence[ASTNode] | None = None,
) -> str:
    """Merge disjoint top-level Python edits into the current branch content.

    ``base_nodes``, ``current_nodes`` and ``other_nodes`` may be supplied by
    the triage step. If omitted, they are parsed directly from the source.
    Overlapping AST identities are rejected; this function never guesses at
    semantic conflicts.
    """
    try:
        base_nodes = list(base_nodes) if base_nodes is not None else parse_python_source(base_content)
        current_nodes = list(current_nodes) if current_nodes is not None else parse_python_source(current_content)
        other_nodes = list(other_nodes) if other_nodes is not None else parse_python_source(other_content)
    except SyntaxError as exc:
        raise TrivialMergeError(f"Cannot merge invalid Python source: {exc}") from exc

    current_diff = diff_nodes(base_nodes, current_nodes)
    other_diff = diff_nodes(base_nodes, other_nodes)
    for nodes in (base_nodes, current_nodes, other_nodes):
        keys = [_node_key(node) for node in nodes]
        if len(keys) != len(set(keys)):
            raise TrivialMergeError("Duplicate top-level AST identities are ambiguous to merge")
    current_changed = {_node_key(node) for action in current_diff.values() for node in action}
    other_changed = {_node_key(node) for action in other_diff.values() for node in action}
    overlap = current_changed & other_changed
    if overlap:
        raise TrivialMergeError("AST changes overlap; semantic merge required")

    lines = current_content.splitlines(keepends=True)
    newline = _newline(current_content or base_content)
    current_by_key = {_node_key(node): node for node in current_nodes}
    incoming_changes: list[tuple[ASTNode | None, ASTNode | None]] = []

    for node in other_diff["deleted"]:
        incoming_changes.append((current_by_key.get(_node_key(node)), None))
    for node in other_diff["modified"]:
        incoming_changes.append((current_by_key.get(_node_key(node)), node))
    for node in other_diff["added"]:
        incoming_changes.append((None, node))

    replacements: list[tuple[int, int, str | None, ASTNode]] = []
    imports_to_add: list[str] = []
    appended_definitions: list[str] = []
    for current_node, incoming_node in incoming_changes:
        if incoming_node is None:
            if current_node is not None:
                replacements.append((int(current_node["start_line"]), int(current_node["end_line"]), None, current_node))
            continue
        kind = str(incoming_node.get("type", ""))
        code = str(incoming_node.get("code", ""))
        if kind.startswith("import"):
            existing_imports = {
                " ".join(str(node.get("code", "")).split())
                for node in current_nodes if str(node.get("type", "")).startswith("import")
            }
            if " ".join(code.split()) not in existing_imports:
                imports_to_add.append(code)
        elif current_node is not None:
            replacements.append((int(current_node["start_line"]), int(current_node["end_line"]), code, current_node))
        else:
            appended_definitions.append(code)

    import_insert_at: int | None = None
    import_addition_lines: list[str] = []
    if imports_to_add:
        unique_imports = list(dict.fromkeys(imports_to_add))
        import_insert_at = _import_insert_index(current_content, current_nodes)
        for code in unique_imports:
            import_addition_lines.extend(code.splitlines())
        import_addition_lines = [line + newline for line in import_addition_lines]
        if min(import_insert_at, len(lines)) > 0 and lines[min(import_insert_at, len(lines)) - 1].strip():
            import_addition_lines.insert(0, newline)

    # Walk the original current lines once so insertions and replacements keep
    # referring to the same original line coordinates.
    replacement_by_start: dict[int, tuple[int, str | None]] = {}
    for start_line, end_line, replacement, _target in replacements:
        start_index = start_line - 1
        if start_index in replacement_by_start:
            raise TrivialMergeError("Patch contains multiple edits for one source node")
        replacement_by_start[start_index] = (end_line, replacement)
    output_lines: list[str] = []
    index = 0
    while index <= len(lines):
        if import_insert_at is not None and index == min(import_insert_at, len(lines)):
            output_lines.extend(import_addition_lines)
        if index == len(lines):
            break
        edit = replacement_by_start.get(index)
        if edit is None:
            output_lines.append(lines[index])
            index += 1
            continue
        end_line, replacement = edit
        block = lines[index:end_line]
        trailing_newline = bool(block and block[-1].endswith(("\n", "\r")))
        if replacement is not None:
            replacement_lines = replacement.splitlines()
            if trailing_newline and replacement_lines:
                replacement_lines[-1] += newline
            replacement_lines[:-1] = [line.rstrip("\r\n") + newline for line in replacement_lines[:-1]]
            output_lines.extend(replacement_lines)
        index = end_line

    merged = "".join(output_lines)
    if appended_definitions:
        if merged and not merged.endswith(("\n", "\r")):
            merged += newline
        for code in dict.fromkeys(appended_definitions):
            if merged and not merged.endswith(newline * 2):
                merged += newline
            merged += code.rstrip("\r\n") + newline
    try:
        ast.parse(merged)
    except SyntaxError as exc:
        raise TrivialMergeError(f"Merged result is invalid Python: {exc}") from exc
    return merged


def resolve_trivial_conflict(
    base_content: str,
    current_content: str,
    other_content: str,
    base_nodes: Sequence[ASTNode] | None = None,
    current_nodes: Sequence[ASTNode] | None = None,
    other_nodes: Sequence[ASTNode] | None = None,
) -> str:
    """Compatibility-named wrapper returning the merged source text."""
    return merge_trivial(base_content, current_content, other_content,
                         base_nodes, current_nodes, other_nodes)


def write_trivial_merge(
    base_path: str | Path,
    current_path: str | Path,
    other_path: str | Path,
) -> str:
    """Read a three-way conflict, merge it, and atomically write current_path."""
    base_file, current_file, other_file = Path(base_path), Path(current_path), Path(other_path)
    if any(path.suffix.lower() != ".py" for path in (base_file, current_file, other_file)):
        raise TrivialMergeError("Trivial AST resolution currently supports Python files only")
    merged = merge_trivial(
        base_file.read_text(encoding="utf-8"), current_file.read_text(encoding="utf-8"),
        other_file.read_text(encoding="utf-8"),
    )
    file_mode = stat.S_IMODE(current_file.stat().st_mode)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=current_file.parent,
                                         prefix=f".{current_file.name}.", suffix=".tmp", delete=False) as tmp:
            tmp.write(merged)
            temporary_name = tmp.name
        os.chmod(temporary_name, file_mode)
        os.replace(temporary_name, current_file)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return merged
