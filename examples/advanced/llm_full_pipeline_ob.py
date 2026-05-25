"""Full real-LLM end-to-end pipeline on OceanBase.

Covers:
1) Phase 1: LLM rerank + dream
2) Phase 2: LLM merge + conflict check
3) Phase 3: LLM stage inference + distill + feedback parsing
4) Prompt template override via PromptSettings (PROMPT_*)

Run:
    uv run python examples/llm_full_pipeline_oceanbase.py
"""

from __future__ import annotations

import os
import time
import traceback
from datetime import datetime, timezone
from typing import Any

import seekvfs  # type: ignore[import-not-found]

from contextseek.client.contextseek import ContextSeek
from contextseek.config.settings import (
    DreamSettings,
    EmbeddingSettings,
    EvolutionSettings,
    LLMSettings,
    PromptSettings,
    RetrievalSettings,
    ContextSeekSettings,
    StorageSettings,
    SummarizerSettings,
)
from contextseek.domain.provenance import SourceType
from contextseek.llm.client import invoke_text
from contextseek.storage import OceanBaseBackend, SeekVFSStorageAdapter


def _mask_secret(value: str, keep: int = 4) -> str:
    if len(value) <= keep:
        return "*" * len(value)
    return ("*" * (len(value) - keep)) + value[-keep:]


def _apply_openai_kwargs_compat(kwargs: dict[str, Any], *, base_url: str, api_key: str) -> None:
    """Use OpenAI alias names accepted by current langchain_openai builds."""
    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key


def _load_class(class_path: str) -> Any:
    module_name, class_name = class_path.rsplit(".", 1)
    module = __import__(module_name, fromlist=[class_name])
    return getattr(module, class_name)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if raw == "":
        return default
    return float(raw)


def _drop_oceanbase_table(backend: OceanBaseBackend, table_name: str) -> None:
    """Best-effort cleanup for demo tables."""
    print(f"[cleanup] dropping test table if exists: {table_name}")
    try:
        backend.initialize()
        obvector = getattr(backend, "_obvector", None)
        if obvector is None:
            print("[cleanup] skip drop: backend client not initialized")
            return
        with obvector.engine.connect() as conn:
            with conn.begin():
                conn.exec_driver_sql(f"DROP TABLE IF EXISTS `{table_name}`")
        print(f"[cleanup] dropped table: {table_name}")
    except Exception as exc:
        print(f"[cleanup] drop table failed: {type(exc).__name__}: {exc}")


def _require_env() -> tuple[str, str, str, str, str]:
    host = os.getenv("OB_HOST", "127.0.0.1")
    port = os.getenv("OB_PORT", "2881")
    user = os.getenv("OB_USER", "root@test")
    password = os.getenv("OB_PASSWORD", "")
    db_name = os.getenv("OB_DB_NAME", "contextseek")
    if not password:
        raise RuntimeError("Please set OB_PASSWORD in environment.")
    return host, port, user, password, db_name


