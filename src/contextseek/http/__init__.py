"""HTTP server exports."""

from typing import Any

from contextseek.http.server import create_app

__all__ = ["app", "create_app"]


def __getattr__(name: str) -> Any:
    """Expose ``app`` lazily so importing this package has no import-time side
    effects (the ASGI app is built on first access; see server.__getattr__)."""
    if name == "app":
        from contextseek.http.server import app

        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
