#!/usr/bin/env python3
"""Git merge-driver entry point for MergeMind's core resolution pipeline."""

from __future__ import annotations

import sys

from schemas.models import ConflictInput
from tools.resolution_pipeline import resolve_conflict


def main(argv: list[str] | None = None) -> int:
    """Run the three-way merge pipeline for Git's O, A, and B file arguments."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3:
        print("Usage: mergemind_driver.py <ancestor> <current> <other> [marker-size] [path]", file=sys.stderr)
        return 2
    ancestor, current, other = args[:3]
    logical_path = args[4] if len(args) >= 5 else current
    print(f"MergeMind: triaging {logical_path}")
    result = resolve_conflict(ConflictInput(
        file_path=logical_path, ancestor_path=ancestor, current_path=current, other_path=other,
    ))
    if result.ast_report is not None:
        print(f"Conflict type: {result.ast_report.conflict_type}")
        if result.ast_report.changed_functions:
            print("Changed definitions: " + ", ".join(result.ast_report.changed_functions))
    if result.success:
        print("MergeMind resolved the conflict.")
        if result.patch:
            print(f"AI rationale: {result.patch.rationale}")
        return 0
    if result.ast_report and result.ast_report.conflict_type == "semantic":
        print("Semantic conflict detected, passing to AI agent...")
    if result.failure_reason:
        print(f"MergeMind could not resolve the conflict: {result.failure_reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