def _build_settings() -> ContextSeekSettings:
    embedding_class_path = os.getenv("EMBEDDING_CLASS_PATH", "langchain_openai.OpenAIEmbeddings")
    embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    embedding_dims = int(os.getenv("EMBEDDING_DIMS", "1536"))

    llm_class_path = os.getenv("LLM_CLASS_PATH", "langchain_openai.ChatOpenAI")
    llm_model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if "openai" in embedding_class_path.lower() and not openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for OpenAI embeddings/LLM.")

    embedding_kwargs: dict[str, Any] = {}
    llm_kwargs: dict[str, Any] = {}

    embedding_base_url = os.getenv("EMBEDDING_BASE_URL", "").strip()
    if "openai" in embedding_class_path.lower():
        _apply_openai_kwargs_compat(
            embedding_kwargs,
            base_url=embedding_base_url,
            api_key=openai_api_key,
        )
        # Some OpenAI-compatible gateways reject token-id array input and only
        # accept raw text strings. Disable len-safe token batching by default.
        embedding_kwargs["check_embedding_ctx_length"] = _env_bool(
            "EMBEDDING_CHECK_CTX_LENGTH",
            default=False,
        )
    elif embedding_base_url:
        embedding_kwargs["base_url"] = embedding_base_url
    embedding_timeout = os.getenv("EMBEDDING_TIMEOUT_SECONDS", "").strip()
    if embedding_timeout:
        embedding_kwargs["request_timeout"] = float(embedding_timeout)
    embedding_retries = os.getenv("EMBEDDING_MAX_RETRIES", "").strip()
    if embedding_retries:
        embedding_kwargs["max_retries"] = int(embedding_retries)

    llm_base_url = os.getenv("LLM_BASE_URL", "").strip()
    if "openai" in llm_class_path.lower():
        _apply_openai_kwargs_compat(
            llm_kwargs,
            base_url=llm_base_url,
            api_key=openai_api_key,
        )
    elif llm_base_url:
        llm_kwargs["base_url"] = llm_base_url
    llm_timeout = os.getenv("LLM_TIMEOUT_SECONDS", "").strip()
    if llm_timeout:
        llm_kwargs["request_timeout"] = float(llm_timeout)
    llm_retries = os.getenv("LLM_MAX_RETRIES", "").strip()
    if llm_retries:
        llm_kwargs["max_retries"] = int(llm_retries)

    print(
        "[config] embedding:",
        {
            "class_path": embedding_class_path,
            "model": embedding_model,
            "dims": embedding_dims,
            "base_url": embedding_base_url or None,
            "kwargs_keys": sorted(embedding_kwargs.keys()),
            "api_key_tail": _mask_secret(openai_api_key) if openai_api_key else None,
        },
    )
    print(
        "[config] llm:",
        {
            "class_path": llm_class_path,
            "model": llm_model,
            "base_url": llm_base_url or None,
            "kwargs_keys": sorted(llm_kwargs.keys()),
            "api_key_tail": _mask_secret(openai_api_key) if openai_api_key else None,
        },
    )

    return ContextSeekSettings(
        storage=StorageSettings(backend="memory"),  # placeholder; replaced by OceanBase adapter
        embedding=EmbeddingSettings(
            provider="langchain",
            class_path=embedding_class_path,
            model=embedding_model,
            dims=embedding_dims,
            kwargs=embedding_kwargs,
        ),
        llm=LLMSettings(
            provider="langchain",
            class_path=llm_class_path,
            model=llm_model,
            kwargs=llm_kwargs,
        ),
        summarizer=SummarizerSettings(provider="llm", l0_max_chars=120, l1_max_chars=1500),
        retrieval=RetrievalSettings(
            recall_routes=["phrase", "terms", "vector"],
            reranker_mode="llm",
            llm_rerank_top_n=20,
        ),
        evolution=EvolutionSettings(
            enabled=True,
            llm_merge_enabled=True,
            llm_conflict_check_enabled=True,
            llm_stage_infer_enabled=True,
            llm_distill_enabled=True,
            llm_feedback_enabled=True,
            min_cluster_size=3,
            distill_min_use_count=5,
            distill_min_relevance_boost=1.1,
        ),
        dream=DreamSettings(llm_enabled=True),
        prompts=PromptSettings(
            retrieval_relevance_template=(
                "You are a retrieval reranker for production RAG.\n"
                "Score semantic relevance in [0,1] and return JSON only: {{\"score\": <float>}}.\n"
                "Rubric: 1.0 exact answer; 0.7-0.9 strong topical match; 0.4-0.6 partial/related; "
                "0.1-0.3 weak mention; 0.0 only when unrelated.\n"
                "Query: {query}\nPassage: {content}"
            )
        ),
    )


