# schemas/models.py
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, StrictFloat, StrictStr

# 1. What the Git Driver sends to the backend
class ConflictInput(BaseModel):
    file_path: str
    ancestor_path: str
    current_path: str
    other_path: str

# 2. What the AST Parser (Member A) outputs
class ASTReport(BaseModel):
    conflict_type: str  # "trivial", "semantic", "syntax_error", "unsupported"
    changed_functions: List[str]
    overlapping_nodes: bool

# 3. What the LLM / AI Agent (Member B) outputs
class MergePatch(BaseModel):
    target_function: str
    new_code: str
    rationale: str
    confidence: float


class PatchOperation(BaseModel):
    """One constrained source edit requested by the merge agent."""

    operation: Literal["replace_node", "update_import", "insert_helper"]
    target_function: Optional[StrictStr] = None
    target_import: Optional[StrictStr] = None
    new_code: StrictStr

    class Config:
        extra = "forbid"


class LLMPatch(BaseModel):
    """Validated structured response expected from the local merge model."""

    target_function: StrictStr
    patch_operations: List[PatchOperation] = Field(default_factory=list)
    new_code: StrictStr
    rationale: StrictStr
    confidence: StrictFloat = Field(ge=0.0, le=1.0)

    class Config:
        extra = "forbid"


class ContextBundle(BaseModel):
    """Bounded source context supplied to an LLM for a single conflict."""

    file_path: str
    language: str
    target_names: List[str]
    conflicting_code: str
    dependencies: List[str] = Field(default_factory=list)
    imports: List[str] = Field(default_factory=list)
    related_tests: List[str] = Field(default_factory=list)
    truncated: bool = False

# 4. What the Docker Verifier (Member C) outputs
class VerificationReport(BaseModel):
    syntax_valid: bool
    tests_passed: bool
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    error: Optional[str] = None


class WorkflowState(BaseModel):
    """Mutable state carried between semantic merge workflow stages."""

    stage: str
    context: Optional[ContextBundle] = None
    patch: Optional[LLMPatch] = None
    verification: Optional[VerificationReport] = None
    security_report: Optional["SecurityReport"] = None
    feedback: str = ""
    retries_used: int = 0
    max_retries: int = 3
    resolved_code: Optional[str] = None
    failure_reason: Optional[str] = None


class SecurityFinding(BaseModel):
    """One normalized result returned by the static security scanner."""

    rule_id: str
    severity: str
    category: str
    message: str
    file_path: str
    line: Optional[int] = None


class SecurityReport(BaseModel):
    """Security scan decision and its findings."""

    status: Literal["pass", "warning", "critical", "block"]
    score: int = Field(ge=0, le=100)
    findings: List[SecurityFinding] = Field(default_factory=list)
    error: Optional[str] = None
    requires_human_review: bool = False

# 5. The final combined result sent to the UI (Member D)
class FinalMergeResult(BaseModel):
    success: bool
    resolved_code: Optional[str] = None
    ast_report: Optional[ASTReport] = None
    patch: Optional[LLMPatch] = None
    verification: Optional[VerificationReport] = None
    security_report: Optional[SecurityReport] = None
    retries_used: int = 0
    failure_reason: Optional[str] = None
