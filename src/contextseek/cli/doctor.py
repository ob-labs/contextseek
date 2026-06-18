"""``contextseek doctor`` diagnostics command."""

from __future__ import annotations

from contextseek.config.factory import redact_diagnostic_text, run_config_diagnostics
from contextseek.config.settings import ContextSeekSettings


def run_doctor(settings: ContextSeekSettings | None = None) -> int:
    """Run configuration diagnostics and return a process exit code."""
    try:
        effective_settings = settings or ContextSeekSettings()
    except Exception as exc:  # noqa: BLE001 - doctor should report config load errors.
        print(f"FAIL config: {redact_diagnostic_text(str(exc))}")
        print("  hint: Check CONTEXTSEEK_CONFIG or .env; see .env.example.")
        return 1

    checks = run_config_diagnostics(effective_settings)
    failed = False
    for check in checks:
        if check.status == "FAIL":
            failed = True
        summary = redact_diagnostic_text(check.summary, effective_settings)
        print(f"{check.status} {check.component}: {summary}")
        if check.hint:
            print(f"  hint: {redact_diagnostic_text(check.hint, effective_settings)}")
    return 1 if failed else 0


__all__ = ["run_doctor"]
