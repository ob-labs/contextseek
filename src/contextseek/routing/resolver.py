"""Scope resolver — maps scope strings to storage URIs.

The new model uses a single unified namespace. No more type-based routing.
All ContextItems under a scope share a single URI prefix.
"""

from __future__ import annotations

# Reserved item id used to store a scope node's aggregated L0/L1 summary.
# Written at ``{scheme}{scope}/__node__`` with ``searchable=False`` so it never
# surfaces in normal retrieval; it powers hierarchical navigation and directory
# overviews. Enumeration paths skip refs ending in this segment.
SCOPE_NODE_ITEM_ID = "__node__"


def is_scope_node_ref(ref: str) -> bool:
    """Whether *ref* points to a reserved scope-node summary item."""
    return ref.rstrip("/").endswith("/" + SCOPE_NODE_ITEM_ID)


class ScopeResolver:
    """Resolves scope strings to storage URI prefixes.

    URI format: {scheme}{scope}/{item_id}

    Example:
        resolver = ScopeResolver()
        resolver.prefix_for("acme/bot/user_123")
        # → "contextseek://acme/bot/user_123/"
        resolver.ref_for("acme/bot/user_123", "item_abc")
        # → "contextseek://acme/bot/user_123/item_abc"
    """

    def __init__(self, uri_scheme: str = "contextseek://"):
        self._scheme = uri_scheme

    @property
    def scheme(self) -> str:
        return self._scheme

    def prefix_for(self, scope: str) -> str:
        """Build the storage prefix for a scope.

        Args:
            scope: Scope string, e.g. "acme/bot/user_123"

        Returns:
            URI prefix ending with /, e.g. "contextseek://acme/bot/user_123/"
        """
        # Normalize: strip leading/trailing slashes
        scope = scope.strip("/")
        return f"{self._scheme}{scope}/"

    def ref_for(self, scope: str, item_id: str) -> str:
        """Build a full URI reference for a specific item.

        Args:
            scope: Scope string.
            item_id: Item ID.

        Returns:
            Full URI, e.g. "contextseek://acme/bot/user_123/item_abc"
        """
        return f"{self.prefix_for(scope)}{item_id}"

    def parse_ref(self, ref: str) -> tuple[str, str]:
        """Parse a URI reference into (scope, item_id).

        Args:
            ref: Full URI reference.

        Returns:
            Tuple of (scope, item_id).

        Raises:
            ValueError: If ref doesn't match the scheme.
        """
        if not ref.startswith(self._scheme):
            msg = f"ref does not match scheme {self._scheme}: {ref}"
            raise ValueError(msg)
        path = ref[len(self._scheme) :]
        parts = path.rstrip("/").rsplit("/", 1)
        if len(parts) != 2:
            msg = f"cannot parse ref into scope/item_id: {ref}"
            raise ValueError(msg)
        return parts[0], parts[1]
