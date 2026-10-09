"""End-to-end conflict triage and resolution for Python source files."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from agents.workflow import run_semantic_workflow
from schemas.models import ASTReport, ConflictInput, ContextBundle, FinalMergeResult
from tools.ast_diff import classify_conflict
from tools.ast_parser import UnsupportedLanguageError, parse_file
from tools.context_extractor import build_context
from tools.docker_verifier import DockerVerifier
from tools.trivial_resolver import TrivialMergeError, merge_trivial


def _read_source(path: str | Path) -> str:
    """Read UTF-8 source and reject binary content before parsing."""
    data = Path(path).read_bytes()
    if b"\x00" in data:
        raise ValueError(f"Binary file is not supported: {path}")
    return data.decode("utf-8")


def _write_atomic(path: str | Path, content: str) -> None:
    """Atomically replace a source file after successful resolution."""
    target = Path(path)
    file_mode = stat.S_IMODE(target.stat().st_mode)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=target.parent,
                                         prefix=f".{target.name}.", suffix=".tmp", delete=False) as temp:
            temp.write(content)
            temporary_name = temp.name
        os.chmod(temporary_name, file_mode)
        os.replace(temporary_name, target)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _semantic_context(
    current_path: str,
    current_source: str,
    other_source: str,
    current_nodes: list[dict[str, Any]],
    other_nodes: list[dict[str, Any]],
    report: ASTReport,
    max_chars: int,
) -> ContextBundle:
    """Collect target code from both branches and dependency context when available."""
    names = report.changed_functions
    context: ContextBundle | None = None
    if names:
        try:
            context = build_context(current_path, names, max_chars=max_chars)
        except (OSError, UnicodeError, ValueError, SyntaxError):
            context = None

    current_by_name = {node.get("name"): node for node in current_nodes if node.get("name")}
    other_by_name = {node.get("name"): node for node in other_nodes if node.get("name")}
    code_parts: list[str] = []
    for name in names:
        ours = current_by_name.get(name)
        theirs = other_by_name.get(name)
        if ours:
            code_parts.append(f"# CURRENT VERSION: {name}\n{ours['code']}")
        if theirs:
            code_parts.append(f"# INCOMING VERSION: {name}\n{theirs['code']}")
    if not names:
        changed_imports = [node for node in current_nodes + other_nodes
                           if str(node.get("type", "")).startswith("import")]
        code_parts.extend(str(node.get("code", "")) for node in changed_imports)

    if context is None:
        imports = list(dict.fromkeys(
            str(node["code"]) for node in current_nodes + other_nodes
            if str(node.get("type", "")).startswith("import")
        ))
        context = ContextBundle(
            file_path=current_path, language="python", target_names=names,
            conflicting_code="\n\n".join(code_parts)[:max_chars], imports=imports,
        )
    else:
        context.conflicting_code = "\n\n".join(code_parts)[:max_chars]
    return context


def resolve_conflict(
    conflict: ConflictInput,
    *,
    ollama_client: Any | None = None,
    model: str = "qwen2.5-coder:3b",
    max_context_chars: int = 24_000,
    max_retries: int = 3,
    project_root: str | Path | None = None,
    test_files: list[str] | None = None,
    verifier: Any | None = None,
    security_scanner: Any | None = None,
) -> FinalMergeResult:
    """Resolve a three-way conflict automatically when safe, or use the local LLM.

    Only Python files are supported by the parser currently available in this
    repository. A candidate is written only after parsing, isolated verification,
    and a non-blocking security scan have completed successfully.
    """
    try:
        base_source = _read_source(conflict.ancestor_path)
        current_source = _read_source(conflict.current_path)
        other_source = _read_source(conflict.other_path)
        base_nodes = parse_file(conflict.ancestor_path)
        current_nodes = parse_file(conflict.current_path)
        other_nodes = parse_file(conflict.other_path)
    except UnsupportedLanguageError as exc:
        return FinalMergeResult(success=False, ast_report=ASTReport(
            conflict_type="unsupported", changed_functions=[], overlapping_nodes=False), failure_reason=str(exc))
    except SyntaxError as exc:
        return FinalMergeResult(success=False, ast_report=ASTReport(
            conflict_type="syntax_error", changed_functions=[], overlapping_nodes=False), failure_reason=str(exc))
    except (OSError, UnicodeError, ValueError) as exc:
        return FinalMergeResult(success=False, failure_reason=str(exc))

    report = classify_conflict(base_nodes, current_nodes, other_nodes)
    if report.conflict_type == "unsupported":
        return FinalMergeResult(success=False, ast_report=report,
                                failure_reason="This Python file contains top-level structures outside the supported merge subset")
    if report.conflict_type == "trivial":
        try:
            resolved = merge_trivial(base_source, current_source, other_source,
                                     base_nodes, current_nodes, other_nodes)
            verify = verifier or DockerVerifier().verify_file
            root = Path(project_root or Path(conflict.file_path).resolve().parent)
            try:
                verification = verify(conflict.current_path, patched_content=resolved,
                                       project_root=str(root), test_files=test_files,
                                       relative_file=conflict.file_path)
            except Exception as exc:
                return FinalMergeResult(success=False, ast_report=report,
                                        failure_reason=f"Verification stage failed: {exc}")
            if verification.error or not verification.tests_passed:
                return FinalMergeResult(success=False, ast_report=report, verification=verification,
                                        failure_reason=verification.error or "Isolated tests failed")
            from agents.security_agent import scan_code
            scan = security_scanner or scan_code
            try:
                security = scan(resolved, file_name=Path(conflict.file_path).name)
            except Exception as exc:
                return FinalMergeResult(success=False, ast_report=report, verification=verification,
                                        failure_reason=f"Security stage failed: {exc}")
            if security.status in {"critical", "block"}:
                return FinalMergeResult(success=False, ast_report=report, verification=verification,
                                        security_report=security,
                                        failure_reason=security.error or "Security scan blocked the merge")
            _write_atomic(conflict.current_path, resolved)
            return FinalMergeResult(success=True, resolved_code=resolved, ast_report=report,
                                    verification=verification, security_report=security)
        except (TrivialMergeError, OSError) as exc:
            return FinalMergeResult(success=False, ast_report=report, failure_reason=str(exc))

    context = _semantic_context(conflict.current_path, current_source, other_source,
                                current_nodes, other_nodes, report, max_context_chars)
    root = Path(project_root or Path(conflict.file_path).resolve().parent)
    workflow_result = run_semantic_workflow(
        source=current_source, context=context, patched_file=conflict.current_path,
        project_root=str(root), relative_file=conflict.file_path,
        file_name=Path(conflict.file_path).name,
        test_files=test_files, model=model, client=ollama_client,
        verifier=verifier, scanner=security_scanner, max_retries=max_retries,
    )
    state = workflow_result.state
    if workflow_result.resolved_code is None:
        return FinalMergeResult(success=False, ast_report=report, patch=state.patch,
                                verification=state.verification, security_report=state.security_report,
                                retries_used=state.retries_used,
                                failure_reason=state.failure_reason or "Semantic merge was not approved")
    try:
        _write_atomic(conflict.current_path, workflow_result.resolved_code)
    except OSError as exc:
        return FinalMergeResult(success=False, ast_report=report, patch=state.patch,
                                verification=state.verification, security_report=state.security_report,
                                retries_used=state.retries_used, failure_reason=str(exc))
    return FinalMergeResult(success=True, resolved_code=workflow_result.resolved_code,
                            ast_report=report, patch=state.patch, verification=state.verification,
                            security_report=state.security_report, retries_used=state.retries_used)
