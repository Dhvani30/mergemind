"""Run local Semgrep rules and turn findings into merge safety decisions."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

from schemas.models import SecurityFinding, SecurityReport

RULES_PATH = Path(__file__).resolve().parents[1] / "security" / "rules.yaml"


class SecurityScanError(RuntimeError):
    """Raised when Semgrep is unavailable or its output cannot be interpreted."""


def scan_code(
    code: str,
    *,
    file_name: str = "merged.py",
    rules_path: str | Path = RULES_PATH,
    timeout_seconds: int = 90,
    semgrep_executable: str | None = None,
) -> SecurityReport:
    """Scan candidate code using local Semgrep rules and classify its risk.

    Scanner failures return ``block`` with ``requires_human_review=True``. A
    scanner outage is never represented as a clean pass.
    """
    executable = semgrep_executable or os.getenv("SEMGREP_BIN") or shutil.which("semgrep")
    if not executable:
        return SecurityReport(
            status="block", score=0, findings=[], error="Semgrep is unavailable; install it with `python -m pip install semgrep`",
            requires_human_review=True,
        )
    rules = Path(rules_path)
    if not rules.is_file():
        return SecurityReport(status="block", score=0, error=f"Semgrep rules file not found: {rules}",
                              requires_human_review=True)
    if Path(file_name).name != file_name or not file_name.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".java")):
        return SecurityReport(status="block", score=0, error="file_name must be a supported source filename",
                              requires_human_review=True)

    try:
        with tempfile.TemporaryDirectory(prefix="mergemind-semgrep-") as temp_dir:
            target = Path(temp_dir) / file_name
            target.write_text(code, encoding="utf-8")
            command = [executable, "scan", "--config", str(rules), "--json", "--metrics=off", str(target)]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds,
                                       check=False, shell=False)
            if completed.returncode not in (0, 1):
                details = (completed.stderr or completed.stdout or "Semgrep failed without diagnostic output")
                return SecurityReport(status="block", score=0, error=details[-8_000:],
                                      requires_human_review=True)
            result = json.loads(completed.stdout or "{}")
    except subprocess.TimeoutExpired:
        return SecurityReport(status="block", score=0, error=f"Semgrep timed out after {timeout_seconds} seconds",
                              requires_human_review=True)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return SecurityReport(status="block", score=0, error=f"Semgrep could not complete: {exc}",
                              requires_human_review=True)

    findings: list[SecurityFinding] = []
    for item in result.get("results", []):
        extra = item.get("extra", {})
        metadata = extra.get("metadata", {})
        severity = str(extra.get("severity", metadata.get("severity", "WARNING"))).upper()
        findings.append(SecurityFinding(
            rule_id=str(item.get("check_id", "unknown")), severity=severity,
            category=str(metadata.get("category", "security")),
            message=str(extra.get("message", "Security rule matched")),
            file_path=file_name, line=item.get("start", {}).get("line"),
        ))

    severities = {finding.severity for finding in findings}
    if severities & {"CRITICAL", "ERROR"}:
        status, score = "critical", 0
    elif "HIGH" in severities:
        status, score = "block", 20
    elif findings:
        status, score = "warning", max(35, 85 - 10 * len(findings))
    else:
        status, score = "pass", 100
    return SecurityReport(status=status, score=score, findings=findings,
                          requires_human_review=status in {"critical", "block"})


def scan_file(path: str | Path, *, rules_path: str | Path = RULES_PATH) -> SecurityReport:
    """Read a UTF-8 source file and scan its contents."""
    file_path = Path(path)
    try:
        code = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return SecurityReport(status="block", score=0, error=f"Cannot read source for security scan: {exc}",
                              requires_human_review=True)
    return scan_code(code, file_name=file_path.name, rules_path=rules_path)
