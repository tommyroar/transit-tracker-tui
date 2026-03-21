"""Unit tests for the logging module.


Tests JSON and Pretty formatters, message logging toggle,
and auto-setup behaviour.
"""

import json
import logging

import pytest

from transit_tracker.logging import (
    JSONFormatter,
    PrettyFormatter,
    is_message_logging_enabled,
    set_message_logging,
)

pytestmark = pytest.mark.unit


def _make_record(
    msg="test message", level="INFO", name="transit_tracker.test", **extra
):
    """Create a LogRecord with optional extra fields."""
    record = logging.LogRecord(
        name=name,
        level=getattr(logging, level),
        pathname="test.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


# -- JSONFormatter -----------------------------------------------------------


class TestJSONFormatter:
    def test_basic_output(self):
        fmt = JSONFormatter()
        record = _make_record()
        result = json.loads(fmt.format(record))
        assert "ts" in result
        assert result["level"] == "INFO"
        assert result["logger"] == "transit_tracker.test"
        assert result["msg"] == "test message"

    def test_with_exception(self):
        fmt = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = _make_record()
            record.exc_info = sys.exc_info()
        result = json.loads(fmt.format(record))
        assert "exc" in result
        assert "boom" in result["exc"]

    def test_extra_fields(self):
        fmt = JSONFormatter()
        record = _make_record(component="server", stop_id="1_8494")
        result = json.loads(fmt.format(record))
        assert result["component"] == "server"
        assert result["stop_id"] == "1_8494"

    def test_missing_extra_fields_excluded(self):
        fmt = JSONFormatter()
        record = _make_record()
        result = json.loads(fmt.format(record))
        assert "component" not in result
        assert "stop_id" not in result


# -- PrettyFormatter ---------------------------------------------------------


class TestPrettyFormatter:
    def test_info_no_level_prefix(self):
        fmt = PrettyFormatter()
        result = fmt.format(_make_record(level="INFO"))
        assert "[TEST]" in result
        assert "WARN" not in result
        assert "ERR" not in result

    def test_warning_prefix(self):
        fmt = PrettyFormatter()
        result = fmt.format(_make_record(level="WARNING"))
        assert "WARN" in result

    def test_error_prefix(self):
        fmt = PrettyFormatter()
        result = fmt.format(_make_record(level="ERROR"))
        assert "ERR" in result

    def test_component_tag(self):
        fmt = PrettyFormatter()
        result = fmt.format(_make_record(component="server"))
        assert "[SERVER]" in result


# -- Message logging toggle --------------------------------------------------


class TestMessageLogging:
    def test_toggle_on_off(self):
        original = is_message_logging_enabled()
        set_message_logging(True)
        assert is_message_logging_enabled() is True
        set_message_logging(False)
        assert is_message_logging_enabled() is False
        # Restore
        set_message_logging(original)
