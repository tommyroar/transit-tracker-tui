"""Structured logging for Transit Tracker.

Configures Python's logging module with JSON-formatted output to stdout.
All modules should use ``get_logger(__name__)`` to obtain a logger instance.
"""

import json
import logging
import os
import sys


class JSONFormatter(logging.Formatter):
    """Emits each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc"] = self.formatException(record.exc_info)
        # Merge any extra fields attached via `extra={}` on log calls
        for key in ("component", "stop_id", "client", "event", "direction",
                     "interval", "pairs", "route", "detail"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        return json.dumps(entry, default=str)


class PrettyFormatter(logging.Formatter):
    """Human-readable formatter with component prefix tags."""

    LEVEL_PREFIX = {
        "DEBUG": "DBG",
        "INFO": "",
        "WARNING": "WARN",
        "ERROR": "ERR",
        "CRITICAL": "CRIT",
    }

    def format(self, record: logging.LogRecord) -> str:
        component = getattr(record, "component", None)
        if component:
            tag = f"[{component.upper()}]"
        else:
            tag = f"[{record.name.split('.')[-1].upper()}]"
        level = self.LEVEL_PREFIX.get(record.levelname, record.levelname)
        prefix = f"{tag} {level} " if level else f"{tag} "
        msg = record.getMessage()
        text = f"{prefix}{msg}"
        if record.exc_info and record.exc_info[0] is not None:
            text += "\n" + self.formatException(record.exc_info)
        return text


class _RingBufferHandler(logging.Handler):
    """Pushes each log record into the metrics log ring buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from .metrics import metrics  # deferred to avoid circular import
            entry = {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "component": getattr(record, "component", None),
            }
            metrics.logs.append(entry)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_configured = False
_message_logging_enabled = False


def setup_logging(
    level: str = "INFO",
    json_output: bool = False,
    message_logging: bool = False,
) -> None:
    """Configure the root transit_tracker logger.

    Parameters
    ----------
    level:
        Log level name (DEBUG, INFO, WARNING, ERROR).
    json_output:
        If True, emit structured JSON lines (for containers / log aggregators).
        If False, emit human-readable prefixed lines.
    message_logging:
        If True, enable DEBUG-level logging of all WebSocket messages
        sent and received.  Can also be toggled at runtime via
        ``set_message_logging()``.
    """
    global _configured, _message_logging_enabled
    _message_logging_enabled = message_logging

    root = logging.getLogger("transit_tracker")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter() if json_output else PrettyFormatter())
    root.addHandler(handler)

    # Ring-buffer handler: feeds log entries into the metrics log ring
    # so the /api/logs endpoint and dashboard can display them.
    root.addHandler(_RingBufferHandler())

    # Prevent propagation to the root logger (avoids duplicate output)
    root.propagate = False

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``transit_tracker`` namespace.

    If ``setup_logging()`` hasn't been called yet, a default configuration
    is applied automatically.
    """
    if not _configured:
        json_mode = os.environ.get("TT_LOG_JSON", "").lower() in ("1", "true", "yes")
        level = os.environ.get("TT_LOG_LEVEL", "INFO")
        msg_log = os.environ.get("TT_LOG_MESSAGES", "").lower() in ("1", "true", "yes")
        setup_logging(level=level, json_output=json_mode, message_logging=msg_log)
    return logging.getLogger(name)


def set_message_logging(enabled: bool) -> None:
    """Toggle WebSocket message logging at runtime."""
    global _message_logging_enabled
    _message_logging_enabled = enabled


def is_message_logging_enabled() -> bool:
    """Check whether verbose message logging is on."""
    return _message_logging_enabled
