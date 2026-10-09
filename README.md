# MergeMind

MergeMind is a local-first Git merge driver that classifies Python changes,
automatically combines disjoint top-level edits, and sends overlapping edits to
a local Ollama coding model. The current core supports Python; JavaScript and
Java parsing, container verification, security scanning, persistence, and the
web UI are not implemented in this checkout yet.

## Setup

Use Python 3.10 or later. From the repository root, create and activate a
virtual environment, then install the Python packages:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Install and start [Ollama](https://ollama.com/) for semantic conflicts, then
download the configured model:

```powershell
ollama pull qwen2.5-coder:3b
ollama serve
```

Phase 2 also requires a running Docker Desktop/Engine and a local Semgrep CLI.
Build the isolated verifier image once after Docker has started:

```powershell
python -m pip install -r requirements.txt
docker info
docker build -f Dockerfile.verifier -t mergemind-verifier:py312 .
```

The requirements install the Semgrep CLI in the active Python environment;
verify it with `\.\venv\Scripts\semgrep.exe --version` (or `semgrep --version`
after activating the venv). The container build may take several minutes since
it installs the project requirements and pytest.

The verifier copies project source and test files into a network-disabled,
read-only container with resource limits. Set `MERGEMIND_DOCKER_IMAGE` to use a
different prebuilt image. The semantic flow retries a failed or rejected patch
up to three times after the first generation, feeding bounded test output back
to Ollama. Semgrep scanner outages and critical/blocking findings prevent the
candidate from being written. Warning findings are returned for human review.

Tree-sitter's Python grammar is used for parsing when installed. The Python
standard library AST provides source node extraction and a fallback parser.

## Start the API

```powershell
python -m uvicorn backend.main:app --reload
```

Open `http://127.0.0.1:8000/docs` and submit a `POST /api/resolve` request:

```json
{
  "file_path": "src/example.py",
  "ancestor_path": "C:/merge/base/example.py",
  "current_path": "C:/merge/current/example.py",
  "other_path": "C:/merge/other/example.py"
}
```

The three paths must be readable UTF-8 Python files. Successful trivial or AI
resolution writes the merged code to `current_path`. The API returns the AST
report, resolved code, and (for AI resolutions) the validated patch.

## Run the Git merge driver

```powershell
python -m tools.mergemind_driver <ancestor-file> <current-file> <other-file> [marker-size] [path]
```

Git merge-driver configuration can point to the same module invocation:

```ini
[merge "mergemind"]
    name = MergeMind local merge assistant
    driver = python -m tools.mergemind_driver %O %A %B %L %P
```

The driver returns `0` when it writes a resolved result and `1` when it cannot
resolve the conflict. Review AI-generated changes before committing them.

## Current resolution behavior

- Disjoint top-level Python imports, simple assignments, functions, and classes
  are merged from the incoming branch into the current branch.
- Changes to the same named node are treated as semantic conflicts and sent to
  Ollama. If Ollama, Docker, or Semgrep is unavailable, the current file is left
  unchanged and the result includes the failing stage's diagnostic.
- Unsupported top-level Python statements and non-Python files are rejected for
  automatic resolution.
- Generated edits are parsed before any host file is changed. Candidates go
  through isolated tests and security scanning when Phase 2 services are ready.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the current data flow and limits.
