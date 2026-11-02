"""Read-only workspace access, with authorization at the open file handle."""

import ctypes
import os
import stat
from pathlib import Path

MAX_FILE_BYTES = 128 * 1024
PROTECTED = {
    ".git",
    ".aws",
    ".ssh",
    ".gnupg",
    ".azure",
    ".kube",
    ".docker",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "credentials",
    "id_rsa",
    "id_ed25519",
}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kdbx"}
SKIPPED_DIRECTORIES = {
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
MAX_DISCOVERY_FILES = 500
MAX_SEARCH_RESULTS = 50


class WorkspaceError(ValueError):
    """A workspace access was rejected or could not be completed safely."""


def _protected(parts: tuple[str, ...]) -> bool:
    return any(
        p.casefold() in PROTECTED
        or p.casefold() == ".env"
        or p.casefold().startswith(".env.")
        or Path(p).suffix.casefold() in SECRET_SUFFIXES
        for p in parts
    )


def _handle_path(fd: int) -> Path:
    if os.name == "nt":
        import msvcrt

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        get_path = kernel.GetFinalPathNameByHandleW
        get_path.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        get_path.restype = ctypes.c_uint32
        buf = ctypes.create_unicode_buffer(32768)
        size = get_path(msvcrt.get_osfhandle(fd), buf, len(buf), 0)
        if not size or size >= len(buf):
            raise WorkspaceError("Unable to verify opened file path")
        name = buf.value
        if name.startswith("\\\\?\\UNC\\"):
            name = "\\\\" + name[8:]
        elif name.startswith("\\\\?\\"):
            name = name[4:]
        return Path(name).resolve()
    proc = Path(f"/proc/self/fd/{fd}")
    if not proc.exists():
        raise WorkspaceError("Handle path verification is unavailable on this platform")
    return proc.resolve(strict=True)


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise WorkspaceError("Workspace must be a directory")

    def path(self, relative: str) -> Path:
        path = Path(relative)
        if (
            path.is_absolute()
            or ".." in path.parts
            or _protected(path.parts)
            or any(":" in part or any(ord(ch) < 32 for ch in part) for part in path.parts)
        ):
            raise WorkspaceError("Path is outside the workspace or protected")
        target = self.root / path
        try:
            resolved = target.resolve(strict=False)
            parts = resolved.relative_to(self.root).parts
        except (ValueError, OSError, RuntimeError):
            raise WorkspaceError("Path is outside the workspace or invalid") from None
        if _protected(parts):
            raise WorkspaceError("Path is protected")
        # Disallow links, including junctions, rather than silently following aliases.
        current = self.root
        for part in path.parts:
            current = current / part
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise WorkspaceError("Links and junctions are not allowed")
        return target

    def read(self, relative: str) -> str:
        path = self.path(relative)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            fd = os.open(path, flags)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                real = _handle_path(stream.fileno())
                try:
                    real_parts = real.relative_to(self.root).parts
                except ValueError:
                    raise WorkspaceError("Opened file is outside the workspace") from None
                if _protected(real_parts) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise WorkspaceError("Opened file is protected or not a regular unlinked file")
                if info.st_size > MAX_FILE_BYTES:
                    raise WorkspaceError("File exceeds size limit")
                data = stream.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    raise WorkspaceError("File exceeds size limit")
                return data.decode("utf-8")
        except (OSError, UnicodeError):
            raise WorkspaceError("File cannot be read as UTF-8") from None

    def files(self, limit: int = MAX_DISCOVERY_FILES) -> list[str]:
        results: list[str] = []
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            base = Path(directory)
            safe_dirs = []
            for name in sorted(dirs):
                if name.casefold() in SKIPPED_DIRECTORIES:
                    continue
                relative = (base / name).relative_to(self.root).as_posix()
                try:
                    self.path(relative)
                    safe_dirs.append(name)
                except WorkspaceError:
                    continue
            dirs[:] = safe_dirs
            for name in sorted(files):
                relative = (base / name).relative_to(self.root).as_posix()
                try:
                    self.path(relative)
                except WorkspaceError:
                    continue
                results.append(relative)
                if len(results) >= limit:
                    return results
        return results

    def search(self, query: str) -> list[str]:
        if not query or len(query) > 200:
            raise WorkspaceError("Search query must contain 1 to 200 characters")
        results = []
        files = self.files()
        for path in files:
            try:
                contents = self.read(path)
            except WorkspaceError:
                continue
            for number, line in enumerate(contents.splitlines(), 1):
                if query.casefold() in line.casefold():
                    results.append(f"{path}:{number}: {line[:300]}")
                    if len(results) >= MAX_SEARCH_RESULTS:
                        results.append("[Search results truncated at 50 matches.]")
                        return results
        if len(files) >= MAX_DISCOVERY_FILES:
            results.append("[Search may be incomplete: file discovery reached its 500-file limit.]")
        return results
