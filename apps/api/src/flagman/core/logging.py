import logging
import sys

import structlog

# Processors shared by structlog loggers and by stdlib loggers of libraries (uvicorn, sqlalchemy),
# so everything ends up as one JSON object per line with the same fields.
_SHARED_PROCESSORS: list[structlog.typing.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]

# uvicorn logs every WebSocket handshake as '<client ip:port> - "WebSocket <path>" <outcome>'.
# Behind Caddy that is the real address of the client from X-Forwarded-For, and we keep no IPs
# in logs (Caddy drops them too).
_WEBSOCKET_HANDSHAKE_PREFIX = '%s - "WebSocket '


def _drop_websocket_client_address(record: logging.LogRecord) -> bool:
    if (
        isinstance(record.msg, str)
        and record.msg.startswith(_WEBSOCKET_HANDSHAKE_PREFIX)
        and isinstance(record.args, tuple)
        and record.args
    ):
        record.msg = record.msg.removeprefix("%s - ")
        record.args = record.args[1:]
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
