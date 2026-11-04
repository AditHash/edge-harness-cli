from pathlib import Path

import pytest

from code_harness.images import (
    MAX_IMAGE_BYTES,
    detect_format,
    has_image,
    load_image,
    split_image_command,
)
from code_harness.workspace import WorkspaceError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (PNG, "png"),
        (b"\xff\xd8\xff\xe0rest", "jpeg"),
        (b"GIF89a....", "gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"),
        (b"SECRET=1\n", None),
        (b"", None),
    ],
)
def test_format_comes_from_the_header(data: bytes, expected: str | None) -> None:
    assert detect_format(data) == expected


def test_loads_relative_and_absolute_paths_outside_the_workspace(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.png").write_bytes(PNG)
    outside = tmp_path / "Downloads" / "shot.png"
    outside.parent.mkdir()
    outside.write_bytes(PNG)
    assert load_image("a.png", root).image_format == "png"
    loaded = load_image(str(outside), root)
    assert loaded.name == "shot.png"
    assert loaded.block() == {"image": {"format": "png", "source": {"bytes": PNG}}}


@pytest.mark.parametrize("name", [".env", "secret.png", "id_rsa.jpg"])
def test_renamed_secrets_are_refused_without_revealing_contents(tmp_path: Path, name: str) -> None:
    target = tmp_path / name
    target.write_bytes(b"TOKEN=super-secret\n")
    with pytest.raises(WorkspaceError) as error:
        load_image(str(target), tmp_path)
    assert "super-secret" not in str(error.value) and "not a PNG" in str(error.value)


def test_missing_oversized_and_folder_paths_are_refused(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError):
        load_image("missing.png", tmp_path)
    big = tmp_path / "big.png"
    big.write_bytes(PNG + b"\x00" * MAX_IMAGE_BYTES)
    with pytest.raises(WorkspaceError, match="3.75 MB"):
        load_image("big.png", tmp_path)
    with pytest.raises(WorkspaceError):
        load_image(str(tmp_path), tmp_path)


def test_links_are_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.png"
    real.write_bytes(PNG)
    link = tmp_path / "link.png"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(WorkspaceError):
        load_image("link.png", tmp_path)


@pytest.mark.parametrize(
    ("argument", "expected"),
    [
        ("a.png", ("a.png", "")),
        ("a.png what is this", ("a.png", "what is this")),
        ('"C:/My Files/a b.png" explain', ("C:/My Files/a b.png", "explain")),
        ("'x y.png'", ("x y.png", "")),
        ('"unterminated.png', ('"unterminated.png', "")),
    ],
)
def test_split_image_command(argument: str, expected: tuple[str, str]) -> None:
    assert split_image_command(argument) == expected


def test_has_image_scans_the_conversation() -> None:
    text = {"role": "user", "content": [{"text": "hi"}]}
    image = {"role": "user", "content": [{"image": {"format": "png"}}, {"text": "see"}]}
    assert not has_image([text]) and has_image([text, image])
    assert not has_image("not a list")
