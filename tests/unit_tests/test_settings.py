"""Tests for the unified pydantic-settings configuration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from contextseek.config.settings import (
    EmbeddingSettings,
    EvolutionSettings,
    LLMSettings,
    ObservabilitySettings,
    RetrievalSettings,
    ContextSeekSettings,
    StorageSettings,
    to_strategy_config,
)
from contextseek.config.factory import (
    _import_class,
    build_embedder,
    build_llm,
    resolve_embedding_dims,
)


# ---------------------------------------------------------------------------
# ContextSeekSettings construction
# ---------------------------------------------------------------------------


class TestContextSeekSettings:
    """Test settings model construction and defaults."""

    def test_default_construction(self, monkeypatch):
        """Zero-config construction succeeds with sensible defaults."""
        monkeypatch.delenv("STORAGE_BACKEND", raising=False)
        settings = ContextSeekSettings()
        assert settings.storage.backend == "sqlite"
        assert settings.embedding.provider == "none"
        assert settings.llm.provider == "none"
        assert settings.retrieval.default_k == 20
        assert settings.evolution.enabled is False
        assert settings.observability.audit_enabled is False

    def test_explicit_construction(self):
        """Explicit nested values are preserved."""
        settings = ContextSeekSettings(
            storage=StorageSettings(backend="file", path="/tmp/ctx"),
            embedding=EmbeddingSettings(
                provider="langchain",
                class_path="langchain_openai.OpenAIEmbeddings",
                model="text-embedding-3-small",
                dims=1536,
            ),
            retrieval=RetrievalSettings(default_k=50),
        )
        assert settings.storage.backend == "file"
        assert settings.storage.path == "/tmp/ctx"
        assert settings.embedding.provider == "langchain"
        assert settings.embedding.dims == 1536
        assert settings.retrieval.default_k == 50

    def test_env_override_case_insensitive(self, monkeypatch):
        """Env keys are matched case-insensitively (PowerMem-style)."""
        monkeypatch.setenv("storage_backend", "file")
        settings = ContextSeekSettings()
        assert settings.storage.backend == "file"

    def test_env_override(self, monkeypatch):
        """Environment variables override defaults."""
        monkeypatch.setenv("STORAGE_BACKEND", "file")
        monkeypatch.setenv("STORAGE_PATH", "/data/store")
        monkeypatch.setenv("RETRIEVAL_DEFAULT_K", "50")
        monkeypatch.setenv("EVOLUTION_ENABLED", "true")
        monkeypatch.setenv("OBSERVABILITY_AUDIT_ENABLED", "true")

        settings = ContextSeekSettings()
        assert settings.storage.backend == "file"
        assert settings.storage.path == "/data/store"
        assert settings.retrieval.default_k == 50
        assert settings.evolution.enabled is True
        assert settings.observability.audit_enabled is True

    def test_env_nested_embedding(self, monkeypatch):
        """Deeply nested embedding settings parse from env."""
        monkeypatch.setenv("EMBEDDING_PROVIDER", "langchain")
        monkeypatch.setenv("EMBEDDING_CLASS_PATH", "my_pkg.MyEmbed")
        monkeypatch.setenv("EMBEDDING_MODEL", "custom-v1")
        monkeypatch.setenv("EMBEDDING_DIMS", "768")

        settings = ContextSeekSettings()
        assert settings.embedding.provider == "langchain"
        assert settings.embedding.class_path == "my_pkg.MyEmbed"
        assert settings.embedding.model == "custom-v1"
        assert settings.embedding.dims == 768

    def test_env_file_loading(self, tmp_path, monkeypatch):
        """Values from a .env-shaped file apply when exported into the process env."""
        env_file = tmp_path / ".env"
        env_file.write_text("STORAGE_BACKEND=file\nSTORAGE_PATH=/from/env/file\n")
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            monkeypatch.setenv(key.strip(), value.strip())
        settings = ContextSeekSettings()
        assert settings.storage.backend == "file"
        assert settings.storage.path == "/from/env/file"

    def test_extra_fields_ignored(self, monkeypatch):
        """Unknown environment variables don't cause errors."""
        monkeypatch.setenv("ZZZ_NOT_A_CONTEXTSEEK_VAR", "xyz")
        settings = ContextSeekSettings()
        assert settings.storage.backend == "memory"


