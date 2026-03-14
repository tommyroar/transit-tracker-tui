import os
import tempfile

import httpx
import pytest

# The expected latest version we are testing against (from our research)
LATEST_VERSION = "v2.8.3"
REPO_OWNER = "EastsideUrbanism"
REPO_NAME = "transit-tracker"
FIRMWARE_ASSET_NAME = "firmware.factory.bin"

@pytest.fixture(scope="module")
def firmware_binary():
    """Downloads the latest firmware binary from GitHub for testing."""
    url = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/download/{LATEST_VERSION}/{FIRMWARE_ASSET_NAME}"
    
    # We use a persistent cache if possible to avoid redundant downloads during dev
    cache_path = os.path.join(tempfile.gettempdir(), f"transit_tracker_{LATEST_VERSION}_{FIRMWARE_ASSET_NAME}")
    
    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            return f.read()
            
    print(f"\nDownloading {url} for contract testing...")
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=30.0)
        resp.raise_for_status()
        content = resp.content
        
        with open(cache_path, "wb") as f:
            f.write(content)
        return content
    except Exception as e:
        pytest.skip(f"Could not download firmware for testing: {e}")

def test_firmware_size_contract(firmware_binary):
    """Verifies the binary size is within reasonable bounds for an ESP32-S3 factory image (usually ~1.4MB)."""
    size_mb = len(firmware_binary) / (1024 * 1024)
    assert 1.0 < size_mb < 4.0, f"Firmware size {size_mb:.2f}MB is outside expected 1-4MB range"

def test_esp32_binary_header_contract(firmware_binary):
    """
    Verifies the binary has a valid ESP32 image header.
    Format: 1 byte magic (0xE9), 1 byte segment count, 1 byte spi mode, 1 byte spi size etc.
    """
    assert firmware_binary[0] == 0xE9, "Binary missing ESP32 magic byte (0xE9)"
    
    # Check flash size (bits 4-7 of the 4th byte)
    # 0x0=1MB, 0x1=2MB, 0x2=4MB, 0x3=8MB, 0x4=16MB
    flash_size_code = (firmware_binary[3] & 0xF0) >> 4
    assert flash_size_code in [0x1, 0x2, 0x3, 0x4], f"Invalid flash size code: {flash_size_code}"

def test_firmware_identity_contract(firmware_binary):
    """Searches the binary for required identifying strings to ensure it's the right project."""
    required_strings = [
        b"Transit Tracker",
        b"ESPHome",
        b"v2.8.3"
    ]
    for s in required_strings:
        assert s in firmware_binary, f"Binary missing required identity string: {s.decode()}"

def test_protocol_strings_contract(firmware_binary):
    """Verifies the binary contains the JSON-RPC protocol markers it claims to support."""
    protocol_markers = [
        b"JRPC:",
        b"device.info",
        b"entity.get",
        b"entity.set",
        b"write_preferences"
    ]
    for marker in protocol_markers:
        assert marker in firmware_binary, f"Binary missing protocol marker: {marker.decode()}"

def test_config_entity_contract(firmware_binary):
    """Ensures the firmware contains the specific configuration entities the TUI expects to manage."""
    entities = [
        b"base_url_config",
        b"schedule_config",
        b"time_display_config",
        b"flip_display_config"
    ]
    for entity in entities:
        assert entity in firmware_binary, f"Binary missing expected config entity: {entity.decode()}"

if __name__ == "__main__":
    pytest.main([__file__])
