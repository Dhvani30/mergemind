"""Run Python syntax checks and pytest in a disposable, locked-down container."""

from __future__ import annotations

import io
import os
import posixpath
import tarfile
from pathlib import Path
from typing import Iterable

from schemas.models import VerificationReport

DEFAULT_IMAGE = "mergemind-verifier:py312"
_EXCLUDED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".aws", ".codex", ".agents"}
_INCLUDED_SUFFIXES = {".py", ".json", ".txt", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".csv", ".xml"}

_RUNNER = r'''import json, os, subprocess, sys
marker, timeout, *tests = sys.argv[1:]
timeout = int(timeout)
syntax_script = "import ast,pathlib,sys; files=list(pathlib.Path('.').rglob('*.py')); " \
                "[(ast.parse(p.read_text(encoding='utf-8'), filename=str(p))) for p in files]"
compile_result = subprocess.run([sys.executable, "-c", syntax_script],
                                capture_output=True, text=True, timeout=timeout)
stdout, stderr = compile_result.stdout, compile_result.stderr
syntax_valid = compile_result.returncode == 0
timed_out = False
exit_code = compile_result.returncode
if syntax_valid:
    if not tests:
        tests = ["tests"] if os.path.isdir("tests") else []
    if not tests:
        stderr += "\nNo test files or tests/ directory were included in the verification workspace."
        exit_code = 5
    else:
        try:
            result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
                                    capture_output=True, text=True, timeout=timeout,
                                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            stdout += result.stdout
            stderr += result.stderr
            exit_code = result.returncode
        except subprocess.TimeoutExpired as exc:
            stdout += (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr += (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            stderr += "\npytest exceeded the configured time limit."
            timed_out, exit_code = True, 124
limit = 64000
result = {"syntax_valid": syntax_valid, "stdout": stdout[-limit:], "stderr": stderr[-limit:],
          "exit_code": exit_code, "timed_out": timed_out}
print(marker + json.dumps(result, ensure_ascii=False))
'''


