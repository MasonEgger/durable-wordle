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
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _port_busy(port: int) -> bool:
    """Return True if a TCP port is already accepting connections on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _find_free_port() -> int:
    """Ask the OS for an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(check: Callable[[], bool], timeout: float, interval: float = 0.5) -> bool:
    """Poll ``check`` (a no-arg callable) until it is truthy or the timeout lapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(interval)
    return False


def _health_ok(app_port: int) -> bool:
    """Return True once the web app's health endpoint responds 200."""
    try:
        with urllib.request.urlopen(
            f"http://localhost:{app_port}/health", timeout=1
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

    temporal_port = _find_free_port()
    app_port = _find_free_port()
    for port in (temporal_port, app_port):
        if _port_busy(port):
            pytest.skip(f"port {port} was claimed before the test stack started")

    db_dir = tempfile.mkdtemp(prefix="wordle-e2e-")
    env = {
        **os.environ,
        "DURABLE_WORDLE_DB": str(Path(db_dir) / "leaderboard.db"),
        "TEMPORAL_ADDRESS": f"localhost:{temporal_port}",
        "TEMPORAL_NAMESPACE": "default",
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
        _spawn("temporal", "server", "start-dev", "--port", str(temporal_port))
        if not _wait_for(
            lambda: (
                subprocess.run(
                    [
                        "temporal",
                        "operator",
                        "cluster",
                        "health",
                        "--address",
                        f"localhost:{temporal_port}",
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
            str(app_port),
        )
        if not _wait_for(lambda: _health_ok(app_port), timeout=45):
            pytest.skip("web app did not become healthy in time")

        yield f"http://localhost:{app_port}"
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
