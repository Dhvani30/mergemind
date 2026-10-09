# MergeMind Architecture

## Implemented core flow

```text
Git driver or POST /api/resolve
              |
              v
     Read base/current/other
              |
              v
   Parse Python source and triage
       /                 \
 disjoint               overlap
    |                      |
    v                      v
 AST trivial merge    Context extraction
    |                      |
    |                 Ollama JSON patch
    |                      |
    |                 Patch validation
    |                      |
    |                 Docker tests
    |                  /       \
    |             retry        Semgrep
    \__________________________/
              |
              v
      Atomic write to current
```

`tools/resolution_pipeline.py` owns orchestration. `tools/ast_parser.py` extracts
the source nodes, and `tools/ast_diff.py` compares them across the three merge
sides. `tools/trivial_resolver.py` applies non-overlapping incoming edits.
Semantic conflicts use `tools/context_extractor.py` and
`agents/merge_agent.py`; `tools/patch_applier.py` validates and applies the
model's constrained edits. Semantic candidates pass through
`agents/workflow.py`, which allows an initial generation plus at most three
corrections based on verifier output. `tools/docker_verifier.py` runs syntax
parsing and pytest in a network-disabled, read-only Docker container with
resource and time limits. `agents/security_agent.py` applies the local rules in
`security/rules.yaml` and returns a score, findings, and decision. Scanner errors
and critical/block decisions prevent file writes; warning findings are returned
for review. Both the FastAPI endpoint and Git merge driver call the same pipeline.

The current parser uses Tree-sitter's Python grammar when installed and CPython's
AST to provide source locations. The standard library parser remains a fallback.
The automatic merge subset is limited to top-level imports, simple assignments,
functions, and classes. Other top-level statement kinds are reported as
unsupported so they are not silently discarded.

## Data safety

- Input files are read as UTF-8 and rejected if they contain NUL bytes.
- Parsing and patch application happen before the current file is changed.
- Successful results are written with a temporary file in the target directory
  and an atomic replace.
- Ollama output is validated against `LLMPatch`; generated Python is parsed
  before writing. Model-generated code is not executed by this phase.
- Verification and security results are attached to the pipeline response.
- This phase has no database, user approval workflow, or persistent audit log;
  those remain follow-on components.

## Planned extensions

The project plan adds richer CLI commands, a React/Monaco review interface,
persistent resolution history, and benchmark evaluation. Those components are
not part of the current pipeline and are not represented as implemented here.