# ---------------------------------------------------------------------------
# to_strategy_config bridge
# ---------------------------------------------------------------------------


class TestToStrategyConfig:
    """Test conversion to legacy StrategyConfig."""

    def test_default_round_trip(self):
        """Default settings produce a valid StrategyConfig."""
        settings = ContextSeekSettings()
        config = to_strategy_config(settings)
        assert config.retrieval.default_k == 20
        assert config.retrieval.recall_routes == ("phrase", "terms")
        assert config.evolution.semantic_merge_threshold == 0.72
        assert config.write.acl_enabled is True
        assert config.lifecycle.auto_compact is True

    def test_embedding_enables_vector_recall_by_default(self):
        """Embedding config opts default retrieval into vector recall."""
        settings = ContextSeekSettings(
            embedding=EmbeddingSettings(
                provider="langchain",
                class_path="langchain_openai.OpenAIEmbeddings",
                model="text-embedding-3-small",
                dims=1536,
            ),
        )
        config = to_strategy_config(settings)
        assert config.retrieval.recall_routes == ("phrase", "terms", "vector")

    def test_explicit_recall_routes_are_preserved_with_embedding(self):
        """Explicit recall routes remain an override even when embedding exists."""
        settings = ContextSeekSettings(
            embedding=EmbeddingSettings(
                provider="langchain",
                class_path="langchain_openai.OpenAIEmbeddings",
                model="text-embedding-3-small",
                dims=1536,
            ),
            retrieval=RetrievalSettings(recall_routes=["phrase"]),
        )
        config = to_strategy_config(settings)
        assert config.retrieval.recall_routes == ("phrase",)

    def test_vector_recall_is_removed_without_embedding(self):
        """Vector recall is disabled when embedding is not configured."""
        settings = ContextSeekSettings(
            embedding=EmbeddingSettings(provider="none"),
            retrieval=RetrievalSettings(recall_routes=["phrase", "vector"]),
        )
        config = to_strategy_config(settings)
        assert config.retrieval.recall_routes == ("phrase",)

    def test_vector_only_recall_falls_back_without_embedding(self):
        """Vector-only config degrades to text recall when embedding is off."""
        settings = ContextSeekSettings(
            embedding=EmbeddingSettings(provider="none"),
            retrieval=RetrievalSettings(recall_routes=["vector"]),
        )
        config = to_strategy_config(settings)
        assert config.retrieval.recall_routes == ("phrase", "terms")

    def test_custom_values_transfer(self):
        """Custom settings values transfer to StrategyConfig."""
        settings = ContextSeekSettings(
            retrieval=RetrievalSettings(default_k=100, vector_weight=0.9),
            evolution=EvolutionSettings(min_cluster_size=5),
        )
        config = to_strategy_config(settings)
        assert config.retrieval.default_k == 100
        assert config.retrieval.vector_weight == 0.9
        assert config.evolution.min_cluster_size == 5


# ---------------------------------------------------------------------------
# Factory: build_embedder / build_llm
# ---------------------------------------------------------------------------


