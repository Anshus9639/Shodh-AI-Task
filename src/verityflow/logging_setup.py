from __future__ import annotations

import logging
import re

_SECRET_PATTERNS = (re.compile(r"(?i)\b(api[_-]?key|authorization|token|secret)\b\s*[:=]\s*\S+"),)


class RedactionFilter(logging.Filter):
    """Replaces secret-looking values in log records before they are emitted."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(r"\1=[REDACTED]", redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
