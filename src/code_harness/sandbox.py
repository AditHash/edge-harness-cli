"""Build a filtered snapshot and execute it in a WSL Bubblewrap sandbox."""

import base64
import ctypes
import io
import json
import os
import subprocess
import tarfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from unicodedata import category

from code_harness.workspace import Workspace, WorkspaceError

SNAPSHOT_FILES = 2000
SNAPSHOT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    reason: str
    duration_seconds: float
    omitted_files: int


def _wsl_executable() -> str:
    """Resolve the system WSL executable without searching the project or PATH."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    get_directory = kernel.GetSystemDirectoryW
    get_directory.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    get_directory.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_directory(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise WorkspaceError("Cannot locate the Windows system directory")
    return str(Path(buffer.value) / "wsl.exe")


def _snapshot(workspace: Workspace) -> tuple[bytes, int]:
    files = workspace.files(limit=SNAPSHOT_FILES + 1)
    if len(files) > SNAPSHOT_FILES:
        raise WorkspaceError("Workspace exceeds the 2000-file sandbox snapshot limit")
    result = io.BytesIO()
    total = 0
    omitted = 0
    with tarfile.open(fileobj=result, mode="w") as tar:
        for name in files:
            try:
                data = workspace.read(name).encode("utf-8")
            except WorkspaceError:
                omitted += 1
                continue
            total += len(data)
            if total > SNAPSHOT_BYTES:
                raise WorkspaceError("Workspace exceeds the 8 MiB sandbox snapshot limit")
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            entry.mode = 0o600
            tar.addfile(entry, io.BytesIO(data))
    return result.getvalue(), omitted


def execute_in_sandbox(
    workspace: Workspace,
    command: str,
    review: Callable[[str], bool],
    *,
    timeout: int = 30,
    distro: str = "Ubuntu",
    cancel_signal: threading.Event | None = None,
) -> SandboxResult:
    """Run one approved command in a temporary network-isolated WSL sandbox."""
    if os.name != "nt":
        raise WorkspaceError("The Bubblewrap WSL backend currently requires Windows")
    if not command or len(command) > 4000 or not 1 <= timeout <= 60:
        raise WorkspaceError("Command or timeout is outside the allowed limits")
    if any(category(ch).startswith("C") and ch not in "\n\t" for ch in command):
        raise WorkspaceError("Command contains unsafe terminal control characters")
    if cancel_signal is not None and cancel_signal.is_set():
        raise WorkspaceError("Command canceled; nothing was executed")
    if not review(command):
        raise WorkspaceError("Command rejected; nothing was executed")
    if cancel_signal is not None and cancel_signal.is_set():
        raise WorkspaceError("Command canceled; nothing was executed")
    archive, omitted = _snapshot(workspace)
    helper = Path(__file__).with_name("wsl_sandbox_helper.py").read_text(encoding="utf-8")
    payload = (
        json.dumps(
            {
                "command": command,
                "timeout": timeout,
                "archive": base64.b64encode(archive).decode("ascii"),
            }
        ).encode()
        + b"\n"
    )
    try:
        process = subprocess.Popen(
            [_wsl_executable(), "-d", distro, "--exec", "/usr/bin/python3", "-I", "-c", helper],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        raise WorkspaceError("WSL sandbox could not start; no host command was run") from None
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()

    def collect(pipe, output: bytearray) -> None:
        while chunk := pipe.read(8192):
            if len(output) < 256 * 1024:
                output.extend(chunk[: 256 * 1024 - len(output)])

    readers = [
        threading.Thread(target=collect, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=collect, args=(process.stderr, stderr), daemon=True),
    ]
    for thread in readers:
        thread.start()
    try:
        process.stdin.write(payload)
        process.stdin.flush()
        deadline = time.monotonic() + timeout + 15
        while process.poll() is None:
            if cancel_signal is not None and cancel_signal.is_set() and not process.stdin.closed:
                process.stdin.close()
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(process.args, timeout + 15)
            time.sleep(0.05)
    except BaseException:
        process.stdin.close()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        for thread in readers:
            thread.join(timeout=3)
    if process.returncode != 0:
        raise WorkspaceError("WSL sandbox failed to initialize; no host fallback was used")
    try:
        data = json.loads(stdout)
        return SandboxResult(
            exit_code=int(data["exit_code"]),
            stdout=str(data["stdout"]),
            stderr=str(data["stderr"]),
            reason=str(data["reason"]),
            duration_seconds=float(data["duration_seconds"]),
            omitted_files=omitted,
        )
    except (ValueError, TypeError, KeyError):
        raise WorkspaceError("WSL sandbox returned an invalid response") from None
