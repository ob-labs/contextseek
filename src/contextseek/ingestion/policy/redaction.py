"""Ingestion-specific redaction utilities."""

from __future__ import annotations

import re
from typing import Iterable

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b1[3-9]\d{9}\b")
_TOKEN_RE = re.compile(
    r"\b(?:sk|api|key|token|secret)[\w-]{0,8}[_-][A-Za-z0-9]{8,}\b", re.IGNORECASE
)


def redact_text(text: str, *, token: str = "[REDACTED]") -> str:
    text = _EMAIL_RE.sub(token, text)
    text = _PHONE_RE.sub(token, text)
    return _TOKEN_RE.sub(token, text)


def mask_sensitive_paths(text: str, *, mask: str = "/redacted/path") -> str:
    # Keep the rule intentionally narrow to avoid over-redaction.
    text = re.sub(r"/home/[^/\s]+(?:/[^\s]+)?", mask, text)
    text = re.sub(r"[A-Za-z]:\\Users\\[^\\\s]+(?:\\[^\s]+)?", mask, text)
    return text


def redact_principals(principals: Iterable[str]) -> list[str]:
    result: list[str] = []
    for principal in principals:
        if principal.startswith("user:"):
            result.append(principal)
        elif principal.startswith("group:"):
            result.append(principal)
    return result

