# src/contextseek/config/envreflector.py
"""Reflect ``ContextSeekSettings`` to discover the env vars it consumes.

Ported from the ``agentseek-contextseek`` contrib's ``_iter_env_vars`` so the
config manager can (a) write a valid ``.env`` from an effective config and
(b) let ``AGENTSEEK_CTX_*`` act as fallbacks for contextseek's flat env vars.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic_settings import BaseSettings

from contextseek.config.settings import ContextSeekSettings


def _iter_env_vars(settings_cls: type[BaseSettings]) -> Iterator[str]:
    """Yield ``PREFIX + FIELD_NAME`` (uppercased) for every nested settings group."""
    case_sensitive = settings_cls.model_config.get("case_sensitive", False)
    for field_info in settings_cls.model_fields.values():
        group_cls = field_info.annotation
        if not isinstance(group_cls, type) or not issubclass(group_cls, BaseSettings):
            continue
        prefix = group_cls.model_config.get("env_prefix", "")
        for sub_name in group_cls.model_fields:
            env_name = f"{prefix}{sub_name}"
            yield env_name if case_sensitive else env_name.upper()


def iter_env_vars(
    settings_cls: type[BaseSettings] = ContextSeekSettings,
) -> Iterator[str]:
    """Names of every env var ``settings_cls`` reads."""
    yield from _iter_env_vars(settings_cls)


def iter_section_env_fields(
    settings_cls: type[BaseSettings] = ContextSeekSettings,
) -> Iterator[tuple[str, str, str]]:
    """Yield ``(section, field, env_name)`` for every nested settings group.

    ``section`` is the lowercase attribute name on the root settings model
    (e.g. ``storage``); ``field`` is the attribute name on that group
    (e.g. ``backend``); ``env_name`` is the resolved env var (e.g.
    ``STORAGE_BACKEND``).
    """
    case_sensitive = settings_cls.model_config.get("case_sensitive", False)
    for section, field_info in settings_cls.model_fields.items():
        group_cls = field_info.annotation
        if not isinstance(group_cls, type) or not issubclass(group_cls, BaseSettings):
            continue
        prefix = group_cls.model_config.get("env_prefix", "")
        for sub_name in group_cls.model_fields:
            env_name = f"{prefix}{sub_name}"
            yield (
                section,
                sub_name,
                env_name if case_sensitive else env_name.upper(),
            )


def env_to_section_field(
    settings_cls: type[BaseSettings] = ContextSeekSettings,
) -> dict[str, tuple[str, str]]:
    """Reverse map: ``{env_name: (section, field)}`` for every nested group.

    Used by the migrator and the ``PUT /config`` reroute to translate an env
    var (or a dashboard flat field, via ``FIELD_TO_ENV``) back into a dotted
    native path ``section.field``.
    """
    return {
        env: (section, field)
        for section, field, env in iter_section_env_fields(settings_cls)
    }
