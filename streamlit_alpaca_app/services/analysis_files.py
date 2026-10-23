"""Owned file analysis in a credential-free container.

The caller admits immutable input files and owns the workspace lifecycle. Model
arguments contain code, never host paths, container options, or credentials.
The local Docker transport is for research qualification; serving must provide
an equivalent isolated executor and authenticated artifact delivery.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import threading
import time
import uuid

from services.work_scope import check_work, remaining_work_seconds, register_cancellation


_WORKSPACE = ContextVar("analysis_file_workspace", default=None)
MAX_EXPORT_BYTES = 64 * 1024 * 1024


def current_workspace():
    return _WORKSPACE.get()


def _bounded_output(process, request, timeout):
    process.stdin.write(request)
    process.stdin.close()
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        for stream in streams:
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            check_work()
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(process.args, timeout)
            for key, _ in selector.select(.1):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                streams[key.fileobj].extend(chunk)
                if sum(map(len, streams.values())) > 2 * 1024 * 1024:
                    raise ValueError("File executor transport exceeded2MiB")
        process.wait(timeout=max(.001, deadline - time.monotonic()))
    return bytes(streams[process.stdout]), bytes(streams[process.stderr])


class FileAnalysisWorkspace:
    def __init__(self, *, files: dict[str, Path], artifact_root: Path,
                 image: str, docker_context: str, memory_mb: int = 2048):
        if not image.startswith("sha256:") or len(image) != 71:
            raise ValueError("File execution requires an immutable local image ID")
        if not 512 <= memory_mb <= 4096:
            raise ValueError("File executor memory must be between512 and4096MiB")
        self.image, self.memory_mb = image, memory_mb
        self.docker = ["docker", "--context", docker_context]
        self.root = Path(artifact_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.staging = Path(tempfile.mkdtemp(prefix="inputs-", dir=self.root))
        self.files = []
        self._container = None
        self._lock = threading.RLock()
        for name, source in files.items():
            if not name or Path(name).name != name or name in {".", ".."}:
                raise ValueError("Input names must be unique plain filenames")
            source = Path(source)
            if source.is_symlink() or not source.is_file():
                raise ValueError("Only declared regular input files may be admitted")
            data = source.read_bytes()
            if len(data) > MAX_EXPORT_BYTES:
                raise ValueError("One admitted file exceeds64MiB")
            (self.staging / name).write_bytes(data)
            self.files.append({"name": name, "bytes": len(data),
                               "sha256": hashlib.sha256(data).hexdigest()})
        self._write("inputs.json", self.files)

    def _write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
        path.chmod(0o600)

    def _command(self, args, *, timeout=30, input=None):
        return subprocess.run(self.docker + args, input=input, capture_output=True,
                              timeout=timeout, check=True).stdout

    @contextmanager
    def activate(self):
        token = _WORKSPACE.set(self)
        try:
            yield self
        finally:
            _WORKSPACE.reset(token)

    def __enter__(self):
        runtime = Path(__file__).with_name("analysis_files_runtime.py").resolve()
        command = ["run", "-d", "--network", "none", "--read-only", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges", "--user", "10001:10001",
                   "--memory", f"{self.memory_mb}m", "--memory-swap", f"{self.memory_mb}m",
                   "--pids-limit", "128", "--cpus", "1", "--log-driver", "none",
                   "--tmpfs", "/workspace:rw,nosuid,nodev,size=512m,uid=10001,gid=10001,mode=700",
                   "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=128m,uid=10001,gid=10001,mode=700",
                   "--mount", f"type=bind,src={self.staging},dst=/input,readonly",
                   "--mount", f"type=bind,src={runtime},dst=/runtime.py,readonly",
                   "--workdir", "/workspace", "--env", "HOME=/workspace/home",
                   "--entrypoint", "/bin/sleep", self.image, "infinity"]
        self._container = self._command(command).decode().strip()
        try:
            inspected = json.loads(self._command(["inspect", self._container]))[0]
            self._write("container.json", inspected)
        except BaseException:
            self.close()
            raise
        return self

    def close(self):
        with self._lock:
            container, self._container = self._container, None
        if container:
            try:
                self._command(["rm", "-f", container])
            except Exception as exc:
                self._write("cleanup-error.json", {"container": container, "error": str(exc)})
                raise

    def __exit__(self, *_):
        self.close()

    def run(self, code: str, *, timeout_seconds: int = 120):
        check_work()
        timeout = remaining_work_seconds(min(max(int(timeout_seconds), 1), 900))
        identifier = "fileanalysis_" + uuid.uuid4().hex
        folder = self.root / identifier
        folder.mkdir(mode=0o700)
        (folder / "code.py").write_text(code)
        started = time.monotonic()
        process = None
        unregister = register_cancellation(self.close)
        try:
            if not self._container:
                raise RuntimeError("File workspace is no longer available")
            process = subprocess.Popen(self.docker + ["exec", "-i", self._container,
                "python3", "-I", "/runtime.py", "run"], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            request = json.dumps({"code": code, "inputs": self.files}).encode()
            try:
                out, err = _bounded_output(process, request, timeout)
            except BaseException:
                self.close()  # Terminate every descendant, including native calculation processes.
                process.kill()
                process.wait()
                raise
            check_work()
            if process.returncode:
                raise RuntimeError(f"File executor exited{process.returncode}: {err.decode(errors='replace')[:2000]}")
            result = json.loads(out)
            exported = json.loads(self._command(["exec", self._container,
                "python3", "-I", "/runtime.py", "export"], timeout=30))
            artifacts = []
            for item in exported:
                data = base64.b64decode(item["base64"], validate=True)
                name = item["name"]
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Invalid output filename")
                path = folder / "files" / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                digest = hashlib.sha256(data).hexdigest()
                artifacts.append({"artifact_type": "file", "name": name,
                    "artifact_id": f"{identifier}:{digest}", "bytes": len(data), "sha256": digest})
            result.update(analysis_run_id=identifier, artifacts=artifacts,
                          elapsed_ms=int((time.monotonic() - started) * 1000))
        except Exception as exc:
            result = {"status": "failed", "analysis_run_id": identifier,
                      "error": f"{type(exc).__name__}: {exc}", "artifacts": [],
                      "elapsed_ms": int((time.monotonic() - started) * 1000)}
        finally:
            unregister()
        result['llm_context_text'] = json.dumps({k: v for k, v in result.items()
                                                if k != 'llm_context_text'}, ensure_ascii=False)
        (folder / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        return result


def run_file_analysis(*, code: str, timeout_seconds: int = 120):
    workspace = current_workspace()
    if workspace is None:
        raise ValueError("No owned file workspace is attached to this investigation")
    return workspace.run(code, timeout_seconds=timeout_seconds)
