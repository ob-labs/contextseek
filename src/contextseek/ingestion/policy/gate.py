"""Policy gate for ACL/scope/redaction checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from contextseek.ingestion.models import PolicyDecision, RawEvent
from contextseek.ingestion.policy.redaction import (
    mask_sensitive_paths,
    redact_principals,
    redact_text,
)


@dataclass(slots=True)
class GateConfig:
    policy_version: str = "ingestion-v1"
    redact_pii: bool = True
    redact_paths: bool = False
    reject_empty_content: bool = True
    allowed_scopes: tuple[str, ...] = ()
    default_private_principal: str = "user:connector_owner"


class DefaultPolicyGate:
    def __init__(self, config: GateConfig | None = None) -> None:
        self._config = config or GateConfig()

    @property
    def policy_version(self) -> str:
        return self._config.policy_version

    def _scope_allowed(self, scope: str) -> bool:
        if not self._config.allowed_scopes:
            return True
        return any(scope.startswith(prefix) for prefix in self._config.allowed_scopes)

    def apply(self, event: RawEvent) -> RawEvent | None:
        decision = self.decide(event)
        event.metadata["policy_version"] = decision.policy_version
        event.metadata["policy_decision"] = decision.decision
        if decision.reason:
            event.metadata["policy_reason"] = decision.reason
        if decision.decision == "reject":
            return None
        return event

    def decide(self, event: RawEvent) -> PolicyDecision:
        if self._config.reject_empty_content and not event.content.strip():
            return PolicyDecision(
                decision="reject",
                policy_version=self._config.policy_version,
                reason="empty_content",
            )
        if not self._scope_allowed(event.scope):
            return PolicyDecision(
                decision="reject",
                policy_version=self._config.policy_version,
                reason="scope_not_allowed",
            )
        if not event.acl_principals:
            event.acl_principals = [self._config.default_private_principal]
        else:
            event.acl_principals = self._sanitize_acl(event.acl_principals)
        return self._redact_if_needed(event)

    def _redact_if_needed(self, event: RawEvent) -> PolicyDecision:
        redacted = False
        content = event.content
        if self._config.redact_pii:
            new_content = redact_text(content)
            redacted = redacted or new_content != content
            content = new_content
        if self._config.redact_paths:
            new_content = mask_sensitive_paths(content)
            redacted = redacted or new_content != content
            content = new_content
        event.content = content
        decision = "redact" if redacted else "allow"
        return PolicyDecision(
            decision=decision,
            policy_version=self._config.policy_version,
            redacted=redacted,
        )

    def _sanitize_acl(self, principals: Iterable[str]) -> list[str]:
        cleaned = redact_principals(principals)
        return cleaned or [self._config.default_private_principal]