class TestFactory:
    """Test lazy model factory functions."""

    @staticmethod
    def _fake_langchain_embedder():
        class FakeLangChainEmbedder:
            def __init__(self, embeddings, *, dims):
                self._embeddings = embeddings
                self._dims = dims

            def __call__(self, text: str) -> list[float]:
                return self._embeddings.embed_query(text)

            @property
            def dims(self) -> int:
                return self._dims

        return FakeLangChainEmbedder

    def test_build_embedder_none(self):
        """Provider 'none' returns None without any imports."""
        result = build_embedder(EmbeddingSettings())
        assert result is None

    def test_build_embedder_no_class_path(self):
        """Provider set but empty class_path returns None."""
        result = build_embedder(EmbeddingSettings(provider="langchain", class_path=""))
        assert result is None

    def test_build_embedder_with_mock(self):
        """Successfully builds embedder with mock LangChain class."""
        mock_embeddings = MagicMock()
        mock_embeddings.embed_query.return_value = [0.1] * 768
        mock_cls = MagicMock(return_value=mock_embeddings)

        with (
            patch("contextseek.config.factory._import_class", return_value=mock_cls),
            patch(
                "contextseek.embedders.langchain_embedder.LangChainEmbedder",
                self._fake_langchain_embedder(),
            ),
        ):
            embedder = build_embedder(
                EmbeddingSettings(
                    provider="langchain",
                    class_path="some_pkg.SomeEmbeddings",
                    model="test-model",
                    dims=768,
                    kwargs={},
                )
            )

        assert embedder is not None
        mock_cls.assert_called_once_with(model="test-model")

        # Call the embedder
        result = embedder("hello")
        assert result == [0.1] * 768
        mock_embeddings.embed_query.assert_called_once_with("hello")

    def test_build_embedder_openai_provider_alias(self):
        """Provider aliases resolve class_path and default dimensions."""
        mock_embeddings = MagicMock()
        mock_cls = MagicMock(return_value=mock_embeddings)

        with (
            patch(
                "contextseek.config.factory._import_class", return_value=mock_cls
            ) as import_class,
            patch(
                "contextseek.embedders.langchain_embedder.LangChainEmbedder",
                self._fake_langchain_embedder(),
            ),
        ):
            embedder = build_embedder(
                EmbeddingSettings(provider="openai", model="text-embedding-3-small")
            )

        assert embedder is not None
        assert embedder.dims == 1536
        import_class.assert_called_once_with("langchain_openai.OpenAIEmbeddings")
        mock_cls.assert_called_once_with(model="text-embedding-3-small")

    def test_build_embedder_ollama_provider_default_dims(self):
        """Ollama alias uses its provider-specific default dimensions."""
        mock_embeddings = MagicMock()
        mock_cls = MagicMock(return_value=mock_embeddings)

        with (
            patch("contextseek.config.factory._import_class", return_value=mock_cls),
            patch(
                "contextseek.embedders.langchain_embedder.LangChainEmbedder",
                self._fake_langchain_embedder(),
            ),
        ):
            embedder = build_embedder(
                EmbeddingSettings(provider="ollama", model="nomic-embed-text")
            )

        assert embedder is not None
        assert embedder.dims == 768

    def test_resolve_embedding_dims_for_legacy_openai_class_path(self):
        """Legacy langchain_openai class_path reports its effective default dims."""
        dims = resolve_embedding_dims(
            EmbeddingSettings(
                provider="langchain",
                class_path="langchain_openai.OpenAIEmbeddings",
            )
        )

        assert dims == 1536

    def test_build_embedder_explicit_class_path_overrides_provider_alias(self):
        """Explicit class_path remains the escape hatch for custom providers."""
        mock_embeddings = MagicMock()
        mock_cls = MagicMock(return_value=mock_embeddings)

        with (
            patch(
                "contextseek.config.factory._import_class", return_value=mock_cls
            ) as import_class,
            patch(
                "contextseek.embedders.langchain_embedder.LangChainEmbedder",
                self._fake_langchain_embedder(),
            ),
        ):
            embedder = build_embedder(
                EmbeddingSettings(
                    provider="openai",
                    class_path="custom_pkg.CustomEmbeddings",
                    dims=42,
                )
            )

        assert embedder is not None
        assert embedder.dims == 42
        import_class.assert_called_once_with("custom_pkg.CustomEmbeddings")

    def test_build_embedder_unknown_provider(self):
        """Unknown providers fail with a clear error."""
        with pytest.raises(ValueError, match="Unknown embedding provider"):
            build_embedder(EmbeddingSettings(provider="not-real"))

    def test_build_llm_none(self):
        """Provider 'none' returns None."""
        result = build_llm(LLMSettings())
        assert result is None

    def test_build_llm_with_mock(self):
        """Successfully builds LLM with mock class."""
        mock_llm = MagicMock()
        mock_cls = MagicMock(return_value=mock_llm)

        with patch("contextseek.config.factory._import_class", return_value=mock_cls):
            llm = build_llm(
                LLMSettings(
                    provider="langchain",
                    class_path="some_pkg.SomeLLM",
                    model="gpt-4o-mini",
                    kwargs={"temperature": 0.0},
                )
            )

        assert llm is mock_llm
        mock_cls.assert_called_once_with(temperature=0.0, model="gpt-4o-mini")

    def test_build_llm_provider_alias(self):
        """LLM aliases resolve to their LangChain chat classes."""
        mock_llm = MagicMock()
        mock_cls = MagicMock(return_value=mock_llm)

        with patch(
            "contextseek.config.factory._import_class", return_value=mock_cls
        ) as import_class:
            llm = build_llm(
                LLMSettings(
                    provider="dashscope",
                    model="qwen-plus",
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                    kwargs={"temperature": 0.0},
                )
            )

        assert llm is mock_llm
        import_class.assert_called_once_with(
            "langchain_community.chat_models.ChatTongyi"
        )
        mock_cls.assert_called_once_with(
            temperature=0.0,
            model="qwen-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    def test_build_llm_explicit_class_path_overrides_provider_alias(self):
        """Explicit class_path wins over provider aliases."""
        mock_llm = MagicMock()
        mock_cls = MagicMock(return_value=mock_llm)

        with patch(
            "contextseek.config.factory._import_class", return_value=mock_cls
        ) as import_class:
            llm = build_llm(
                LLMSettings(
                    provider="openai",
                    class_path="custom_pkg.CustomChatModel",
                    model="custom-model",
                )
            )

        assert llm is mock_llm
        import_class.assert_called_once_with("custom_pkg.CustomChatModel")

    def test_build_llm_unknown_provider(self):
        """Unknown LLM providers fail with a clear error."""
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            build_llm(LLMSettings(provider="not-real"))

    def test_import_class_invalid(self):
        """Invalid class_path raises ImportError."""
        with pytest.raises(ImportError, match="Invalid class_path"):
            _import_class("NoModulePart")

    def test_import_class_nonexistent(self):
        """Non-existent module raises ModuleNotFoundError."""
        with pytest.raises(ModuleNotFoundError):
            _import_class("totally_fake_package.FakeClass")


# ---------------------------------------------------------------------------
# from_settings() integration
# ---------------------------------------------------------------------------


class TestFromSettings:
    """Test ContextSeek.from_settings() factory."""

    def test_default_from_settings(self):
        """from_settings() with defaults creates working client."""
        from contextseek import ContextSeek

        ctx = ContextSeek.from_settings()
        assert ctx.adapter is not None
        assert ctx.embedder is None  # no embedding configured

        # Basic add / retrieve roundtrip
        item = ctx.add("pydantic settings work", scope="test/proj/u1", source="test")
        assert item.id
        response = ctx.retrieve("settings", scope="test/proj/u1")
        assert len(response) >= 1

    def test_from_settings_file_backend(self, tmp_path):
        """from_settings() with file backend works."""
        from contextseek import ContextSeek

        settings = ContextSeekSettings(
            storage=StorageSettings(backend="file", path=str(tmp_path / "store")),
        )
        ctx = ContextSeek.from_settings(settings)
        ctx.add("file backend test", scope="t/p/u", source="test")
        response = ctx.retrieve("file", scope="t/p/u")
        assert len(response) >= 1

    def test_from_settings_with_evolution(self):
        """from_settings() enables evolution engine when configured."""
        from contextseek import ContextSeek

        settings = ContextSeekSettings(
            evolution=EvolutionSettings(enabled=True),
        )
        ctx = ContextSeek.from_settings(settings)
        assert ctx.evolution_engine is not None

    def test_from_settings_with_audit(self, tmp_path):
        """from_settings() enables audit log when configured."""
        from contextseek import ContextSeek

        settings = ContextSeekSettings(
            observability=ObservabilitySettings(
                audit_enabled=True,
                audit_path=str(tmp_path / "audit.jsonl"),
            ),
        )
        ctx = ContextSeek.from_settings(settings)
        assert ctx.audit_log is not None
