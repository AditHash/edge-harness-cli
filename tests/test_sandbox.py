import os
import subprocess
import threading
from pathlib import Path

import pytest

from code_harness.platforms import sandbox_backend
from code_harness.sandbox import _snapshot, execute_in_sandbox
from code_harness.workspace import Workspace, WorkspaceError


def test_rejection_does_not_read_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if sandbox_backend() is None:
        pytest.skip("no sandbox backend on this platform")
    workspace = Workspace(tmp_path)
    monkeypatch.setattr(workspace, "files", lambda **kwargs: pytest.fail("snapshot was read"))
    with pytest.raises(WorkspaceError, match="rejected"):
        execute_in_sandbox(workspace, "true", lambda _: False)


def test_snapshot_excludes_secrets_and_limits_output(tmp_path: Path) -> None:
    import io
    import tarfile

    (tmp_path / "app.py").write_text("print(1)\n")
    (tmp_path / ".env").write_text("secret")
    archive, omitted = _snapshot(Workspace(tmp_path))
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        assert tar.getnames() == ["app.py"]
    assert omitted == 0


def test_command_approval_is_independent() -> None:
    from code_harness.approvals import review_command

    shown = []
    assert not review_command(
        "echo hello", interactive=False, display=shown.append, read_reply=lambda _: "yes"
    )
    assert not shown
    assert review_command(
        "echo hello", interactive=True, display=shown.append, read_reply=lambda _: "yes"
    )
    assert "echo hello" in shown[0]


@pytest.mark.skipif(
    os.environ.get("HARNESS_TEST_WSL") != "1", reason="WSL sandbox integration is opt-in"
)
def test_real_wsl_sandbox_isolation_and_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARNESS_CANARY_VALUE", "must-not-reach-sandbox")
    (tmp_path / "app.py").write_text("print('sandbox-ok')\n")
    (tmp_path / ".env").write_text("secret")
    workspace = Workspace(tmp_path)
    result = execute_in_sandbox(workspace, "python3 app.py", lambda _: True, timeout=20)
    assert result.exit_code == 0 and result.reason == "completed"
    assert "sandbox-ok" in result.stdout
    result = execute_in_sandbox(
        workspace, "test ! -e /home && test ! -e /mnt && test ! -e .env", lambda _: True, timeout=20
    )
    assert result.exit_code == 0
    result = execute_in_sandbox(
        workspace,
        "python3 -c 'import os; assert os.getuid()!=0; assert \"HARNESS_CANARY_VALUE\" not in os.environ'",
        lambda _: True,
        timeout=20,
    )
    assert result.exit_code == 0
    result = execute_in_sandbox(workspace, "exit 7", lambda _: True, timeout=20)
    assert result.exit_code == 7 and result.reason == "completed"
    result = execute_in_sandbox(
        workspace,
        "python3 -c 'import socket; s=socket.socket(); s.settimeout(1); assert s.connect_ex((\"1.1.1.1\",443)) != 0'",
        lambda _: True,
        timeout=20,
    )
    assert result.exit_code == 0
    result = execute_in_sandbox(workspace, "printf changed > app.py", lambda _: True, timeout=20)
    assert result.exit_code == 0
    assert (tmp_path / "app.py").read_text() == "print('sandbox-ok')\n"


@pytest.mark.skipif(
    os.environ.get("HARNESS_TEST_WSL") != "1", reason="WSL sandbox integration is opt-in"
)
def test_real_wsl_sandbox_reports_actual_test_results(tmp_path: Path) -> None:
    fixture = tmp_path / "test_sample.py"
    fixture.write_text(
        "import unittest\nclass Example(unittest.TestCase):\n"
        "    def test_math(self): self.assertEqual(1 + 1, 3)\n"
    )
    workspace = Workspace(tmp_path)
    failed = execute_in_sandbox(workspace, "python3 -m unittest -v", lambda _: True, timeout=20)
    assert failed.exit_code != 0 and "FAILED" in failed.stderr
    fixture.write_text(
        "import unittest\nclass Example(unittest.TestCase):\n"
        "    def test_math(self): self.assertEqual(1 + 1, 2)\n"
    )
    passed = execute_in_sandbox(workspace, "python3 -m unittest -v", lambda _: True, timeout=20)
    assert passed.exit_code == 0 and "OK" in passed.stderr


@pytest.mark.skipif(
    os.environ.get("HARNESS_TEST_WSL") != "1", reason="WSL sandbox integration is opt-in"
)
def test_real_wsl_sandbox_timeout_and_output_limit(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    result = execute_in_sandbox(workspace, "sleep 15", lambda _: True, timeout=1)
    assert result.reason == "timeout" and result.exit_code != 0
    result = execute_in_sandbox(
        workspace, "python3 -c 'print(\"x\"*200000)'", lambda _: True, timeout=20
    )
    assert result.reason == "output_limit" and len(result.stdout.encode()) <= 65536


@pytest.mark.skipif(
    os.environ.get("HARNESS_TEST_WSL") != "1", reason="WSL sandbox integration is opt-in"
)
def test_real_wsl_sandbox_cancel(tmp_path: Path) -> None:
    cancellation = threading.Event()
    timer = threading.Timer(3, cancellation.set)
    timer.start()
    try:
        result = execute_in_sandbox(
            Workspace(tmp_path),
            "python3 -c 'import time; marker=\"edge_harness_cancel_probe_93041\"; time.sleep(25)'",
            lambda _: True,
            timeout=30,
            cancel_signal=cancellation,
        )
    finally:
        timer.cancel()
    assert result.reason == "canceled" and result.exit_code != 0
    remaining = subprocess.run(
        [
            "wsl.exe",
            "-d",
            "Ubuntu",
            "--exec",
            "/usr/bin/pgrep",
            "-f",
            "edge_harness_cancel_probe_93041",
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert remaining.returncode == 1


def test_unsupported_platform_has_no_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from code_harness import sandbox

    monkeypatch.setattr(sandbox, "sandbox_backend", lambda: None)
    with pytest.raises(WorkspaceError, match="No sandbox available"):
        sandbox.execute_in_sandbox(Workspace(tmp_path), "true", lambda _: True)


def test_macos_reports_disabled_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    from code_harness import platforms

    monkeypatch.setattr(platforms.sys, "platform", "darwin")
    monkeypatch.setattr(platforms.os, "name", "posix")
    assert platforms.sandbox_backend() is None
    assert "disabled" in platforms.sandbox_unavailable_reason()