def _probe_dependencies(ctx: ContextSeek, settings: ContextSeekSettings) -> None:
    print("[probe] direct instantiate embedder class...")
    t_direct_emb = time.time()
    try:
        emb_cls = _load_class(settings.embedding.class_path)
        emb_instance = emb_cls(model=settings.embedding.model, **settings.embedding.kwargs)
        direct_vec = emb_instance.embed_query("contextseek-direct-embed-probe")
        print(
            f"[probe] direct embedder ok, dims={len(direct_vec)}, elapsed={time.time() - t_direct_emb:.2f}s"
        )
    except Exception as exc:
        print(f"[probe] direct embedder failed: {type(exc).__name__}: {exc}")
        print(traceback.format_exc())
        raise

    print("[probe] testing embedder...")
    t0 = time.time()
    if ctx.embedder is None:
        raise RuntimeError("Embedder is not configured.")
    try:
        vec = ctx.embedder("contextseek-connectivity-probe")
    except Exception as exc:
        print(f"[probe] ctx embedder failed: {type(exc).__name__}: {exc}")
        print(traceback.format_exc())
        raise
    print(f"[probe] embedder ok, dims={len(vec)}, elapsed={time.time() - t0:.2f}s")

    print("[probe] direct instantiate llm class...")
    t_direct_llm = time.time()
    try:
        llm_cls = _load_class(settings.llm.class_path)
        llm_instance = llm_cls(model=settings.llm.model, **settings.llm.kwargs)
        direct_llm_resp = llm_instance.invoke("Reply with exactly: OK")
        direct_text = getattr(direct_llm_resp, "content", str(direct_llm_resp))
        if not str(direct_text).strip():
            raise RuntimeError("Direct LLM invoke returned empty response.")
        print(
            f"[probe] direct llm ok, resp={str(direct_text)[:40]!r}, elapsed={time.time() - t_direct_llm:.2f}s"
        )
    except Exception as exc:
        print(f"[probe] direct llm failed: {type(exc).__name__}: {exc}")
        print(traceback.format_exc())
        raise

    print("[probe] testing llm...")
    t1 = time.time()
    if ctx.llm is None:
        raise RuntimeError("LLM is not configured.")
    try:
        llm_resp = invoke_text(ctx.llm, "Reply with exactly: OK")
    except Exception as exc:
        print(f"[probe] ctx llm failed: {type(exc).__name__}: {exc}")
        print(traceback.format_exc())
        raise
    if not llm_resp:
        raise RuntimeError("LLM probe returned empty response.")
    print(f"[probe] llm ok, resp={llm_resp[:40]!r}, elapsed={time.time() - t1:.2f}s")


