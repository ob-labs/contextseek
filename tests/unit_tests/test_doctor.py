"""Tests for the ``contextseek doctor`` diagnostics command."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO

from contextseek.cli.main import build_parser
from contextseek.config.factory import (
    DoctorCheck,
    redact_diagnostic_text,
    run_config_diagnostics,
)
from contextseek.config.settings import (
    ContextSeekSettings,
    EmbeddingSettings,
    LLMSettings,
    StorageSettings,
)


def _settings(
    *,
    storage: StorageSettings | None = None,
    embedding: EmbeddingSettings | None = None,
    llm: LLMSettings | None = None,
) -> ContextSeekSettings:
    return ContextSeekSettings(
        storage=storage or StorageSettings(backend="memory"),
        embedding=embedding or EmbeddingSettings(provider="none"),
        llm=llm or LLMSettings(provider="none"),
    )


def test_doctor_parser_accepts_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["doctor"])

    assert args.command == "doctor"


def test_default_diagnostics_pass_storage_and_skip_optional_models() -> None:
    checks = run_config_diagnostics(_settings())
    by_component = {check.component: check for check in checks}

    assert by_component["config"].status == "PASS"
    assert by_component["storage"].status == "PASS"
    assert by_component["embedding"].status == "SKIP"
    assert by_component["llm"].status == "SKIP"


def test_diagnostics_report_storage_configuration_failure() -> None:
    checks = run_config_diagnostics(
        _settings(storage=StorageSettings(backend="oceanbase"))
    )
    storage = next(check for check in checks if check.component == "storage")

    assert storage.status == "FAIL"
    assert "EMBEDDING_DIMS" in storage.summary
    assert ".env.example" in storage.hint


def test_redacts_secret_values_from_diagnostic_text(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")

    redacted = redact_diagnostic_text("provider rejected key sk-test-secret")

    assert "sk-test-secret" not in redacted
    assert "***" in redacted


def test_redacts_url_credentials_and_secret_query_params() -> None:
    redacted = redact_diagnostic_text(
        "request failed for https://user:pass@example.test/v1?api_key=plain-secret"
    )

    assert "user:pass" not in redacted
    assert "plain-secret" not in redacted
    assert "https://***@example.test/v1?api_key=***" in redacted


def test_diagnostics_report_resolved_model_classes(monkeypatch) -> None:
    from contextseek.config import factory

    monkeypatch.setattr(factory, "build_embedder", lambda settings: lambda text: [0.0])
    monkeypatch.setattr(factory, "build_llm", lambda settings: object())
    monkeypatch.setattr(factory, "_invoke_llm_probe", lambda llm: "OK")

    checks = factory.run_config_diagnostics(
        _settings(
            embedding=EmbeddingSettings(
                provider="openai",
                model="text-embedding-3-small",
            ),
            llm=LLMSettings(
                provider="openai",
                model="gpt-4o-mini",
            ),
        )
    )
    by_component = {check.component: check for check in checks}

    assert by_component["embedding"].status == "PASS"
    assert (
        "class_path=langchain_openai.OpenAIEmbeddings"
        in by_component["embedding"].summary
    )
    assert "dims=1536" in by_component["embedding"].summary
    assert by_component["llm"].status == "PASS"
    assert "class_path=langchain_openai.ChatOpenAI" in by_component["llm"].summary


def test_doctor_command_returns_nonzero_on_failed_check(monkeypatch) -> None:
    from contextseek.cli import doctor

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setattr(
        doctor,
        "run_config_diagnostics",
        lambda settings: [
            DoctorCheck(
                component="config",
                status="PASS",
                summary="loaded ContextSeekSettings",
            ),
            DoctorCheck(
                component="embedding",
                status="FAIL",
                summary="provider rejected key sk-test-secret",
                hint="Check EMBEDDING_* in .env.example.",
            ),
        ],
    )
    out = StringIO()

    with redirect_stdout(out):
        code = doctor.run_doctor(_settings())

    rendered = out.getvalue()
    assert code == 1
    assert "FAIL embedding" in rendered
    assert "sk-test-secret" not in rendered
    assert "Check EMBEDDING_* in .env.example." in rendered