class DockerVerifier:
    """Verify a candidate Python workspace using an isolated Docker container."""

    def __init__(
        self,
        *,
        image: str | None = None,
        timeout_seconds: int = 120,
        memory_limit: str = "768m",
        cpu_count: float = 1.0,
        max_workspace_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        """Set image and resource limits; environment variables can override defaults."""
        self.image = image or os.getenv("MERGEMIND_DOCKER_IMAGE", DEFAULT_IMAGE)
        self.timeout_seconds = timeout_seconds
        self.memory_limit = memory_limit
        self.cpu_count = cpu_count
        self.max_workspace_bytes = max_workspace_bytes

    @staticmethod
    def _failure(message: str) -> VerificationReport:
        """Return a normalized report for Docker/setup errors."""
        return VerificationReport(syntax_valid=False, tests_passed=False, stdout="", stderr=message,
                                 exit_code=125, error=message)

    def _workspace_archive(
        self,
        source_file: Path,
        source_content: str,
        project_root: Path,
        test_files: Iterable[str | Path] | None,
        relative_file: str | None = None,
    ) -> tuple[bytes, list[str]]:
        """Pack project source and tests under workspace/ without following symlinks."""
        root = project_root.resolve()
        source_file = source_file.resolve()
        if relative_file is not None:
            relative_file = relative_file.replace("\\", "/")
            normalized = posixpath.normpath(relative_file)
            if normalized.startswith("../") or normalized in {".", ".."} or normalized.startswith("/"):
                raise ValueError("relative_file must stay inside project_root")
            source_rel = Path(normalized)
        else:
            try:
                source_rel = source_file.relative_to(root)
            except ValueError as exc:
                raise ValueError("The patched file must be located under project_root") from exc

        selected_tests: set[Path] = set()
        if test_files is None:
            for path in root.rglob("test_*.py"):
                if path.is_file() and not any(part in _EXCLUDED_DIRS for part in path.parts):
                    selected_tests.add(path.resolve())
            for path in root.rglob("*_test.py"):
                if path.is_file() and not any(part in _EXCLUDED_DIRS for part in path.parts):
                    selected_tests.add(path.resolve())
        else:
            for candidate in test_files:
                path = Path(candidate).resolve()
                if not path.is_file():
                    raise ValueError(f"Test file does not exist: {path}")
                try:
                    path.relative_to(root)
                except ValueError as exc:
                    raise ValueError(f"Test file is outside project_root: {path}") from exc
                selected_tests.add(path)

        contents: dict[str, bytes] = {}
        for path in root.rglob("*"):
            if any(part in _EXCLUDED_DIRS for part in path.parts) or path.is_symlink() or not path.is_file():
                continue
            if path.name.startswith(".env") or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
                continue
            if path.suffix.lower() not in _INCLUDED_SUFFIXES:
                continue
            relative = path.relative_to(root).as_posix()
            try:
                contents[relative] = path.read_bytes()
            except OSError:
                continue

        for test_path in selected_tests:
            relative = test_path.relative_to(root).as_posix()
            contents[relative] = test_path.read_bytes()
        contents[source_rel.as_posix()] = source_content.encode("utf-8")
        total_bytes = sum(len(data) for data in contents.values())
        if total_bytes > self.max_workspace_bytes:
            raise ValueError(f"Verification workspace exceeds {self.max_workspace_bytes} bytes")

        tests = sorted(path.relative_to(root).as_posix() for path in selected_tests)
        if not tests:
            tests = [relative for relative in sorted(contents)
                     if Path(relative).name.startswith("test_") and relative.endswith(".py")]

        archive_buffer = io.BytesIO()
        directories: set[str] = {"workspace"}
        for relative in contents:
            parent = Path(relative).parent
            while parent != Path("."):
                directories.add("workspace/" + parent.as_posix())
                parent = parent.parent
        with tarfile.open(fileobj=archive_buffer, mode="w") as archive:
            for directory in sorted(directories, key=lambda item: (item.count("/"), item)):
                info = tarfile.TarInfo(directory.rstrip("/") + "/")
                info.type, info.mode, info.uid, info.gid = tarfile.DIRTYPE, 0o555, 0, 0
                archive.addfile(info)
            for relative, data in sorted(contents.items()):
                info = tarfile.TarInfo("workspace/" + relative)
                info.size, info.mode, info.uid, info.gid = len(data), 0o444, 0, 0
                archive.addfile(info, io.BytesIO(data))
        return archive_buffer.getvalue(), tests

    def verify_file(
        self,
        patched_file: str | Path,
        *,
        patched_content: str,
        project_root: str | Path,
        test_files: Iterable[str | Path] | None = None,
        relative_file: str | None = None,
    ) -> VerificationReport:
        """Run compileall and pytest without network, privileges, or writable source."""
        path, root = Path(patched_file), Path(project_root)
        try:
            archive, tests = self._workspace_archive(path, patched_content, root, test_files, relative_file)
        except (OSError, UnicodeError, ValueError) as exc:
            return self._failure(str(exc))

        try:
            import docker
            from docker.errors import DockerException, ImageNotFound
        except ImportError:
            return self._failure("Docker SDK is missing; install the requirements from requirements.txt")

        client = None
        container = None
        try:
            client = docker.from_env(timeout=self.timeout_seconds + 30)
            client.ping()
            try:
                client.images.get(self.image)
            except ImageNotFound:
                return self._failure(
                    f"Verifier image {self.image!r} is missing. Build it with: "
                    "docker build -f Dockerfile.verifier -t mergemind-verifier:py312 ."
                )
            marker = "__MERGEMIND_RESULT__"
            container = client.containers.create(
                self.image,
                command=["python", "-c", "import time; time.sleep(3600)"],
                working_dir="/workspace",
                user="65532:65532",
                network_disabled=True,
                read_only=True,
                tmpfs={"/tmp": "rw,nosuid,nodev,size=128m"},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit=self.memory_limit,
                nano_cpus=int(self.cpu_count * 1_000_000_000),
                pids_limit=128,
                environment={"HOME": "/tmp", "TMPDIR": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"},
                detach=True,
                auto_remove=False,
            )
            container.start()
            if not container.put_archive("/", archive):
                raise RuntimeError("Docker did not accept the verification workspace archive")
            command = ["python", "-c", _RUNNER, marker, str(self.timeout_seconds), *tests]
            result = container.exec_run(command, workdir="/workspace", user="65532:65532", demux=True)
            stdout_bytes, stderr_bytes = result.output if isinstance(result.output, tuple) else (result.output, b"")
            stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")
            stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")
            payload_line = next((line for line in reversed(stdout.splitlines()) if line.startswith(marker)), None)
            if payload_line is None:
                details = (stderr + "\n" + stdout).strip() or "Verifier did not return a result payload"
                return self._failure(details[-8_000:])
            import json
            result_data = json.loads(payload_line[len(marker):])
            report_out, report_err = result_data["stdout"], result_data["stderr"]
            if stderr:
                report_err = (report_err + "\n" + stderr).strip()
            return VerificationReport(
                syntax_valid=bool(result_data["syntax_valid"]),
                tests_passed=int(result_data["exit_code"]) == 0 and bool(result_data["syntax_valid"]),
                stdout=report_out[-64_000:], stderr=report_err[-64_000:],
                exit_code=int(result_data["exit_code"]), timed_out=bool(result_data["timed_out"]),
            )
        except Exception as exc:
            docker_exception = locals().get("DockerException", ())
            if docker_exception and isinstance(exc, docker_exception):
                return self._failure(f"Docker service error: {exc}")
            return self._failure(f"Docker verification failed: {exc}")
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
