"""Extract top-level Python merge units with source locations."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


class UnsupportedLanguageError(ValueError):
    """Raised when no parser is available for the source file's language."""


def parse_python_source(source_code: str, *, filename: str = "<source>") -> list[dict[str, Any]]:
    """Parse Python source and return top-level functions, classes, and imports.

    Python's standard AST is used here so parsing and syntax-error detection do
    not depend on an optional native Tree-sitter wheel at runtime.
    """
    # Use Tree-sitter's Python grammar when installed, then use CPython's AST
    # for stable source locations and named-node extraction.
    tree_sitter_tree = None
    try:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        tree_sitter_tree = Parser(Language(tspython.language())).parse(source_code.encode("utf-8"))
    except ImportError:
        # The standard library parser below remains a working fallback when
        # optional native grammar wheels have not yet been installed.
        tree_sitter_tree = None
    if tree_sitter_tree is not None and tree_sitter_tree.root_node.has_error:
        raise SyntaxError(f"Tree-sitter reported invalid syntax in {filename}")
    tree = ast.parse(source_code, filename=filename)
    lines = source_code.splitlines()
    output: list[dict[str, Any]] = []
    for child in tree.body:
        if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant) and isinstance(child.value.value, str):
            continue
        supported = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                       ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign))
        start_line = child.lineno
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.decorator_list:
            start_line = min(start_line, *(decorator.lineno for decorator in child.decorator_list))
        end_line = child.end_lineno or start_line
        if isinstance(child, ast.Assign) and len(child.targets) == 1 and isinstance(child.targets[0], ast.Name):
            kind, name = "assignment_statement", child.targets[0].id
        elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            kind, name = "assignment_statement", child.target.id
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind, name = "function_definition", child.name
        elif isinstance(child, ast.ClassDef):
            kind, name = "class_definition", child.name
        elif isinstance(child, ast.ImportFrom):
            kind, name = "import_from_statement", None
        elif isinstance(child, ast.Import):
            kind, name = "import_statement", None
        else:
            kind, name = "unsupported_statement", None
        item: dict[str, Any] = {
            "type": kind,
            "start_line": start_line,
            "end_line": end_line,
            "code": "\n".join(lines[start_line - 1:end_line]),
        }
        if name:
            item["name"] = name
        output.append(item)
    occupied_lines: set[int] = set()
    for item in output:
        span = set(range(item["start_line"], item["end_line"] + 1))
        if occupied_lines & span:
            output.append({"type": "unsupported_statement", "start_line": item["start_line"],
                           "end_line": item["end_line"], "code": "<multiple statements share source lines>"})
            break
        occupied_lines.update(span)
    return output


def parse_python_file(file_path: str | Path) -> list[dict[str, Any]]:
    """Read and parse a Python file, returning extracted top-level nodes."""
    path = Path(file_path)
    return parse_python_source(path.read_text(encoding="utf-8"), filename=str(path))


def parse_file(file_path: str | Path) -> list[dict[str, Any]]:
    """Parse a supported source file, raising for unsupported extensions."""
    path = Path(file_path)
    if path.suffix.lower() != ".py":
        raise UnsupportedLanguageError(f"No parser is configured for {path.suffix or 'extensionless'} files")
    return parse_python_file(path)


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m tools.ast_parser <path_to_python_file>")
    print(json.dumps(parse_file(sys.argv[1]), indent=2))
