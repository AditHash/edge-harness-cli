# mypy: disable-error-code=attr-defined
"""WSL-side sandbox supervisor; invoked as trusted code, never with model shell on the host."""

import base64
import json
import os
import resource
import signal
import subprocess
import sys
import threading
import time

OUTPUT_LIMIT = 65536


def _limits() -> None:
    for kind, amount in (
        (resource.RLIMIT_AS, 768 * 1024 * 1024),
        (resource.RLIMIT_CPU, 30),
        (resource.RLIMIT_NPROC, 64),
        (resource.RLIMIT_NOFILE, 128),
        (resource.RLIMIT_FSIZE, 32 * 1024 * 1024),
    ):
        resource.setrlimit(kind, (amount, amount))


def main() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("The WSL sandbox requires a non-root distribution user")
    request = json.loads(sys.stdin.buffer.readline())
    archive = base64.b64decode(request["archive"], validate=True)
    command = request["command"]
    timeout = request["timeout"]
    bootstrap = (
        "import os,sys,tarfile; "
        "t=tarfile.open(fileobj=sys.stdin.buffer,mode='r|'); "
        "t.extractall('/workspace',filter='data'); t.close(); "
        "c=os.environ.pop('HARNESS_COMMAND'); os.execv('/bin/sh',['sh','-c',c])"
    )
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
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--setenv",
        "HARNESS_COMMAND",
        command,
        "/usr/bin/python3",
        "-I",
        "-c",
        bootstrap,
    ]
    start = time.monotonic()
    process = subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=_limits,  # noqa: PLW1509 - this helper starts threads only after spawning
        start_new_session=True,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    child_stdin = process.stdin
    canceled = threading.Event()
    overflow = threading.Event()
    stdout = bytearray()
    stderr = bytearray()

    def watch_parent() -> None:
        if not os.read(0, 1):
            canceled.set()

    def feed_archive() -> None:
        try:
            child_stdin.write(archive)
            child_stdin.close()
        except (BrokenPipeError, OSError):
            pass

    def collect(pipe, output: bytearray) -> None:
        while chunk := pipe.read(8192):
            remaining = OUTPUT_LIMIT - len(output)
            output.extend(chunk[: max(0, remaining)])
            if len(chunk) > remaining:
                overflow.set()
                break

    threads = [
        threading.Thread(target=watch_parent, daemon=True),
        threading.Thread(target=feed_archive, daemon=True),
        threading.Thread(target=collect, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=collect, args=(process.stderr, stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    reason = "completed"
    while process.poll() is None:
        if canceled.is_set():
            reason = "canceled"
        elif overflow.is_set():
            reason = "output_limit"
        elif time.monotonic() - start > timeout:
            reason = "timeout"
        if reason != "completed":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break
        time.sleep(0.05)
    process.wait()
    for thread in threads[1:]:
        thread.join(timeout=2)
    if overflow.is_set() and reason == "completed":
        reason = "output_limit"
    result = {
        "exit_code": process.returncode,
        "stdout": stdout.decode("utf-8", "replace"),
        "stderr": stderr.decode("utf-8", "replace"),
        "reason": reason,
        "duration_seconds": round(time.monotonic() - start, 3),
    }
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
