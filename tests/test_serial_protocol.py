import json
from unittest.mock import MagicMock, patch

import pytest

from transit_tracker.hardware import EntityType, ESPHomeFlasher


def test_esphome_flasher_protocol():
    """Test that ESPHomeFlasher correctly formats JRPC messages over serial."""
    with patch("transit_tracker.hardware.serial.Serial") as MockSerial:
        mock_serial_instance = MagicMock()
        MockSerial.return_value = mock_serial_instance
        
        # Setup mock to return a valid JSON-RPC response when readline is called
        def mock_readline():
            # Match the request id 1
            response = {"jsonrpc": "2.0", "id": 1, "result": {"success": True}}
            return f"JRPC:{json.dumps(response)}\r\n".encode("utf-8")
            
        mock_serial_instance.readline.side_effect = mock_readline

        with ESPHomeFlasher("/dev/tty.mock") as flasher:
            success = flasher.set_entity("base_url_config", EntityType.TEXT, "wss://test.url")
            
            assert success is True
            
            # Verify the exact bytes written
            mock_serial_instance.write.assert_called_once()
            written_bytes = mock_serial_instance.write.call_args[0][0]
            written_str = written_bytes.decode("utf-8")
            
            assert written_str.startswith("JRPC:")
            assert written_str.endswith("\r\n")
            
            # Extract JSON part
            json_part = written_str[5:-2]
            payload = json.loads(json_part)
            
            assert payload["jsonrpc"] == "2.0"
            assert payload["method"] == "entity.set"
            assert payload["params"]["id"] == "base_url_config"
            assert payload["params"]["type"] == EntityType.TEXT
            assert payload["params"]["value"] == "wss://test.url"

if __name__ == "__main__":
    pytest.main([__file__])
