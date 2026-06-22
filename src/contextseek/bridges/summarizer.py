"""Summarizer — generate L2 abstract and L1 summary for ContextItems.

The API layer (``ContextSeek``) is the only place that calls a Summarizer.
:class:`LLMSummarizer` wraps any LangChain ``BaseChatModel`` to produce
controlled-length summaries.

When no LLM is available ContextSeek falls back to flat L0-only mode
(no summarization, embeddings run on full content).
"""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable
from contextseek.llm.client import invoke_text
from contextseek.llm.prompts import (
    LLMPromptTemplates,
    summarizer_abstract_prompt,
    summarizer_summary_prompt,
)

# Sentence terminators for deriving an L2 abstract from an L1 summary's lead.
# Covers CJK and ASCII end punctuation plus hard line breaks.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。.!?！？\n])")


def _lead_sentence(text: str, char_budget: int) -> str:
    """Return the summary's opening sentence, trimmed to ``char_budget``.

    Used to derive the L2 abstract from the L1 summary's first line instead of
    a second LLM call. Returns an empty string when nothing usable is found so
    callers can fall back to a dedicated abstract prompt.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    first = next(
        (s.strip() for s in _SENTENCE_SPLIT_RE.split(cleaned) if s.strip()), ""
    )
    if not first:
        return ""
    if len(first) > char_budget:
        return first[:char_budget].rstrip()
    return first


@runtime_checkable
class Summarizer(Protocol):
    """Protocol that produces L2 (abstract) and L1 (summary) summaries."""

    def abstract(self, content: str) -> str:
        """Produce the ~100-token L2 abstract."""

    def summary(self, content: str) -> str:
        """Produce the ~2k-token L1 summary."""

    def summarize(self, content: str) -> tuple[str, str]:
        """Produce ``(abstract, summary)`` together.

        Implementations may derive the abstract from the summary to save an LLM
        call. The default falls back to the two independent methods.
        """
        return self.abstract(content), self.summary(content)


class LLMSummarizer:
    """Summarizer backed by a LangChain ``BaseChatModel``.

    The chat model is constructed externally (e.g. via
    :func:`contextseek.config.factory.build_llm`) and injected. Both prompts
    are run synchronously through ``llm.invoke``.
    """

    def __init__(
        self,
        llm: Any,
        *,
        l2_max_chars: int = 100,
        l1_max_chars: int = 2000,
        prompts: LLMPromptTemplates | None = None,
    ) -> None:
        self._llm = llm
        self._l2_max_chars = int(l2_max_chars)
        self._l1_max_chars = int(l1_max_chars)
        self._prompts = prompts

    def abstract(self, content: str) -> str:
        prompt = summarizer_abstract_prompt(
            char_budget=self._l2_max_chars,
            content=content,
            templates=self._prompts,
        )
        return invoke_text(self._llm, prompt)

    def summary(self, content: str) -> str:
        prompt = summarizer_summary_prompt(
            char_budget=self._l1_max_chars,
            content=content,
            templates=self._prompts,
        )
        return invoke_text(self._llm, prompt)

    def summarize(self, content: str) -> tuple[str, str]:
        """Generate the L1 summary once and derive the L2 abstract from its lead.

        Saves one LLM round-trip per write versus calling ``abstract()`` and
        ``summary()`` separately, and keeps L0/L1 semantically consistent. Falls
        back to a dedicated abstract call only when the lead-sentence derivation
        yields nothing usable.
        """
        summary = self.summary(content)
        abstract = _lead_sentence(summary, self._l2_max_chars)
        if not abstract:
            abstract = self.abstract(content)
        return abstract, summary


__all__ = ["LLMSummarizer", "Summarizer"]
