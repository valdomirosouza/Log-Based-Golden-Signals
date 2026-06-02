import logging
import os


def configure_logging() -> None:
    """Configure JSON structured logging for the service."""
    from pythonjsonlogger import jsonlogger

    service = os.getenv("SERVICE_NAME", "analytics_api")

    class _ServiceFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.service = service
            return True

    handler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(service)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    handler.setFormatter(formatter)
    handler.addFilter(_ServiceFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
