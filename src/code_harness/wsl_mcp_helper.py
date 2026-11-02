# mypy: disable-error-code=attr-defined
"""Run a stdio MCP server inside WSL Bubblewrap and relay bounded protocol traffic."""

import json
import os
import resource
import signal
import subprocess
import sys
import threading
import time

MAX_PROTOCOL_BYTES = 2 * 1024 * 1024


def _limits() -> None:
    for kind, amount in (
        (resource.RLIMIT_AS, 768 * 1024 * 1024),
        (resource.RLIMIT_CPU, 120),
        (resource.RLIMIT_NPROC, 64),
        (resource.RLIMIT_NOFILE, 128),
        (resource.RLIMIT_FSIZE, 32 * 1024 * 1024),
    ):
        resource.setrlimit(kind, (amount, amount))


def main() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("The WSL sandbox requires a non-root distribution user")
    snapshot_path = sys.argv[1]
    server_argv = json.loads(sys.argv[2])
    bootstrap = (
        "import json,os,tarfile; "
        "t=tarfile.open('/snapshot.tar'); t.extractall('/workspace',filter='data'); t.close(); "
        "a=json.loads(os.environ.pop('HARNESS_MCP_ARGV')); os.execvp(a[0],a)"
    )
    with open(snapshot_path, "rb") as snapshot:
        args = [
            "/usr/bin/bwrap",
            "--unshare-all",
            "--unshare-user",
            "--disable-userns",
            "--new-session",
            "--die-with-parent",
            "--ro-bind",
            "/usr/bin",
            "/usr/bin",
            "--ro-bind",
            "/usr/lib",
            "/usr/lib",
            "--ro-bind-try",
            "/usr/lib64",
            "/usr/lib64",
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--size",
            "67108864",
            "--tmpfs",
            "/tmp",
            "--size",
            "134217728",
            "--tmpfs",
            "/workspace",
            "--ro-bind-data",
            str(snapshot.fileno()),
            "/snapshot.tar",
            "--chdir",
            "/workspace",
            "--clearenv",
            "--setenv",
            "PATH",
            "/usr/bin:/bin",
            "--setenv",
            "HOME",
            "/tmp",
            "--setenv",
            "HARNESS_MCP_ARGV",
            json.dumps(server_argv),
            "/usr/bin/python3",
            "-I",
            "-c",
            bootstrap,
        ]
        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(snapshot.fileno(),),
            preexec_fn=_limits,  # noqa: PLW1509 - no threads exist before Popen
            start_new_session=True,
        )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    child_stdin = process.stdin
    child_stdout = process.stdout
    child_stderr = process.stderr
    stopped = threading.Event()

    def send_input() -> None:
        try:
            while chunk := os.read(0, 8192):
                child_stdin.write(chunk)
                child_stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            stopped.set()

    def forward_output() -> None:
        total = 0
        while chunk := os.read(child_stdout.fileno(), 8192):
            total += len(chunk)
            if total > MAX_PROTOCOL_BYTES:
                stopped.set()
                return
            try:
                view = memoryview(chunk)
                while view:
                    view = view[os.write(1, view) :]
            except (BrokenPipeError, OSError):
                stopped.set()
                return

    def drain_errors() -> None:
        total = 0
        while chunk := os.read(child_stderr.fileno(), 8192):
            total += len(chunk)
            if total > 65536:
                stopped.set()
                return

    for target in (send_input, forward_output, drain_errors):
        threading.Thread(target=target, daemon=True).start()
    deadline = time.monotonic() + 600
    while process.poll() is None and not stopped.is_set() and time.monotonic() < deadline:
        time.sleep(0.05)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


if __name__ == "__main__":
    main()