def main() -> None:
    print("[step] loading environment and building settings...")
    ob_host, ob_port, ob_user, ob_password, ob_db_name = _require_env()
    settings = _build_settings()
    print("[step] creating ContextSeek from settings...")
    ctx = ContextSeek.from_settings(settings)
    _probe_dependencies(ctx, settings)

    table_name = f"seekctx_llm_py_{int(time.time())}"
    scope = f"demo/oceanbase/llm/{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    vector_dims = settings.embedding.dims
    rerank_min_score = _env_float("RERANK_MIN_SCORE", 0.0)
    auto_drop_table = _env_bool("OB_AUTO_DROP_TABLE", False)
    drop_table_on_error = _env_bool("OB_DROP_TABLE_ON_ERROR", False)
    run_succeeded = False

    print("TABLE_NAME:", table_name)
    print("SCOPE:", scope)
    print("RERANK_MIN_SCORE:", f"{rerank_min_score:.2f}")
    print("OB_AUTO_DROP_TABLE:", auto_drop_table)
    print("ContextSeek initialized with shared LLM:", ctx.llm is not None)
    print("[step] creating OceanBase backend...")

    backend = OceanBaseBackend(
        table_name=table_name,
        vector_dims=vector_dims,
        host=ob_host,
        port=ob_port,
        user=ob_user,
        password=ob_password,
        db_name=ob_db_name,
        fulltext_parser="ngram",
        vidx_metric_type="cosine",
        vector_weight=0.6,
        fts_weight=0.4,
        rrf_k=60,
    )

    vfs = seekvfs.VFS({"contextseek://": {"backend": backend}}, scheme="contextseek://")
    vfs.__enter__()
    try:
        ctx.adapter = SeekVFSStorageAdapter(vfs)
        print("OceanBase adapter ready.")

        # Baseline data for retrieval/rerank
        print("[step] writing baseline items...")
        seed_items = [
            (
                "OceanBase is a distributed SQL database for HTAP workloads.",
                "doc_oceanbase",
                SourceType.document,
                ["oceanbase", "database"],
            ),
            (
                "Hybrid retrieval combines vector similarity and full-text matching.",
                "doc_hybrid",
                SourceType.document,
                ["retrieval", "hybrid"],
            ),
            (
                "LangChain embeddings provide a common abstraction over embedding models.",
                "doc_langchain",
                SourceType.document,
                ["langchain", "embedding"],
            ),
        ]
        for content, source, source_type, tags in seed_items:
            t_add = time.time()
            item = ctx.add(content, scope=scope, source=source, source_type=source_type, tags=tags)
            print("added:", item.id, item.stage.value, content[:50], f"(elapsed={time.time() - t_add:.2f}s)")

        print("total items:", len(ctx.items(scope=scope)))

        # Phase 1: LLM reranker
        print("[step] phase1 retrieve with llm reranker...")
        query = "How can I combine semantic and keyword retrieval for production systems?"
        all_hits = list(ctx.retrieve(query, scope=scope, k=5, full=True))
        hits = [h for h in all_hits if h.score >= rerank_min_score]
        print("retrieve hits (raw):", len(all_hits))
        print("retrieve hits (>= min_score):", len(hits))
        if not hits:
            print("[warn] all hits are below RERANK_MIN_SCORE; showing raw rerank output instead.")
            hits = all_hits
        for i, hit in enumerate(hits, 1):
            print(f"[{i}] score={hit.score:.4f} stage={hit.item.stage.value} tags={hit.item.tags[:3]}")
            print("   ", hit.item.content_text[:100])

        # Phase 2 inputs: conflict + merge cluster
        print("[step] phase2 inputs for conflict + merge...")
        base = ctx.add(
            "Feature flag rollout should not require service restarts.",
            scope=scope,
            source="ops_note_1",
            source_type=SourceType.document,
            tags=["release", "feature-flag"],
        )
        conflict_item = ctx.add(
            "Feature flag rollout must require service restarts for consistency.",
            scope=scope,
            source="ops_note_2",
            source_type=SourceType.document,
            tags=["release", "feature-flag"],
        )
        for idx in range(3):
            ctx.add(
                f"When API timeout rises, reduce batch size and retry with jitter (variation {idx}).",
                scope=scope,
                source=f"trace_{idx}",
                source_type=SourceType.agent_inference,
                tags=["procedure", "latency", "retry"],
            )
        print("base stage:", base.stage.value, "conflict stage:", conflict_item.stage.value)
        print("items now:", len(ctx.items(scope=scope)))

        # Phase 3 prep: stage inference + distill + feedback parse
        print("[step] phase3 stage/distill/feedback prep...")
        stage_probe = ctx.add(
            "Use staged rollout plus automatic rollback guardrails for risky deploys.",
            scope=scope,
            source="external_strategy_doc",
            source_type=SourceType.external_api,
            tags=["deploy", "safety"],
        )
        print("stage inference probe ->", stage_probe.stage.value)

        procedure_knowledge = ctx.add(
            {
                "name": "incident_triage_playbook",
                "description": "How to triage API latency incidents",
                "body": "1) Verify alerts\n2) Check error budget\n3) Reduce load\n4) Roll back if needed",
            },
            scope=scope,
            source="playbook_doc",
            source_type=SourceType.document,
            tags=["procedure", "incident", "latency"],
        )
        ref = ctx.resolver.ref_for(scope, procedure_knowledge.id)
        for _ in range(7):
            ctx.feedback(ref, scope=scope, score=0.2, reason="highly reusable in oncall handoff")
        print("procedure item id:", procedure_knowledge.id)

        # Run evolution + dream and inspect outputs
        print("[step] compact + dream...")
        compact_report = ctx.compact(scope=scope, dry_run=False)
        print("compact report:", compact_report)

        dream_report = ctx.dream(scope=scope, dry_run=False)
        print("dream total:", dream_report.total_dream_items)
        print("dream consolidation:", len(dream_report.consolidation.items))
        print("dream divergence:", len(dream_report.divergence.items) if dream_report.divergence else 0)

        overview = ctx.overview(scope=scope)
        print("overview:", overview)

        skills = ctx.skills(scope)
        print("skills found:", len(skills))
        for skill in skills[:5]:
            name = skill.content.get("name") if isinstance(skill.content, dict) else ""
            print("-", skill.id, skill.stage.value, name)

        items = ctx.items(scope=scope)
        print("final item count:", len(items))
        for item in items[-10:]:
            print(item.id, item.stage.value, item.tags[:4], (item.summary or item.content_text)[:80])
        run_succeeded = True
    finally:
        vfs.__exit__(None, None, None)
        print("VFS closed")
        if auto_drop_table and (run_succeeded or drop_table_on_error):
            _drop_oceanbase_table(backend, table_name)
        backend.close()


if __name__ == "__main__":
    main()
