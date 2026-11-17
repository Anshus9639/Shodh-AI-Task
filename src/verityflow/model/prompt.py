"""Versioned reviewer prompt construction.

The prompt has exactly two parts: a fixed system instruction for a known prompt
version and one delimited payload. The payload is serialized JSON in which angle
brackets are escaped, so artifact text can never close the untrusted block.
"""

from __future__ import annotations

import json
from pathlib import Path

from verityflow.model.models import ModelRequest

PROMPT_VERSION = "reviewer-v1"

PAYLOAD_OPEN = "<untrusted_artifact_payload>"
PAYLOAD_CLOSE = "</untrusted_artifact_payload>"

_PROMPT_DIR = Path(__file__).with_name("prompts")
_VERSION_PATTERN = "reviewer-v"


class PromptError(ValueError):
    """The prompt version is unknown or its template is unusable."""


def prompt_path(version: str) -> Path:
    if not version.startswith(_VERSION_PATTERN):
        raise PromptError(f"unknown prompt version {version!r}")
    name = version.replace("-", "_")
    path = _PROMPT_DIR / f"{name}.txt"
    if not path.is_file():
        raise PromptError(f"prompt version {version!r} has no template")
    return path


def escape_payload(text: str) -> str:
    """Escape angle brackets so untrusted text cannot reproduce the delimiters."""
    return text.replace("<", "\\u003c").replace(">", "\\u003e")


def request_payload(request: ModelRequest) -> str:
    body = json.dumps(
        request.model_dump(mode="json"),
        indent=2,
        ensure_ascii=False,
        sort_keys=False,
    )
    return escape_payload(body)


def render_prompt(request: ModelRequest) -> str:
    template = prompt_path(request.prompt_version).read_text(encoding="utf-8")
    payload = request_payload(request)
    if PAYLOAD_OPEN in payload or PAYLOAD_CLOSE in payload:
        raise PromptError("escaped payload still contains the untrusted delimiters")
    rendered = template.replace("{{PROMPT_VERSION}}", request.prompt_version)
    return rendered.replace("{{PAYLOAD}}", payload)


def system_instruction(request: ModelRequest) -> str:
    """The fixed instruction part of the prompt, without the untrusted payload."""
    template = prompt_path(request.prompt_version).read_text(encoding="utf-8")
    rendered = template.replace("{{PROMPT_VERSION}}", request.prompt_version)
    head, _, _ = rendered.partition(PAYLOAD_OPEN)
    return head.rstrip()


__all__ = [
    "PAYLOAD_CLOSE",
    "PAYLOAD_OPEN",
    "PROMPT_VERSION",
    "PromptError",
    "escape_payload",
    "prompt_path",
    "render_prompt",
    "request_payload",
    "system_instruction",
]
