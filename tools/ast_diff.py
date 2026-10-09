"""Compare extracted syntax nodes across the three sides of a merge."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from schemas.models import ASTReport

ASTNode = Mapping[str, Any]


def _node_key(node: ASTNode) -> tuple[str, str]:
    """Return a stable identity for a function, class, method, or import node."""
    node_type = str(node.get("type", "unknown"))
    name = node.get("name")
    if name:
        return node_type, str(name)
    # Import nodes are often anonymous in the parser output. Normalize spacing,
    # so harmless formatting changes do not make them look like new imports.
    code = re.sub(r"\s+", " ", str(node.get("code", ""))).strip()
    return node_type, code


def diff_nodes(base_nodes: Sequence[ASTNode], branch_nodes: Sequence[ASTNode]) -> dict[str, list[ASTNode]]:
    """Describe added, modified, and deleted nodes from base to one branch.

    Named syntax nodes are matched by type and name. Anonymous nodes (currently
    imports) are matched by type and normalized source text.
    """
    base = {_node_key(node): node for node in base_nodes}
    branch = {_node_key(node): node for node in branch_nodes}
    added = [node for key, node in branch.items() if key not in base]
    deleted = [node for key, node in base.items() if key not in branch]
    modified = [node for key, node in branch.items()
                if key in base and str(node.get("code", "")).strip() != str(base[key].get("code", "")).strip()]
    return {"added": added, "modified": modified, "deleted": deleted}


def classify_conflict(
    base_nodes: Sequence[ASTNode],
    current_nodes: Sequence[ASTNode],
    other_nodes: Sequence[ASTNode],
    *,
    syntax_error: bool = False,
    unsupported: bool = False,
) -> ASTReport:
    """Classify whether changes in the two branches affect the same syntax node."""
    if syntax_error:
        return ASTReport(conflict_type="syntax_error", changed_functions=[], overlapping_nodes=False)
    if unsupported:
        return ASTReport(conflict_type="unsupported", changed_functions=[], overlapping_nodes=False)

    if any(node.get("type") == "unsupported_statement"
           for nodes in (base_nodes, current_nodes, other_nodes) for node in nodes):
        return ASTReport(conflict_type="unsupported", changed_functions=[], overlapping_nodes=False)

    for nodes in (base_nodes, current_nodes, other_nodes):
        keys = [_node_key(node) for node in nodes]
        if len(keys) != len(set(keys)):
            # Ambiguous duplicate top-level names are unsafe to auto-resolve.
            return ASTReport(conflict_type="semantic", changed_functions=[], overlapping_nodes=True)

    current_diff = diff_nodes(base_nodes, current_nodes)
    other_diff = diff_nodes(base_nodes, other_nodes)
    current_changed = {_node_key(node) for action in current_diff.values() for node in action}
    other_changed = {_node_key(node) for action in other_diff.values() for node in action}
    overlaps = current_changed & other_changed
    changed_names: set[str] = set()
    nodes_by_key = {
        _node_key(node): node
        for collection in (base_nodes, current_nodes, other_nodes)
        for node in collection
    }
    for key in current_changed | other_changed:
        node = nodes_by_key[key]
        if node.get("name"):
            changed_names.add(str(node["name"]))

    return ASTReport(
        conflict_type="semantic" if overlaps else "trivial",
        changed_functions=sorted(changed_names),
        overlapping_nodes=bool(overlaps),
    )
