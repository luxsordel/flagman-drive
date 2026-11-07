import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

# Logs must not carry personal data or secrets (docs/03-security.md). Call sites log ids only;
# this is the safety net for a slip. An explicit list rather than a pattern: 'code' is a fine-type
# or error code in our logs, and the dev SMS stub logs the sign-in code on purpose.
_REDACTED_KEYS = frozenset(
    {
        "phone",
        "email",
        "password",
        "token",
        "access_token",
        "refresh_token",
        "challenge_token",
        "otp",
        "totp",
        "totp_secret",
        "secret",
        "document_number",
        "first_name",
        "last_name",
        "birth_date",
        # Coordinates show where a client drives, and we keep no client IPs (Caddy drops them).
        "lat",
        "lon",
        "location",
        "ip",
        "authorization",
        "cookie",
        "set_cookie",
    }
)
_REDACTED = "[redacted]"


def _is_redacted(key: object) -> bool:
    return isinstance(key, str) and key.lower().replace("-", "_") in _REDACTED_KEYS


def _redact_personal_data(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key, value in event_dict.items():
        if _is_redacted(key):
            event_dict[key] = _REDACTED
        elif isinstance(value, dict):
            # One level is enough for what we log (headers, details); a copy keeps the caller's
            # dict intact.
            event_dict[key] = {k: _REDACTED if _is_redacted(k) else v for k, v in value.items()}
    return event_dict


# Processors shared by structlog loggers and by stdlib loggers of libraries (uvicorn, sqlalchemy),
# so everything ends up as one JSON object per line with the same fields.
_SHARED_PROCESSORS: list[structlog.typing.Processor] = [
    structlog.contextvars.merge_contextvars,
    _redact_personal_data,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]

# uvicorn logs every WebSocket handshake as '<client ip:port> - "WebSocket <path>" <outcome>'.
# Behind Caddy that is the real address of the client from X-Forwarded-For, and we keep no IPs
# in logs (Caddy drops them too). The path comes with its query string, which may carry a token
# a client put there, so only the path is kept, as RequestContextMiddleware does for HTTP.
_WEBSOCKET_HANDSHAKE_PREFIX = '%s - "WebSocket '


def _drop_websocket_client_address(record: logging.LogRecord) -> bool:
    if isinstance(record.msg, str) and record.msg.startswith(_WEBSOCKET_HANDSHAKE_PREFIX):
        match record.args:
            case (_, str() as path, *rest):
                record.msg = record.msg.removeprefix("%s - ")
                record.args = (path.partition("?")[0], *rest)
            case _:
                pass
    return True


def configure_logging(level: str) -> None:
    structlog.configure(
        processors=[*_SHARED_PROCESSORS, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn installs its own handlers; route them through ours. Its access log is replaced
    # by RequestContextMiddleware, which logs the path without the query string.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    uvicorn_error = logging.getLogger("uvicorn.error")
    if _drop_websocket_client_address not in uvicorn_error.filters:
        uvicorn_error.addFilter(_drop_websocket_client_address)
    logging.getLogger("uvicorn.access").disabled = True
