"""Optional wrapper for LangSmith ``@traceable``.

Degrades to a pass-through decorator when langsmith is not installed;
callers don't need to be aware.
Actual tracing is controlled by environment variables
``LANGSMITH_TRACING=true`` + ``LANGSMITH_API_KEY``
(langsmith reads these variables itself; the shim does not duplicate the check).

Usage is identical to ``langsmith.traceable``, supporting both forms::

    @traceable
    def f(...): ...

    @traceable(run_type="retriever", name="ContextSeek.retrieve")
    def g(...): ...
"""

from __future__ import annotations

from typing import Any, Callable

try:
    from langsmith import traceable as _traceable  # type: ignore[import-not-found]

    _HAS_LANGSMITH = True
except ImportError:
    _HAS_LANGSMITH = False


def traceable(*d_args: Any, **d_kwargs: Any) -> Callable[..., Any]:
    """A decorator with the same signature as ``langsmith.traceable``;
    no-op when langsmith is absent.

    - With langsmith installed: delegates directly to the real
      ``langsmith.traceable``. When ``LANGSMITH_TRACING`` is not enabled,
      langsmith internally skips reporting with near-zero overhead, so there
      is no need to check the environment variable here.
    - Without langsmith: returns a pass-through decorator; the decorated
      function's behavior is completely unchanged.
    """
    if _HAS_LANGSMITH:
        return _traceable(*d_args, **d_kwargs)

    # Degradation path: compatible with both @traceable (bare) and
    # @traceable(...) (parameterized) calling forms.
    if len(d_args) == 1 and callable(d_args[0]) and not d_kwargs:
        return d_args[0]  # @traceable used directly on a function

    def _decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return func  # @traceable(...) with parameters

    return _decorator


__all__ = ["traceable"]
