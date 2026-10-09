"""Build bounded, relevant source context for a merge conflict."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

from schemas.models import ContextBundle


def _segment(source: str, node: ast.AST) -> str:
    """Return the exact source text represented by an AST node."""
    decorators = getattr(node, "decorator_list", ())
    if decorators:
        lines = source.splitlines()
        start_line = min(decorator.lineno for decorator in decorators)
        end_line = getattr(node, "end_lineno", node.lineno)
        return "\n".join(lines[start_line - 1:end_line])
    return ast.get_source_segment(source, node) or ""


def _names_used(node: ast.AST) -> set[str]:
    """Collect referenced identifiers inside a syntax node."""
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _find_tests(source_path: Path, target_names: set[str], roots: Iterable[Path], limit: int) -> list[str]:
    """Find small Python test files that mention one of the changed names."""
    candidates: set[Path] = set()
    for root in roots:
        search_root = root if root.is_dir() else root.parent
        if search_root.exists():
            candidates.update(
                path for path in search_root.rglob("test*.py")
                if path.is_file() and not any(part in {".git", "venv", ".venv", "node_modules", "__pycache__"}
                                               for part in path.parts)
            )
    if source_path.parent.exists():
        candidates.update(
            path for path in source_path.parent.rglob("test*.py")
            if path.is_file() and not any(part in {".git", "venv", ".venv", "node_modules", "__pycache__"}
                                           for part in path.parts)
        )

    matches: list[str] = []
    for path in sorted(candidates):
        try:
            if path.stat().st_size > 256_000:
                continue
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if any(name in content for name in target_names):
            matches.append(f"# {path}\n{content}")
            if len(matches) >= limit:
                break
    return matches


def build_context(
    file_path: str | Path,
    target_names: Iterable[str],
    *,
    test_roots: Iterable[str | Path] = (),
    max_chars: int = 24_000,
    max_test_files: int = 3,
) -> ContextBundle:
    """Extract changed definitions, one-hop dependencies, imports and tests.

    Python is parsed with the standard library AST. ``max_chars`` bounds the
    total serialized source material returned to the model; truncation proceeds
    in priority order: conflict, dependencies, imports, then tests.
    """
    path = Path(file_path)
    if path.suffix.lower() != ".py":
        raise ValueError(f"Context extraction does not support {path.suffix or 'this file type'}")
    if max_chars <= 0 or max_test_files < 0:
        raise ValueError("max_chars must be positive and max_test_files cannot be negative")
    source = path.read_text(encoding="utf-8")
    try:
        module = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise ValueError(f"Cannot extract context from invalid Python: {exc}") from exc

    names = list(dict.fromkeys(name for name in target_names if name))
    if not names:
        raise ValueError("At least one target function or class name is required")
    wanted = set(names)
    definitions: dict[str, ast.AST] = {}
    imports: list[str] = []
    for statement in module.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions[statement.name] = statement
        elif isinstance(statement, (ast.Import, ast.ImportFrom)):
            text = _segment(source, statement)
            if text:
                imports.append(text)

    missing = wanted - definitions.keys()
    if missing:
        raise ValueError(f"Target definitions not found: {', '.join(sorted(missing))}")
    conflicts = [_segment(source, definitions[name]) for name in names]
    used = set().union(*(_names_used(definitions[name]) for name in names)) - wanted
    dependency_names = sorted(used & definitions.keys())
    dependencies = [_segment(source, definitions[name]) for name in dependency_names]
    tests = _find_tests(path, wanted, (Path(root) for root in test_roots), max_test_files)

    # Budget each section without splitting source lines/code blocks.
    remaining = max_chars

    def take(items: list[str]) -> tuple[list[str], bool]:
        nonlocal remaining
        kept: list[str] = []
        truncated = False
        for item in items:
            separator_cost = 2 if kept else 0
            room = remaining - separator_cost
            if room <= 0:
                truncated = True
                break
            if len(item) > room:
                kept.append(item[:room])
                remaining -= room + separator_cost
                truncated = True
                break
            kept.append(item)
            remaining -= len(item) + separator_cost
        return kept, truncated or len(kept) < len(items)

    kept_conflicts, truncated_conflicts = take(conflicts)
    kept_dependencies, truncated_dependencies = take(dependencies)
    kept_imports, truncated_imports = take(imports)
    kept_tests, truncated_tests = take(tests)
    return ContextBundle(
        file_path=str(path), language="python", target_names=names,
        conflicting_code="\n\n".join(kept_conflicts), dependencies=kept_dependencies,
        imports=kept_imports, related_tests=kept_tests,
        truncated=any((truncated_conflicts, truncated_dependencies, truncated_imports, truncated_tests)),
    )
