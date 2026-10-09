"""Safely apply constrained Python source edits returned by the merge agent."""

from __future__ import annotations

import ast
import os
import stat
import tempfile
from pathlib import Path

from schemas.models import LLMPatch, PatchOperation


class PatchApplicationError(ValueError):
    """Raised when a generated patch cannot be applied unambiguously."""


def _parse_module(source: str, label: str) -> ast.Module:
    """Parse Python and provide a useful patch validation error."""
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        raise PatchApplicationError(f"{label} is not valid Python: {exc}") from exc


def _line_offsets(source: str) -> list[int]:
    """Map one-based line numbers to character offsets."""
    offsets = [0]
    for index, char in enumerate(source):
        if char == "\n":
            offsets.append(index + 1)
    return offsets


def _node_span(node: ast.AST, offsets: list[int], source_length: int) -> tuple[int, int]:
    """Convert an AST node's line range to a half-open character range."""
    start_line = getattr(node, "lineno", None)
    decorators = getattr(node, "decorator_list", ())
    if decorators:
        start_line = min(start_line, *(decorator.lineno for decorator in decorators))
    end_line = getattr(node, "end_lineno", None)
    if not start_line or not end_line or start_line > len(offsets):
        raise PatchApplicationError("The target node has no usable source location")
    start = offsets[start_line - 1]
    end = offsets[end_line] if end_line < len(offsets) else source_length
    return start, end


def _named_nodes(module: ast.Module, name: str) -> list[ast.AST]:
    """Find top-level function or class definitions with the requested name."""
    found: list[ast.AST] = []
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            found.append(node)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            found.append(node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            found.append(node)
    return found


def _validated_node_code(code: str, operation: str) -> str:
    """Validate replacement/helper source and return normalized trailing newline."""
    module = _parse_module(code, f"{operation} code")
    if len(module.body) != 1 or not isinstance(module.body[0], (
        ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign
    )):
        raise PatchApplicationError(f"{operation} must contain one function, class, or simple assignment")
    return code.strip("\r\n") + "\n"


def apply_patch(source: str, patch: LLMPatch) -> str:
    """Apply a validated patch to Python source without executing generated code."""
    module = _parse_module(source, "Original source")
    operations = patch.patch_operations or [PatchOperation(
        operation="replace_node", target_function=patch.target_function, new_code=patch.new_code
    )]
    offsets = _line_offsets(source)
    replacements: list[tuple[int, int, str]] = []
    additions: list[str] = []
    import_additions: list[str] = []
    import_nodes = [node for node in module.body if isinstance(node, (ast.Import, ast.ImportFrom))]

    for operation in operations:
        if operation.operation == "replace_node":
            target = operation.target_function or patch.target_function
            matches = _named_nodes(module, target)
            if len(matches) != 1:
                raise PatchApplicationError(f"Expected one top-level target named {target!r}; found {len(matches)}")
            start, end = _node_span(matches[0], offsets, len(source))
            replacements.append((start, end, _validated_node_code(operation.new_code, "Replacement")))
        elif operation.operation == "insert_helper":
            additions.append(_validated_node_code(operation.new_code, "Helper"))
        elif operation.operation == "update_import":
            import_module = _parse_module(operation.new_code, "Import")
            if len(import_module.body) != 1 or not isinstance(import_module.body[0], (ast.Import, ast.ImportFrom)):
                raise PatchApplicationError("update_import must contain exactly one import statement")
            new_import = operation.new_code.strip()
            if operation.target_import:
                target_text = " ".join(operation.target_import.split())
                matches = [node for node in import_nodes
                           if " ".join((ast.get_source_segment(source, node) or "").split()) == target_text]
                if len(matches) != 1:
                    raise PatchApplicationError(
                        f"Expected one import matching {operation.target_import!r}; found {len(matches)}"
                    )
                start, end = _node_span(matches[0], offsets, len(source))
                replacements.append((start, end, new_import + ("\n" if end < len(source) or source.endswith("\n") else "")))
            elif new_import not in {ast.get_source_segment(source, node) for node in import_nodes}:
                import_additions.append(new_import)

    replacements.sort(key=lambda item: item[0], reverse=True)
    previous_start = len(source) + 1
    for start, end, replacement in replacements:
        if end > previous_start:
            raise PatchApplicationError("Patch contains overlapping replacement ranges")
        source = source[:start] + replacement + source[end:]
        previous_start = start
    insertions = "\n".join(dict.fromkeys(import_additions + additions))
    if insertions:
        if source and not source.endswith("\n"):
            source += "\n"
        source += ("\n" if source else "") + insertions
    _parse_module(source, "Patched source")
    return source


def apply_patch_file(file_path: str | Path, patch: LLMPatch) -> str:
    """Apply a patch and atomically replace the requested UTF-8 source file."""
    path = Path(file_path)
    if path.suffix.lower() != ".py":
        raise PatchApplicationError("Patch application currently supports Python files only")
    original = path.read_text(encoding="utf-8")
    updated = apply_patch(original, patch)
    file_mode = stat.S_IMODE(path.stat().st_mode)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as temporary:
            temporary.write(updated)
            temporary_path = temporary.name
        os.chmod(temporary_path, file_mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return updated
