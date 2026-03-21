"""Docker container integration tests for Transit Tracker.


Builds the transit-tracker image, starts a container, and verifies:
- WebSocket heartbeat on port 8000
- /api/spec returns valid JSON on port 8080

Requires Docker. Run with: pytest -m docker tests/test_container.py

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5
"""

import json
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest
import websockets.sync.client

pytestmark = pytest.mark.e2e

def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False

if not _docker_available():
    pytest.skip("Docker not available", allow_module_level=True)
IMAGE_NAME = "transit-tracker"
CONTAINER_NAME = "transit-tracker-test"
WS_HOST_PORT = 18000
HTTP_HOST_PORT = 18080
CONFIG_MOUNT = ".local/home.yaml"
STARTUP_TIMEOUT = 60  # seconds


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def docker_image():
    """Build the Docker image. Yields the image name, no cleanup of image."""
    result = subprocess.run(
        ["docker", "build", "-t", IMAGE_NAME, "."],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"Docker build failed:\n{result.stderr}"
    yield IMAGE_NAME


@pytest.fixture(scope="module")
def docker_container(docker_image):
    """Start the container with unique test ports. Yields once ready, cleans up on exit."""
    # Remove any leftover test container
    subprocess.run(
        ["docker", "rm", "-f", CONTAINER_NAME],
        capture_output=True,
    )

    run_cmd = [
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "-p", f"{WS_HOST_PORT}:8000",
        "-p", f"{HTTP_HOST_PORT}:8080",
    ]
    # Only mount config if it exists (CI environments won't have it)
    config_path = Path(CONFIG_MOUNT)
    if config_path.exists():
        run_cmd += ["-v", f"{config_path.resolve()}:/config/config.yaml:ro"]
    run_cmd.append(docker_image)
    result = subprocess.run(run_cmd, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Container start failed:\n{result.stderr}"

    # Wait for WebSocket port to accept connections
    deadline = time.time() + STARTUP_TIMEOUT
    ready = False
    while time.time() < deadline:
        try:
            with websockets.sync.client.connect(
                f"ws://localhost:{WS_HOST_PORT}",
                open_timeout=2,
                close_timeout=1,
            ):
                ready = True
                break
        except Exception:
            time.sleep(1)

    if not ready:
        logs = subprocess.run(
            ["docker", "logs", CONTAINER_NAME],
            capture_output=True, text=True,
        )
        # Clean up before failing
        subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)
        pytest.fail(
            f"Container did not accept WS connections within {STARTUP_TIMEOUT}s.\n"
            f"Logs:\n{logs.stdout}\n{logs.stderr}"
        )

    yield CONTAINER_NAME

    # Cleanup: stop and remove container
    subprocess.run(["docker", "stop", CONTAINER_NAME], capture_output=True, timeout=30)
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True, timeout=10)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.docker
def test_image_builds(docker_image):
    """Requirement 5.1: Dockerfile builds without errors."""
    result = subprocess.run(
        ["docker", "image", "inspect", docker_image],
        capture_output=True,
    )
    assert result.returncode == 0, "Built image not found"


@pytest.mark.docker
def test_websocket_heartbeat(docker_container):
    """Requirements 5.2, 5.3: WebSocket accepts connections and sends heartbeat."""
    with websockets.sync.client.connect(
        f"ws://localhost:{WS_HOST_PORT}",
        open_timeout=5,
        close_timeout=2,
    ) as ws:
        # Heartbeat is sent every 10s; wait up to 60s to receive one
        deadline = time.time() + 60
        heartbeat_received = False
        while time.time() < deadline:
            try:
                raw = ws.recv(timeout=15)
                msg = json.loads(raw)
                if msg.get("event") == "heartbeat":
                    heartbeat_received = True
                    break
            except TimeoutError:
                continue

        assert heartbeat_received, "Did not receive a heartbeat event within 60s"


@pytest.mark.docker
def test_openapi_returns_json(docker_container):
    """Requirement 5.4: /api/spec returns valid JSON."""
    resp = httpx.get(f"http://localhost:{HTTP_HOST_PORT}/api/spec", timeout=10)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()  # raises if not valid JSON
    assert isinstance(data, dict), "/api/spec should return a JSON object"
