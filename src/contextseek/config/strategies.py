"""Strategy configuration and versioning for ContextSeek policies."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetrievalStrategy:
    """Controls retrieval defaults used by API layers."""

    default_k: int = 20
    recall_routes: tuple[str, ...] = ("phrase", "terms")
    candidate_multiplier: int = 4
    term_weight: float = 0.15
    recency_weight: float = 0.05
    feedback_weight: float = 0.20
    archive_penalty: float = 0.50
    vector_weight: float = 0.7
    fts_weight: float = 0.3
    # Stage-aware scoring: weight for provenance.confidence in ranking
    provenance_weight: float = 0.15
    # Evidence-based scoring
    evidence_weight: float = 0.05
    evidence_quality_weight: float = 0.10
    conflict_penalty: float = 0.20
    # Link-based scoring
    link_boost: float = 0.10
    link_refute_penalty: float = 0.40
    link_supersede_penalty: float = 0.35
    # Namespace weights: tuple of (prefix, weight_multiplier) pairs
    namespace_weights: tuple[tuple[str, float], ...] = ()
    # Stage weights for scoring
    stage_weights: tuple[tuple[str, float], ...] = (
        ("skill", 1.0),
        ("knowledge", 0.85),
        ("extracted", 0.6),
        ("raw", 0.3),
    )
    # Importance-aware ranking: importance^importance_alpha is used as a score
    # multiplier so that lower-importance items rank lower even with high semantic
    # similarity. alpha=0 disables the effect; alpha=0.5 gives sqrt-softened
    # weighting; alpha=1.0 applies importance as a linear multiplier.
    importance_alpha: float = 0.5
    # Floor applied to importance before exponentiation, preventing items with
    # very low importance from being completely suppressed (score → 0).
    importance_floor: float = 0.1
    # Geo decay: distance decay unit in km for reranker spatial penalty
    distance_decay_km: float = 1.0
    # Rerank mode: "heuristic" (default) or "llm"
    reranker_mode: str = "heuristic"
    # Limit number of candidates scored by LLM in reranking
    llm_rerank_top_n: int = 20
    # Reciprocal Rank Fusion constant used when merging multi-route hits at the
    # orchestrator layer. Industry convention is k=60; smaller values amplify
    # rank differences, larger values smooth them out.
    rrf_k: int = 60


HYBRID_RETRIEVAL_STRATEGY = RetrievalStrategy(
    recall_routes=("phrase", "terms", "vector"),
)


@dataclass(frozen=True)
class EvolutionStrategy:
    """Controls evolution pipeline behavior (replaces old MemoryStrategy)."""

    dedupe_by_hash: bool = True
    semantic_merge: bool = True
    semantic_merge_threshold: float = 0.72
    decay_half_life_days: float = 7.0
    min_cluster_size: int = 3
    # Extraction settings
    extract_min_age_seconds: float = 60.0
    # Distillation thresholds
    distill_min_use_count: int = 10
    distill_min_relevance_boost: float = 1.2
    # Archival
    ephemeral_ttl_seconds: float = 3600.0
    # Evidence chain
    reverification_threshold: float = 0.4
    # Enable LLM synthesis when merging extracted clusters
    llm_merge_enabled: bool = False


@dataclass(frozen=True)
class DreamStrategy:
    """Controls dreaming behavior in the evolution pipeline."""

    enabled: bool = True
    # Consolidation phase
    consolidation_window_hours: float = 24.0
    consolidation_min_access: int = 2
    consolidation_similarity_range: tuple[float, float] = (0.35, 0.72)
    consolidation_max_outputs: int = 5
    # Divergence phase
    divergence_enabled: bool = True
    divergence_cross_scope: bool = False
    divergence_min_clusters: int = 2
    divergence_max_outputs: int = 3
    divergence_temperature: float = 0.8
    # Dream item properties
    dream_decay_multiplier: float = 3.0
    dream_initial_confidence: float = 0.35
    dream_stability: str = "transient"
    # Triggers
    min_items_for_dream: int = 10
    cooldown_hours: float = 6.0
    # Enable LLM for consolidation pattern extraction and divergence hypothesis
    llm_enabled: bool = False


@dataclass(frozen=True)
class WriteStrategy:
    """Controls write-side source acceptance and sanitation."""

    allow_any_source: bool = True
    allowed_sources: tuple[str, ...] = ()
    redact_sensitive: bool = False
    acl_enabled: bool = True
    redaction_token: str = "[REDACTED]"
    redact_fields: tuple[str, ...] = ()
    drop_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservabilityStrategy:
    """Controls local audit persistence and metrics export."""

    persist_audit: bool = False
    audit_path: str = ".contextseek/audit.jsonl"
    metrics_path: str = ".contextseek/metrics.prom"


@dataclass(frozen=True)
class LifecycleStrategy:
    """Controls lifecycle scheduler behavior."""

    interval_seconds: float = 3600.0
    auto_compact: bool = True
    compact_min_items: int = 5


@dataclass(frozen=True)
class StrategyConfig:
    """Versioned strategy bundle for audit and runtime behavior."""

    version: str = "v1"
    retrieval: RetrievalStrategy = field(default_factory=RetrievalStrategy)
    evolution: EvolutionStrategy = field(default_factory=EvolutionStrategy)
    dream: DreamStrategy = field(default_factory=DreamStrategy)
    write: WriteStrategy = field(default_factory=WriteStrategy)
    observability: ObservabilityStrategy = field(default_factory=ObservabilityStrategy)
    lifecycle: LifecycleStrategy = field(default_factory=LifecycleStrategy)


@dataclass(frozen=True)
class CanaryRule:
    """Route requests to a strategy version by tenant/subject match."""

    version: str
    tenant_ids: tuple[str, ...] = ()
    subject_ids: tuple[str, ...] = ()
    percent: int = 100


@dataclass(frozen=True)
class StrategyRouter:
    """Select strategy version based on canary rules."""

    strategies: dict[str, StrategyConfig]
    rules: tuple[CanaryRule, ...] = ()

    def resolve(
        self,
        *,
        tenant_id: str = "",
        subject_id: str = "",
        default: StrategyConfig | None = None,
    ) -> StrategyConfig:
        """Return the strategy config for the given context."""
        import hashlib

        for rule in self.rules:
            if rule.tenant_ids and tenant_id in rule.tenant_ids:
                return self.strategies.get(rule.version, default or StrategyConfig())
            if rule.subject_ids and subject_id in rule.subject_ids:
                return self.strategies.get(rule.version, default or StrategyConfig())
            if rule.percent < 100:
                key = f"{tenant_id}:{subject_id}"
                bucket = int(hashlib.md5(key.encode()).hexdigest()[:8], 16) % 100
                if bucket < rule.percent:
                    return self.strategies.get(
                        rule.version, default or StrategyConfig()
                    )
            elif rule.percent == 100 and not rule.tenant_ids and not rule.subject_ids:
                return self.strategies.get(rule.version, default or StrategyConfig())
        return default or StrategyConfig()


def default_strategy_config() -> StrategyConfig:
    """Return default strategy configuration for local runtime."""
    return StrategyConfig()
