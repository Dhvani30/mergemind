# MergeMind

MergeMind is a local-first Git merge driver that classifies Python changes,
automatically combines disjoint top-level edits, and sends overlapping edits to
a local Ollama coding model. The current core supports Python. Docker-based
verification and Semgrep security scanning are implemented; JavaScript and Java
parsing, persistence, and the web UI are not implemented in this checkout.

## Setup

Use Python 3.10 or later. The API can start without Ollama, Docker, or Semgrep,
but merge requests require Docker and Semgrep, and semantic conflicts also
require Ollama. The full workflow is best run in WSL2 because Semgrep does not
support native Windows.

### Windows: start the API only

From PowerShell at the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload
```

Open `http://127.0.0.1:8000/docs` to view the API. This confirms the API starts;
it does not mean conflict resolution is ready. On native Windows, Semgrep is
not available from the requirements file, and Docker Desktop must be running
and reachable for verification.

### WSL2: full conflict-resolution setup

Start Docker Desktop in Windows and enable WSL integration for the Ubuntu
distribution. In PowerShell, check the installed distribution name:

```powershell
wsl --list --verbose
wsl -d Ubuntu
```

If your distribution has a different name, replace `Ubuntu` with that name.
Run the following commands in the Ubuntu terminal (not in PowerShell):

```bash
cd /mnt/d/Projects/MergeMind/mergemind
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
mkdir -p "$HOME/.venvs"
python3 -m venv "$HOME/.venvs/mergemind"
source "$HOME/.venvs/mergemind/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install "semgrep>=1.70,<2.0"
semgrep --version
docker info
docker build -f Dockerfile.verifier -t mergemind-verifier:py312 .
python -m uvicorn backend.main:app --reload
```

If `docker info` cannot reach the server, check that Docker Desktop is running
and WSL integration is enabled before building the image. The image build may
take several minutes because it installs the project requirements and pytest.
Keep the API terminal open. For semantic conflicts, install Ollama in WSL if
needed using the [official Linux instructions](https://ollama.com/download/linux),
then start its server in a second Ubuntu terminal:

```bash
ollama serve
```

In a third Ubuntu terminal, download the model:

```bash
ollama pull qwen2.5-coder:3b
```

If Ollama is already running, do not start a second server.
Open `http://127.0.0.1:8000/docs` from your browser.

The verifier copies project source and test files into a network-disabled,
read-only container with resource limits. Set `MERGEMIND_DOCKER_IMAGE` to use a
different prebuilt image. The semantic flow retries a failed or rejected patch
up to three times after the first generation, feeding bounded test output back
to Ollama. Semgrep scanner outages and critical/blocking findings prevent the
candidate from being written. Warning findings are returned for human review.

Tree-sitter's Python grammar is used for parsing when installed. The Python
standard library AST provides source node extraction and a fallback parser.

## Start the API

With the virtual environment activated and dependencies installed, run:

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
