"""(C) Copyright 2026, by Ross Richardson

Bounded local Java execution for maintainer proofs, not a production sandbox.

@author ross richardson
"""

import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time


def require_local_runtime():
    """Native H2 AUTO_SERVER needs a local socket even in a headless run."""
    if shutil.which("java") is None:
        raise RuntimeError("Java is required")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
    except OSError as error:
        raise RuntimeError("Native H2 needs permission to open a local socket. "
                           "Run this proof in a laptop terminal or permitted test environment.") from error


def java_command(jar, main_class, *arguments, heap="2g"):
    java = shutil.which("java")
    if java is None:
        raise RuntimeError("Java is required")
    return [str(Path(java).resolve()), "-Xmx" + heap, "-XX:ActiveProcessorCount=2",
            "-XX:+ExitOnOutOfMemoryError", "-Djava.awt.headless=true",
            "-Djava.io.tmpdir=tmp", "-cp", str(Path(jar).resolve()), main_class,
            *arguments]


def run_java(command, workspace, log, timeout_seconds, max_log_bytes=16 * 1024 * 1024):
    """Private log, minimal environment, deadline and process-group cleanup."""
    if timeout_seconds <= 0:
        raise ValueError("A positive execution deadline is required")
    workspace = Path(workspace)
    (workspace / "tmp").mkdir(mode=0o700, exist_ok=True)
    started = time.monotonic()
    next_progress = started + 30
    with Path(log).open("xb", buffering=0) as output:
        os.chmod(log, 0o600)
        process = subprocess.Popen(command, cwd=workspace, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True,
                                   env={"LANG": "C.UTF-8", "TMPDIR": str(workspace / "tmp")})
        try:
            while process.poll() is None:
                if time.monotonic() - started > timeout_seconds:
                    raise RuntimeError("Java execution exceeded its deadline; see the private log")
                if os.fstat(output.fileno()).st_size > max_log_bytes:
                    raise RuntimeError("Java diagnostic limit exceeded; see the private log")
                if time.monotonic() >= next_progress:
                    print(f"Java still running ({int(time.monotonic() - started)} seconds)", flush=True)
                    next_progress = time.monotonic() + 30
                time.sleep(0.2)
            if process.returncode != 0:
                raise RuntimeError(f"Java exited with status {process.returncode}; see the private log")
        finally:
            # Also kill descendants after the direct process exits. A local proof
            # does not permit a detached child to retain its working directory.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    return round(time.monotonic() - started, 3)
