"""Bounded generate, apply, verify, retry, and security workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agents.merge_agent import MergeAgentError, generate_patch
from schemas.models import ContextBundle, LLMPatch, SecurityReport, VerificationReport, WorkflowState
from tools.docker_verifier import DockerVerifier
from tools.patch_applier import PatchApplicationError, apply_patch

Verifier = Callable[..., VerificationReport]
SecurityScanner = Callable[..., SecurityReport]


@dataclass
class WorkflowResult:
    """Final artifacts produced by a semantic merge attempt."""

    state: WorkflowState
    resolved_code: str | None


def generate_patch_node(state: WorkflowState, *, client: Any | None, model: str) -> WorkflowState:
    """Ask the LLM for a patch, including the latest verification feedback."""
    state.stage = "generate_patch"
    if state.context is None:
        state.failure_reason = "Merge workflow has no extracted context"
        state.stage = "final"
        return state
    try:
        state.patch = generate_patch(state.context, model=model, feedback=state.feedback or None, client=client)
    except MergeAgentError as exc:
        state.failure_reason = str(exc)
        state.stage = "final"
    return state


def validate_patch_node(state: WorkflowState, *, source: str) -> WorkflowState:
    """Apply generated edits in memory; invalid edits move to retry feedback."""
    state.stage = "validate_patch"
    if state.patch is None:
        state.feedback = state.failure_reason or "The model did not produce a patch."
        return state
    try:
        state.resolved_code = apply_patch(source, state.patch)
        state.failure_reason = None
    except (PatchApplicationError, ValueError) as exc:
        state.resolved_code = None
        state.feedback = f"Generated patch was rejected: {exc}"
    return state


def verify_node(
    state: WorkflowState,
    *,
    verifier: Verifier,
    patched_file: str,
    project_root: str,
    relative_file: str,
    test_files: list[str] | None,
) -> WorkflowState:
    """Execute syntax and project tests for the current candidate."""
    state.stage = "verify"
    if state.resolved_code is None:
        return state
    try:
        state.verification = verifier(
            patched_file, patched_content=state.resolved_code,
            project_root=project_root, test_files=test_files, relative_file=relative_file,
        )
    except Exception as exc:
        state.verification = VerificationReport(
            syntax_valid=False, tests_passed=False, stdout="", stderr=str(exc),
            exit_code=125, error=f"Verification stage failed: {exc}",
        )
    if state.verification.error or not state.verification.tests_passed:
        state.feedback = (
            "The candidate did not pass isolated verification.\n"
            f"Syntax valid: {state.verification.syntax_valid}\n"
            f"Exit code: {state.verification.exit_code}\n"
            f"STDOUT:\n{state.verification.stdout[-12_000:]}\n"
            f"STDERR:\n{state.verification.stderr[-12_000:]}"
        )
        state.resolved_code = None
    return state


def security_node(state: WorkflowState, *, scanner: SecurityScanner, file_name: str) -> WorkflowState:
    """Scan a test-passing candidate and reject critical/blocking outcomes."""
    state.stage = "security"
    if state.resolved_code is None:
        return state
    try:
        state.security_report = scanner(state.resolved_code, file_name=file_name)
    except Exception as exc:
        state.security_report = SecurityReport(status="block", score=0,
                                               error=f"Security stage failed: {exc}",
                                               requires_human_review=True)
    if state.security_report.status in {"critical", "block"}:
        state.failure_reason = (
            "Security review blocked the candidate" +
            (f": {state.security_report.error}" if state.security_report.error else "")
        )
        state.resolved_code = None
    return state


def run_semantic_workflow(
    *,
    source: str,
    context: ContextBundle,
    patched_file: str,
    project_root: str,
    relative_file: str,
    file_name: str,
    test_files: list[str] | None = None,
    model: str = "qwen2.5-coder:3b",
    client: Any | None = None,
    verifier: Verifier | None = None,
    scanner: SecurityScanner | None = None,
    max_retries: int = 3,
) -> WorkflowResult:
    """Run generation and checks, allowing at most ``max_retries`` corrections.

    Initial generation plus at most ``max_retries`` corrected generations are
    attempted. Parse or verification failures feed bounded logs back to Ollama.
    Scanner findings with warning severity are returned for review; ``critical``
    and ``block`` decisions prevent the candidate from being written.
    """
    if not 0 <= max_retries <= 3:
        raise ValueError("max_retries must be between 0 and 3")
    verifier = verifier or DockerVerifier().verify_file
    if scanner is None:
        from agents.security_agent import scan_code
        scanner = scan_code
    state = WorkflowState(stage="context", context=context, max_retries=max_retries)
    for attempt in range(max_retries + 1):
        state.failure_reason = None
        state = generate_patch_node(state, client=client, model=model)
        if state.stage == "final":
            break
        state = validate_patch_node(state, source=source)
        if state.resolved_code is None:
            if attempt < max_retries:
                state.retries_used += 1
                continue
            state.failure_reason = state.feedback
            break
        state = verify_node(state, verifier=verifier, patched_file=patched_file,
                            project_root=project_root, relative_file=relative_file,
                            test_files=test_files)
        if state.resolved_code is None:
            if attempt < max_retries:
                state.retries_used += 1
                continue
            state.failure_reason = state.feedback or "Verification failed after all retry attempts"
            break
        state = security_node(state, scanner=scanner, file_name=file_name)
        state.stage = "final"
        break
    if state.stage != "final":
        state.stage = "final"
    return WorkflowResult(state=state, resolved_code=state.resolved_code)
