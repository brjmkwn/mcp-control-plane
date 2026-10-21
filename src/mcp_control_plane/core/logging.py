"""Structured JSON audit logger with automatic secret redaction."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

SENSITIVE_KEYS = {
    "authorization",
    "token",
    "access_token",
    "secret",
    "password",
    "confirmation_token",
    "api_key",
    "github_token",
}


def _redact_value(val: Any) -> Any:
    """Mask sensitive string or recurse into dict/list."""
    if isinstance(val, str):
        if len(val) > 8:
            return val[:3] + "..." + val[-3:]
        return "********"
    if isinstance(val, dict):
        return {
            k: ("********" if k.lower() in SENSITIVE_KEYS else _redact_value(v))
            for k, v in val.items()
        }
    if isinstance(val, list):
        return [_redact_value(item) for item in val]
    return val


def redact_secrets_processor(
    logger: logging.Logger,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Structlog processor to mask sensitive keys in log events."""
    for key, val in list(event_dict.items()):
        if any(sensitive in key.lower() for sensitive in SENSITIVE_KEYS):
            event_dict[key] = "********"
        elif isinstance(val, (dict, list)):
            event_dict[key] = _redact_value(val)
    return event_dict


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structlog for standard JSON output with secret redaction."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redact_secrets_processor,
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "mcp_control_plane") -> structlog.stdlib.BoundLogger:
    """Get configured bound logger instance."""
    return structlog.get_logger(name)
