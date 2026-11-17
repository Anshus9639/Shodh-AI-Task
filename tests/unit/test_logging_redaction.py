from __future__ import annotations

import logging

from verityflow.logging_setup import RedactionFilter


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="verityflow.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )


def test_redacts_secret_looking_values() -> None:
    record = _record("calling provider with api_key=ABC123")
    assert RedactionFilter().filter(record) is True
    assert "ABC123" not in record.getMessage()
    assert "REDACTED" in record.getMessage()


def test_leaves_ordinary_messages_untouched() -> None:
    record = _record("bundle B01 reviewed")
    RedactionFilter().filter(record)
    assert record.getMessage() == "bundle B01 reviewed"
