"""Policy helpers for ingestion."""

from contextseek.ingestion.policy.gate import DefaultPolicyGate, GateConfig
from contextseek.ingestion.policy.redaction import (
    mask_sensitive_paths,
    redact_principals,
    redact_text,
)

__all__ = [
    "DefaultPolicyGate",
    "GateConfig",
    "redact_text",
    "mask_sensitive_paths",
    "redact_principals",
]

