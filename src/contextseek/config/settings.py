"""Unified pydantic-settings configuration for ContextSeek.

Loads configuration from environment variables (per-section ``env_prefix``
such as ``STORAGE_`` so ``backend`` becomes ``STORAGE_BACKEND``) and from a
config file resolved in priority order:

1. ``CONTEXTSEEK_CONFIG`` env var (explicit override)
2. ``.env`` in CWD / project root / ``examples/configs/`` (SDK / developer workflow)
3. ``~/.contextseek/config.env`` (personal install, created by ``contextseek init``)
4. ``python-dotenv`` search

Environment keys are matched case-insensitively.  Zero-config defaults yield
a local SQLite store with keyword-only retrieval — no LLM, native engine, or
embedding model required.  Advanced users can opt into seekdb or OceanBase via
``STORAGE_BACKEND``.

Usage::

    from contextseek.config.settings import ContextSeekSettings

    # Auto-loads from env / .env
    settings = ContextSeekSettings()

    # Or construct explicitly
    settings = ContextSeekSettings(
        storage=StorageSettings(backend="file", path="/data/ctx"),
        embedding=EmbeddingSettings(
            provider="langchain",
            class_path="langchain_openai.OpenAIEmbeddings",
            model="text-embedding-3-small",
            dims=1536,
        ),
    )
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contextseek.config.strategies import StrategyConfig

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from contextseek.llm.prompts import DEFAULT_LLM_PROMPTS


def _get_default_env_file() -> str | None:
    """Pick a config file path in priority order:

    1. ``CONTEXTSEEK_CONFIG`` env var (explicit override)
    2. ``.env`` in CWD / project root / examples (SDK / developer workflow)
    3. ``~/.contextseek/config.env`` (personal install, created by ``contextseek init``)
    4. ``python-dotenv`` search as last resort

    Project-level config (2) takes precedence over personal config (3) so that
    SDK users building applications are not affected by a local ``contextseek init``.
    """
    import os

    explicit = os.environ.get("CONTEXTSEEK_CONFIG", "").strip()
    if explicit:
        p = Path(explicit).expanduser()
        return str(p)

    project_root = Path(__file__).resolve().parents[3]
    candidates = (
        Path.cwd() / ".env",
        project_root / ".env",
        project_root / "examples" / "configs" / ".env",
    )
    for path in candidates:
        if path.is_file():
            return str(path)

    personal = Path.home() / ".contextseek" / "config.env"
    if personal.is_file():
        return str(personal)

    try:
        from dotenv import find_dotenv

        found = find_dotenv(usecwd=True)
        if found:
            return found
    except ImportError:
        pass
    except Exception:
        pass
    return None


_DEFAULT_ENV_FILE = _get_default_env_file()


def settings_config(
    *,
    env_prefix: str = "",
    env_file: str | None = _DEFAULT_ENV_FILE,
    extra: str = "ignore",
) -> SettingsConfigDict:
    """Options for the root ``ContextSeekSettings`` (PowerMem-style .env + case folding)."""
    return SettingsConfigDict(
        case_sensitive=False,
        extra=extra,
        env_prefix=env_prefix,
        env_file=env_file,
        env_file_encoding="utf-8",
    )


def nested_section_config(env_prefix: str) -> SettingsConfigDict:
    """Per-section ``BaseSettings`` (``STORAGE_BACKEND`` = prefix ``STORAGE_`` + field ``backend``)."""
    return SettingsConfigDict(
        env_prefix=env_prefix,
        env_file=_DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


# ---------------------------------------------------------------------------
# Nested setting sections
# ---------------------------------------------------------------------------


class StorageSettings(BaseSettings):
    """Storage backend configuration."""

    model_config = nested_section_config("STORAGE_")

    backend: str = "sqlite"
    """Backend type: "memory", "file", "sqlite", "seekdb", or "oceanbase".
    Desktop/personal installs default to "sqlite" (cross-platform, no native deps)."""

    path: str = ".contextseek/store"
    """Root path for file-based backend."""

    uri_scheme: str = "contextseek://"
    """URI scheme used for scope resolution."""

    cold_backend: str | None = None
    """Optional cold-tier backend type (None disables tiered storage)."""

    cold_path: str = ".contextseek/cold"
    """Root path for cold-tier file backend."""


class SQLiteSettings(BaseSettings):
    """SQLite storage backend configuration (local file, cross-platform)."""

    model_config = nested_section_config("SQLITE_")

    path: str = "~/.contextseek/contextseek.sqlite3"
    """Path to the SQLite database file."""


class SeekDBSettings(BaseSettings):
    """SeekDB storage backend configuration (embedded or server mode)."""

    model_config = nested_section_config("SEEKDB_")

    path: str = "~/.contextseek/seekdb.db"
    """Local path for embedded mode. Ignored when host is set."""

    host: str = ""
    """Remote seekdb server host. Empty string = embedded (local) mode."""

    port: int = 2881
    """Remote seekdb server port."""

    database: str = "contextseek"
    """Database name inside seekdb."""


class OceanBaseSettings(BaseSettings):
    """OceanBase connection configuration."""

    model_config = nested_section_config("OB_")

    host: str = "127.0.0.1"
    port: str = "2881"
    user: str = "root@test"
    password: str = ""
    db_name: str = "test"
    table_name: str = "contextseek_items"


class EmbeddingSettings(BaseSettings):
    """Embedding model configuration (delegates to LangChain)."""

    model_config = nested_section_config("EMBEDDING_")

    provider: str = "none"
    """Provider: "none", "openai", "dashscope", "ollama", "huggingface", or "langchain"."""

    class_path: str = ""
    """Optional fully qualified class path for custom LangChain embeddings."""

    model: str = ""
    """Model name passed to the provider constructor."""

    dims: int = 0
    """Vector dimensions (required when provider != "none")."""

    base_url: str = ""
    """Optional base URL for compatible endpoints (e.g. EMBEDDING_BASE_URL in .env)."""

    kwargs: dict[str, Any] = Field(default_factory=dict)
    """Extra keyword arguments forwarded to the provider constructor."""


class LLMSettings(BaseSettings):
    """LLM model configuration (delegates to LangChain)."""

    model_config = nested_section_config("LLM_")

    provider: str = "none"
    """Provider: "none", "openai", "dashscope", "ollama", or "langchain"."""

    class_path: str = ""
    """Optional fully qualified class path for a custom LangChain chat model."""

    model: str = ""
    """Model name passed to the provider constructor."""

    base_url: str = ""
    """Optional base URL for compatible endpoints (e.g. LLM_BASE_URL in .env)."""

    kwargs: dict[str, Any] = Field(default_factory=dict)
    """Extra keyword arguments forwarded to the provider constructor."""


class SummarizerSettings(BaseSettings):
    """Summarizer configuration — drives L2/L1 generation in ``ContextSeek.add()``.

    Reuses the global ``LLM_*`` configuration when ``provider="llm"``.
    """

    model_config = nested_section_config("SUMMARIZER_")

    provider: str = "llm"
    """Provider: ``"none"`` (disabled) or ``"llm"`` (LLM-driven L2/L1 generation).

    Default is ``"llm"`` — when no LLM is configured, the factory returns
    ``None`` and ContextSeek falls back to flat L0-only behavior.
    """

    l2_max_chars: int = 100
    """Maximum character budget for L2 abstracts (default 100)."""

    l1_max_chars: int = 2000
    """Maximum character budget for L1 overviews (default 2000)."""


class RetrievalSettings(BaseSettings):
    """Retrieval pipeline tuning parameters."""

    model_config = nested_section_config("RETRIEVAL_")

    default_k: int = 20
    recall_routes: list[str] = Field(default_factory=lambda: ["phrase", "terms"])
    candidate_multiplier: int = 4
    vector_weight: float = 0.7
    fts_weight: float = 0.3
    term_weight: float = 0.15
    recency_weight: float = 0.05
    feedback_weight: float = 0.20
    archive_penalty: float = 0.50
    provenance_weight: float = 0.15
    link_boost: float = 0.10
    link_refute_penalty: float = 0.40
    link_supersede_penalty: float = 0.35
    relation_aware_enabled: bool = True
    link_expansion_enabled: bool = True
    link_expansion_max_depth: int = 2
    link_expansion_decay: float = 0.65
    link_expansion_min_strength: float = 0.0
    link_expansion_relations: list[str] = Field(
        default_factory=lambda: [
            "derived_from",
            "supported_by",
            "refuted_by",
            "supersedes",
            "merged_from",
            "distilled_into",
            "synthesized_from",
        ]
    )
    reranker_mode: str = "heuristic"
    llm_rerank_top_n: int = 20


class EvolutionSettings(BaseSettings):
    """Evolution pipeline configuration."""

    model_config = nested_section_config("EVOLUTION_")

    enabled: bool = False
    dedupe_by_hash: bool = True
    semantic_merge: bool = True
    semantic_merge_threshold: float = 0.72
    min_cluster_size: int = 3
    decay_half_life_days: float = 7.0
    extract_min_age_seconds: float = 60.0
    distill_min_use_count: int = 10
    distill_min_relevance_boost: float = 1.2
    ephemeral_ttl_seconds: float = 3600.0
    llm_merge_enabled: bool = False
    llm_conflict_check_enabled: bool = False
    llm_stage_infer_enabled: bool = False
    llm_distill_enabled: bool = False
    llm_feedback_enabled: bool = False
    # LLM-free evolution: plain text items
    text_extract_min_access: int = 3
    """Minimum access_count before a plain-text raw item is eligible for extraction."""
    heuristic_distill_min_use: int = 5
    """Heuristic skill distillation threshold (lower than LLM path default of 10)."""
    heuristic_distill_min_age_days: float = 3.0
    """Minimum item age in days before heuristic distillation."""
    heuristic_distill_min_boost: float = 1.1
    """Minimum relevance_boost for heuristic skill distillation."""


class DreamSettings(BaseSettings):
    """Dream pipeline configuration."""

    model_config = nested_section_config("DREAM_")

    llm_enabled: bool = False


class PromptSettings(BaseSettings):
    """LLM prompt template configuration."""

    model_config = nested_section_config("PROMPT_")

    summarizer_abstract_template: str = DEFAULT_LLM_PROMPTS.summarizer_abstract_template
    summarizer_summary_template: str = DEFAULT_LLM_PROMPTS.summarizer_summary_template
    retrieval_relevance_template: str = DEFAULT_LLM_PROMPTS.retrieval_relevance_template
    conflict_judge_template: str = DEFAULT_LLM_PROMPTS.conflict_judge_template
    stage_classifier_template: str = DEFAULT_LLM_PROMPTS.stage_classifier_template
    feedback_tag_template: str = DEFAULT_LLM_PROMPTS.feedback_tag_template
    merge_synthesis_template: str = DEFAULT_LLM_PROMPTS.merge_synthesis_template
    distill_candidate_template: str = DEFAULT_LLM_PROMPTS.distill_candidate_template
    distill_render_template: str = DEFAULT_LLM_PROMPTS.distill_render_template
    dream_consolidation_template: str = DEFAULT_LLM_PROMPTS.dream_consolidation_template
    dream_divergence_template: str = DEFAULT_LLM_PROMPTS.dream_divergence_template


class SecuritySettings(BaseSettings):
    """Write-side security and redaction."""

    model_config = nested_section_config("SECURITY_")

    acl_enabled: bool = True
    allow_any_source: bool = True
    allowed_sources: list[str] = Field(default_factory=list)
    redact_sensitive: bool = False
    redaction_token: str = "[REDACTED]"
    redact_fields: list[str] = Field(default_factory=list)
    drop_fields: list[str] = Field(default_factory=list)


class ObservabilitySettings(BaseSettings):
    """Audit and metrics configuration."""

    model_config = nested_section_config("OBSERVABILITY_")

    audit_enabled: bool = False
    audit_path: str = ".contextseek/audit.jsonl"
    metrics_enabled: bool = False
    metrics_path: str = ".contextseek/metrics.prom"
    trace_sample_rate: float = 1.0


class GeoSettings(BaseSettings):
    """GIS feature configuration. Requires STORAGE_BACKEND=oceanbase and OceanBase >= 4.2.2 (or seekdb)."""

    model_config = nested_section_config("GEO_")

    enabled: bool = False
    """Enable GIS support. When true, OceanBaseGeoBackend replaces OceanBaseBackend."""

    geo_table_name: str = "contextseek_geo"
    """Name of the spatial index table."""

    srid: int = 4326
    """Spatial reference system ID (default: WGS84)."""

    default_radius_km: float = 10.0
    """Default search radius in kilometres when GeoQuery.radius_km is not set."""

    distance_decay_km: float = 1.0
    """Distance decay unit in km: score halves every N km (geo_sim = 1 / (1 + dist / (N * 1000)))."""

    geo_weight: float = 0.4
    """Weight of the geo recall route in RRF fusion (0.0–1.0)."""

    route_sample_interval_km: float = 0.5
    """Keypoint sampling interval in km for route-corridor queries."""

    spatial_merge_threshold_m: float = 500.0
    """GeoAwareMerger: trigger spatial merge when two items are within this distance (metres)."""


class LifecycleSettings(BaseSettings):
    """Lifecycle scheduler tuning."""

    model_config = nested_section_config("LIFECYCLE_")

    interval_seconds: float = 3600.0
    auto_compact: bool = True
    compact_min_items: int = 5


# ---------------------------------------------------------------------------
# Top-level settings
# ---------------------------------------------------------------------------


class ContextSeekSettings(BaseSettings):
    """Unified configuration for ContextSeek.

    Loads from environment variables and a resolved ``.env`` file (see
    ``_get_default_env_file``).      Each nested block is its own ``BaseSettings`` with an ``env_prefix`` (for
    example ``STORAGE_`` + ``BACKEND`` → ``STORAGE_BACKEND``).  Keys are
    case-insensitive.  Each section reads the same resolved ``.env`` file
    (``_get_default_env_file``); passing ``_env_file`` only affects the root
    model, not section sub-settings.
    Zero-config defaults yield a local SQLite store with keyword-only retrieval.

    Environment variable examples::

        STORAGE_BACKEND=file
        STORAGE_PATH=.contextseek/data
        EMBEDDING_PROVIDER=openai
        EMBEDDING_MODEL=text-embedding-3-small
        LLM_PROVIDER=openai
        LLM_MODEL=gpt-4o-mini
        EVOLUTION_ENABLED=true
        OBSERVABILITY_AUDIT_ENABLED=true
    """

    model_config = settings_config()

    storage: StorageSettings = Field(default_factory=StorageSettings)
    sqlite: SQLiteSettings = Field(default_factory=SQLiteSettings)
    seekdb: SeekDBSettings = Field(default_factory=SeekDBSettings)
    ob: OceanBaseSettings = Field(default_factory=OceanBaseSettings)
    geo: GeoSettings = Field(default_factory=GeoSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    summarizer: SummarizerSettings = Field(default_factory=SummarizerSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    evolution: EvolutionSettings = Field(default_factory=EvolutionSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    lifecycle: LifecycleSettings = Field(default_factory=LifecycleSettings)
    dream: DreamSettings = Field(default_factory=DreamSettings)
    prompts: PromptSettings = Field(default_factory=PromptSettings)
    scope_lint: bool = Field(
        default=False,
        description="Emit ScopeLintWarning when scope strings look malformed.",
    )
    default_scope: str = Field(
        default="",
        description="Default scope used by CLI commands when --scope is omitted. "
        "Set via DEFAULT_SCOPE env var or config.env.",
    )
    skill_export_dir: str = Field(
        default="",
        description="Directory for materialized SKILL.md exports. "
        "Set via SKILL_EXPORT_DIR env var or config.env; "
        "empty falls back to ~/.contextseek/skills.",
    )


# ---------------------------------------------------------------------------
# Bridge to legacy StrategyConfig dataclass
# ---------------------------------------------------------------------------


def to_strategy_config(settings: ContextSeekSettings) -> "StrategyConfig":
    """Convert ContextSeekSettings to the legacy StrategyConfig dataclass.

    Provides backward compatibility for internal modules that consume
    the frozen dataclass-based strategies.
    """
    from contextseek.config.strategies import (
        DreamStrategy,
        EvolutionStrategy,
        LifecycleStrategy,
        ObservabilityStrategy,
        RetrievalStrategy,
        StrategyConfig,
        WriteStrategy,
    )

    return StrategyConfig(
        retrieval=RetrievalStrategy(
            default_k=settings.retrieval.default_k,
            recall_routes=tuple(settings.retrieval.recall_routes),
            candidate_multiplier=settings.retrieval.candidate_multiplier,
            vector_weight=settings.retrieval.vector_weight,
            fts_weight=settings.retrieval.fts_weight,
            term_weight=settings.retrieval.term_weight,
            recency_weight=settings.retrieval.recency_weight,
            feedback_weight=settings.retrieval.feedback_weight,
            archive_penalty=settings.retrieval.archive_penalty,
            provenance_weight=settings.retrieval.provenance_weight,
            link_boost=settings.retrieval.link_boost,
            link_refute_penalty=settings.retrieval.link_refute_penalty,
            link_supersede_penalty=settings.retrieval.link_supersede_penalty,
            relation_aware_enabled=settings.retrieval.relation_aware_enabled,
            link_expansion_enabled=settings.retrieval.link_expansion_enabled,
            link_expansion_max_depth=settings.retrieval.link_expansion_max_depth,
            link_expansion_decay=settings.retrieval.link_expansion_decay,
            link_expansion_min_strength=settings.retrieval.link_expansion_min_strength,
            link_expansion_relations=tuple(settings.retrieval.link_expansion_relations),
            reranker_mode=settings.retrieval.reranker_mode,
            llm_rerank_top_n=settings.retrieval.llm_rerank_top_n,
        ),
        evolution=EvolutionStrategy(
            dedupe_by_hash=settings.evolution.dedupe_by_hash,
            semantic_merge=settings.evolution.semantic_merge,
            semantic_merge_threshold=settings.evolution.semantic_merge_threshold,
            decay_half_life_days=settings.evolution.decay_half_life_days,
            min_cluster_size=settings.evolution.min_cluster_size,
            extract_min_age_seconds=settings.evolution.extract_min_age_seconds,
            distill_min_use_count=settings.evolution.distill_min_use_count,
            distill_min_relevance_boost=settings.evolution.distill_min_relevance_boost,
            ephemeral_ttl_seconds=settings.evolution.ephemeral_ttl_seconds,
            llm_merge_enabled=settings.evolution.llm_merge_enabled,
            text_extract_min_access=settings.evolution.text_extract_min_access,
            heuristic_distill_min_use=settings.evolution.heuristic_distill_min_use,
            heuristic_distill_min_age_days=settings.evolution.heuristic_distill_min_age_days,
            heuristic_distill_min_boost=settings.evolution.heuristic_distill_min_boost,
        ),
        dream=DreamStrategy(
            llm_enabled=settings.dream.llm_enabled,
        ),
        write=WriteStrategy(
            allow_any_source=settings.security.allow_any_source,
            allowed_sources=tuple(settings.security.allowed_sources),
            redact_sensitive=settings.security.redact_sensitive,
            acl_enabled=settings.security.acl_enabled,
            redaction_token=settings.security.redaction_token,
            redact_fields=tuple(settings.security.redact_fields),
            drop_fields=tuple(settings.security.drop_fields),
        ),
        observability=ObservabilityStrategy(
            persist_audit=settings.observability.audit_enabled,
            audit_path=settings.observability.audit_path,
            metrics_path=settings.observability.metrics_path,
        ),
        lifecycle=LifecycleStrategy(
            interval_seconds=settings.lifecycle.interval_seconds,
            auto_compact=settings.lifecycle.auto_compact,
            compact_min_items=settings.lifecycle.compact_min_items,
        ),
    )


__all__ = [
    "EmbeddingSettings",
    "GeoSettings",
    "OceanBaseSettings",
    "SeekDBSettings",
    "EvolutionSettings",
    "LLMSettings",
    "LifecycleSettings",
    "ObservabilitySettings",
    "RetrievalSettings",
    "SecuritySettings",
    "ContextSeekSettings",
    "StorageSettings",
    "SummarizerSettings",
    "DreamSettings",
    "PromptSettings",
    "nested_section_config",
    "settings_config",
    "to_strategy_config",
]
