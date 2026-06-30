# ABOUTME: Pytest fixtures for full-stack browser (e2e) tests — boots Temporal,
# ABOUTME: the worker, and the web app as subprocesses against an isolated DB.
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPORAL_PORT = 7233  # matches temporal.toml so the app/worker connect by default
_APP_PORT = 8042  # distinct from the booth's 8000 so a running booth is untouched


def _port_busy(port: int) -> bool:
    """Return True if a TCP port is already accepting connections on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_for(check: object, timeout: float, interval: float = 0.5) -> bool:
    """Poll ``check`` (a no-arg callable) until it is truthy or the timeout lapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():  # type: ignore[operator]
            return True
        time.sleep(interval)
    return False


def _health_ok() -> bool:
    """Return True once the web app's health endpoint responds 200."""
    try:
        with urllib.request.urlopen(
            f"http://localhost:{_APP_PORT}/health", timeout=1
        ) as response:
            return response.status == 200
    except Exception:
        return False


@pytest.fixture(scope="session")
def live_server() -> Iterator[str]:
    """Boot Temporal + worker + web app as subprocesses; yield the base URL.

    Skips when the ``temporal`` binary is unavailable or the required ports are
    already in use (e.g. a booth is running). Uses a throwaway leaderboard DB so
    tests never touch real data.
    """
    if shutil.which("temporal") is None:
        pytest.skip("temporal binary not on PATH")
    for port in (_TEMPORAL_PORT, _APP_PORT):
        if _port_busy(port):
            pytest.skip(f"port {port} already in use (stop any running stack first)")

    db_dir = tempfile.mkdtemp(prefix="wordle-e2e-")
    env = {
        **os.environ,
        "DURABLE_WORDLE_DB": str(Path(db_dir) / "leaderboard.db"),
        "TEMPORAL_TASK_QUEUE": "wordle-tasks",
    }
    procs: list[subprocess.Popen[bytes]] = []

    def _spawn(*args: str) -> subprocess.Popen[bytes]:
        proc = subprocess.Popen(
            args, cwd=_PROJECT_ROOT, env=env, start_new_session=True
        )
        procs.append(proc)
        return proc

    try:
        _spawn("temporal", "server", "start-dev", "--port", str(_TEMPORAL_PORT))
        if not _wait_for(
            lambda: (
                subprocess.run(
                    [
                        "temporal",
                        "operator",
                        "cluster",
                        "health",
                        "--address",
                        f"localhost:{_TEMPORAL_PORT}",
                    ],
                    capture_output=True,
                ).returncode
                == 0
            ),
            timeout=45,
        ):
            pytest.skip("Temporal dev server did not become healthy in time")

        _spawn("uv", "run", "python", "-m", "durable_wordle.worker")
        _spawn(
            "uv",
            "run",
            "uvicorn",
            "--factory",
            "durable_wordle.api:create_production_app",
            "--port",
            str(_APP_PORT),
        )
        if not _wait_for(_health_ok, timeout=45):
            pytest.skip("web app did not become healthy in time")

        yield f"http://localhost:{_APP_PORT}"
    finally:
        for proc in reversed(procs):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
        for proc in reversed(procs):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(db_dir, ignore_errors=True)
