"""Images a user attaches to a message with /image.

The path comes from the person at the keyboard, never from the model, so it may point outside the
workspace (a screenshot in Downloads, say). The content must still be a real image: the format is
decided by the file header, not its name, so a renamed `.env` or key file is refused and its
contents never leave the machine.
"""

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_harness.workspace import WorkspaceError

# Bedrock Converse image size limit.
MAX_IMAGE_BYTES = int(3.75 * 1024 * 1024)
MAX_PENDING_IMAGES = 5


@dataclass(frozen=True)
class AttachedImage:
    name: str
    image_format: str
    data: bytes

    def block(self) -> dict[str, Any]:
        return {"image": {"format": self.image_format, "source": {"bytes": self.data}}}


def detect_format(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def split_image_command(argument: str) -> tuple[str, str]:
    """Split `path [message]`; the path may be quoted, as terminals do when dragging a file."""
    text = argument.strip()
    if text[:1] in {'"', "'"}:
        closing = text.find(text[0], 1)
        if closing > 0:
            return text[1:closing], text[closing + 1 :].strip()
    path, _, message = text.partition(" ")
    return path, message.strip()


def load_image(raw_path: str, base: Path) -> AttachedImage:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = base / path
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        if path.is_symlink():
            raise WorkspaceError("Image path cannot be a link")
        fd = os.open(path, flags)
    except OSError:
        raise WorkspaceError(f"Cannot open image: {path.name}") from None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise WorkspaceError("Image path is not a regular file")
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise WorkspaceError("Image is larger than the 3.75 MB limit")
    image_format = detect_format(data)
    if image_format is None:
        raise WorkspaceError(f"{path.name} is not a PNG, JPEG, GIF or WebP image")
    return AttachedImage(path.name, image_format, data)


def has_image(messages: Any) -> bool:
    """True if any message in a conversation carries an image block."""
    if not isinstance(messages, list):
        return False
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list) and any(
            isinstance(block, dict) and "image" in block for block in content
        ):
            return True
    return False
