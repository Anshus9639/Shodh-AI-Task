from __future__ import annotations

from verityflow.models.common import TextSpan


class LineIndex:
    """Addressable line view of an artifact's text, used to verify evidence anchors."""

    __slots__ = ("_lines",)

    def __init__(self, text: str) -> None:
        self._lines = tuple(text.splitlines())

    @property
    def line_count(self) -> int:
        return len(self._lines)

    def line(self, number: int) -> str | None:
        if number < 1 or number > len(self._lines):
            return None
        return self._lines[number - 1]

    def excerpt(self, line_start: int, line_end: int) -> str | None:
        if line_start < 1 or line_end < line_start or line_end > len(self._lines):
            return None
        return "\n".join(self._lines[line_start - 1 : line_end])

    def check_span(self, span: TextSpan, *, expected_path: str) -> str | None:
        if span.path != expected_path:
            return f"anchor path {span.path!r} does not match artifact path {expected_path!r}"
        if span.line_start < 1 or span.line_end < span.line_start:
            return "anchor line range is empty or inverted"
        if span.line_end > len(self._lines):
            return f"anchor ends at line {span.line_end}, beyond {len(self._lines)} lines"
        text = self.excerpt(span.line_start, span.line_end)
        if text is None:
            return "anchor cannot be resolved to artifact text"
        if span.excerpt is not None and span.excerpt != text:
            return "anchor excerpt does not match the artifact text"
        line = self._lines[span.line_start - 1]
        if span.col_start is not None and not 1 <= span.col_start <= len(line) + 1:
            return "anchor column start is outside the line"
        if span.col_end is not None:
            if span.col_start is None or span.col_end < span.col_start:
                return "anchor column range is inverted"
            if span.col_end > len(line) + 1:
                return "anchor column end is outside the line"
        return None


__all__ = ["LineIndex"]
