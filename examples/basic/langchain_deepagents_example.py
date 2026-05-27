"""Non-mock example: LangChain + DeepAgents + ContextSeek.

What this script demonstrates:
1) Baseline LangChain agent run without ContextSeek middleware.
2) DeepAgents bridges (ContextStore + TraceSink) write reusable lessons.
3) LangChain agent with ContextSeekMiddleware reuses lessons.
4) Outcome comparison (usually improvement on covered tasks, not guaranteed).

Run:
    uv run python examples/basic/langchain_deepagents_example.py
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import sys
from typing import Any, Callable

import seekvfs

if __package__ in {None, ""}:
    script_dir = str(Path(__file__).resolve().parent)
    if script_dir in sys.path:
        sys.path.remove(script_dir)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage
from langchain_core.tools import tool

from contextseek import ContextSeek, SourceType
from contextseek.bridges.deepagents import ContextStore, TraceSink
from contextseek.bridges.langchain.middleware import ContextSeekMiddleware
from contextseek.storage import FileBackend, SeekVFSStorageAdapter


DEMO_SCOPE = "examples/langchain-deepagents/real"
DEMO_ROOT = Path(".contextseek/langchain_deepagents_example")
_CURRENT_TASK_ID: str | None = None
_TASK_LOCKED: dict[str, bool] = {}


def _load_env_file() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    except Exception:
        pass


def _require_env(var_name: str) -> str:
    value = os.getenv(var_name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required env var: {var_name}. "
            "Please set it in shell or .env before running this example."
        )
    return value


def _optional_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


@dataclass(frozen=True)
class TaskCase:
    task_id: str
    user_prompt: str
    expected_token: str
    warmup_available: bool = True


def _token_in_output(expected_token: str) -> Callable[[str], bool]:
    def checker(text: str) -> bool:
        return expected_token in text

    return checker


# Tool-backed tasks. Two tasks have prior lessons; one task intentionally does not.
TASKS = [
    TaskCase(
        task_id="tx_limit_2026_05",
        user_prompt=(
            "Use available tools to compute total spend for 2026-05 under compliance gateway "
            "constraints, then reply exactly with `TX_TOTAL:335.5`."
        ),
        expected_token="TX_TOTAL:335.5",
        warmup_available=True,
    ),
    TaskCase(
        task_id="budget_json_v2",
        user_prompt=(
            "Create a monthly budget plan named 'home-2026-05' with categories "
            "Rent=3000, Food=1200, Travel=600 under compliance gateway constraints. "
            "Use tools and include `BUDGET_OK` in final answer."
        ),
        expected_token="BUDGET_OK",
        warmup_available=True,
    ),
    TaskCase(
        task_id="calendar_sync_no_tool",
        user_prompt=(
            "Sync this week's calendar events to finance app."
        ),
        expected_token="SYNC_JOB_ID:",
        warmup_available=False,
    ),
]


def _reject_with_lock(message: str) -> None:
    global _CURRENT_TASK_ID
    task_id = _CURRENT_TASK_ID or "unknown"
    if _TASK_LOCKED.get(task_id, False):
        raise ValueError("Gateway locked this task after a previous invalid request.")
    _TASK_LOCKED[task_id] = True
    raise ValueError(message)


@tool
def calculate_monthly_spend(month: str, page_limit: int = 50, mode: str = "fast") -> str:
    """Calculate monthly spend for a month using compliance gateway."""
    if _TASK_LOCKED.get(_CURRENT_TASK_ID or "unknown", False):
        raise ValueError("Gateway locked this task after a previous invalid request.")
    if page_limit != 20 or mode != "safe":
        _reject_with_lock(
            "Validation error: request rejected by compliance gateway."
        )
    totals = {"2026-05": 335.5, "2026-04": 318.0}
    value = totals.get(month)
    if value is None:
        raise ValueError(f"No transaction data for month={month}")
    return f"TX_TOTAL:{value}"


@tool
def create_budget_v2(
    plan_name: str,
    categories_json: str,
    currency: str = "USD",
    schema_version: str = "v1",
) -> str:
    """Create budget plan with gateway validation."""
    import json

    if _TASK_LOCKED.get(_CURRENT_TASK_ID or "unknown", False):
        raise ValueError("Gateway locked this task after a previous invalid request.")
    if currency != "CNY" or schema_version != "v2":
        _reject_with_lock(
            "Validation error: request rejected by compliance gateway."
        )

    try:
        payload = json.loads(categories_json)
    except Exception as exc:
        raise ValueError(f"Invalid JSON format: {exc}") from exc

    if not isinstance(payload, list):
        raise ValueError("categories_json must be a JSON list")
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("Each category entry must be an object")
        if "name" not in row or "limit" not in row:
            raise ValueError("Each category object must include name and limit")
    return f"BUDGET_OK: plan={plan_name}"


TOOLS = [calculate_monthly_spend, create_budget_v2]


def _prepare_ctx() -> ContextSeek:
    if DEMO_ROOT.exists():
        shutil.rmtree(DEMO_ROOT)
    DEMO_ROOT.mkdir(parents=True, exist_ok=True)
    backend = FileBackend(root_dir=DEMO_ROOT, scheme="contextseek://")
    vfs = seekvfs.VFS({"contextseek://": {"backend": backend}}, scheme="contextseek://")
    adapter = SeekVFSStorageAdapter(vfs)
    return ContextSeek(adapter=adapter)


def _extract_final_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        content = getattr(message, "content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if content:
            return str(content)
    return ""


def _run_task(agent: Any, task: TaskCase) -> tuple[bool, str]:
    global _CURRENT_TASK_ID
    _CURRENT_TASK_ID = task.task_id
    _TASK_LOCKED[task.task_id] = False
    try:
        out = agent.invoke({"messages": [{"role": "user", "content": task.user_prompt}]})
    except Exception as exc:
        error_text = str(exc).strip() or exc.__class__.__name__
        if "Connection error" in error_text or "Network is unreachable" in error_text:
            return (
                False,
                "MODEL_CONNECTION_ERROR: cannot reach LLM endpoint. "
                "Check OPENAI_API_KEY / LLM_BASE_URL / network route.",
            )
        if "compliance gateway" in error_text.lower() or "validation error" in error_text.lower():
            return (False, f"COMPLIANCE_REJECTED: {error_text}")
        return (False, f"MODEL_ERROR: {error_text}")
    finally:
        _CURRENT_TASK_ID = None
    messages = out.get("messages", [])
    final_text = _extract_final_text(messages)
    return _token_in_output(task.expected_token)(final_text), final_text


def _print_overview() -> None:
    print("=== Integrated Demo Overview (Non-mock) ===")
    print("- Uses real ChatOpenAI model from your .env / environment.")
    print("- Uses real LangChain create_agent + tool calls.")
    print("- Uses real DeepAgents bridges to write lessons.")
    print("- Uses real ContextSeek middleware to retrieve lessons.")
    print("- Expected: covered tasks may improve; uncovered tasks may still fail.")
    print()


def main() -> None:
    _load_env_file()
    _require_env("OPENAI_API_KEY")
    model_name = os.getenv("LLM_MODEL", "gpt-4o")
    base_url = _optional_env("LLM_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE")

    _print_overview()
    print(f"Model: {model_name}")
    if base_url:
        print(f"Base URL: {base_url}")
    else:
        print("Base URL: default OpenAI endpoint")
    print()

    ctx = _prepare_ctx()
    model = ChatOpenAI(
        model=model_name,
        temperature=0.0,
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=base_url,
    )

    baseline_agent = create_agent(model=model, tools=TOOLS, middleware=[])

    store = ContextStore.from_client(ctx, scope=DEMO_SCOPE)
    traces = TraceSink.from_client(ctx, scope=DEMO_SCOPE)

    # DeepAgents warmup: write reusable lessons.
    lessons = {
        "tx_limit_2026_05": (
            "When calling calculate_monthly_spend, you must use "
            "page_limit=20 and mode='safe' in the first call."
        ),
        "budget_json_v2": (
            "When calling create_budget_v2, first call must set currency='CNY' "
            "and schema_version='v2'. categories_json must be JSON list of "
            "objects with name and limit."
        ),
    }
    for task in TASKS:
        if not task.warmup_available:
            continue
        lesson_text = lessons[task.task_id]
        store.put_memory(
            content=f"[{task.task_id}] {lesson_text}",
            tags=["demo", "lesson", task.task_id],
            source="deepagents_warmup",
            source_type=SourceType.trace_extraction,
        )
        traces.write_trace(
            task_id=task.task_id,
            input_text=task.user_prompt,
            output_text=f"Stored lesson for {task.task_id}",
            tool_calls=[{"tool": "context_store.put_memory"}],
            status="success",
        )

    react_agent = create_agent(
        model=model,
        tools=TOOLS,
        middleware=[
            ContextSeekMiddleware(
                ctx=ctx,
                retrieval_k=5,
                auto_store=True,
                auto_compact=False,
                scope=DEMO_SCOPE,
            )
        ],
    )

    baseline_pass = 0
    react_pass = 0
    baseline_results: dict[str, bool] = {}
    rescued: list[str] = []

    print("=== Stage: run (baseline: langchain only) ===")
    for task in TASKS:
        ok, text = _run_task(baseline_agent, task)
        baseline_results[task.task_id] = ok
        baseline_pass += int(ok)
        print(f"  [{task.task_id}] {'PASS' if ok else 'FAIL'} - {text}")

    print("\n=== Stage: warmup (deepagents context_store + trace_sink) ===")
    for task in TASKS:
        if task.warmup_available:
            print(f"  [{task.task_id}] stored lesson via DeepAgents bridge")
        else:
            print(f"  [{task.task_id}] no prior lesson available")

    print("\n=== Stage: run (langchain + contextseek middleware) ===")
    print("  ContextSeek middleware injects retrieved lessons into system context.")
    for task in TASKS:
        ok, text = _run_task(react_agent, task)
        react_pass += int(ok)
        if ok and not baseline_results[task.task_id]:
            rescued.append(task.task_id)
        print(f"  [{task.task_id}] {'PASS' if ok else 'FAIL'} - {text}")

    print("\n=== Demo Summary ===")
    print(f"Baseline pass: {baseline_pass}/{len(TASKS)}")
    print(f"React pass: {react_pass}/{len(TASKS)}")
    if rescued:
        print(f"Rescued tasks (fail -> pass): {', '.join(rescued)}")
    else:
        print("Rescued tasks: none")
    print("\nInterpretation:")
    print("- Improvements come from reusable context, not model swap.")
    print("- Unchanged failures indicate missing lesson coverage or unavailable tools.")
    print("- COMPLIANCE_REJECTED is a policy rejection, not a system runtime crash.")


if __name__ == "__main__":
    main()
