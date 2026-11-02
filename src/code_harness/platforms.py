"""Select the sandbox backend for this OS; there is deliberately no unsandboxed fallback."""

import ctypes
import os
import sys
from pathlib import Path

from code_harness.workspace import WorkspaceError

BWRAP = "/usr/bin/bwrap"
LINUX_PYTHON = "/usr/bin/python3"


def sandbox_backend() -> str | None:
    """Return "wsl" (Windows), "linux" (native Bubblewrap) or None when unsupported."""
    if os.name == "nt":
        return "wsl"
    if sys.platform.startswith("linux") and os.access(BWRAP, os.X_OK):
        return "linux"
    return None


def sandbox_unavailable_reason() -> str:
    if sys.platform == "darwin":
        return (
            "macOS has no verified network-and-filesystem sandbox backend here; "
            "command execution and MCP servers stay disabled"
        )
    if sys.platform.startswith("linux"):
        return f"install Bubblewrap ({BWRAP}) with unprivileged user namespaces enabled"
    return "this operating system has no supported sandbox backend"


def wsl_executable() -> str:
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


def helper_command(helper_source: str, distro: str, *args: str) -> list[str]:
    """Build argv that runs the trusted supervisor script where Bubblewrap lives."""
    backend = sandbox_backend()
    if backend == "wsl":
        return [
            wsl_executable(),
            "-d",
            distro,
            "--exec",
            "/usr/bin/python3",
            "-I",
            "-c",
            helper_source,
            *args,
        ]
    if backend == "linux":
        return [LINUX_PYTHON, "-I", "-c", helper_source, *args]
    raise WorkspaceError(f"No sandbox available: {sandbox_unavailable_reason()}")
