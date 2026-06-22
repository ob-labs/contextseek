"""DataPlug and skill importers for ``ContextSeek.plug()``."""

__all__ = [
    "HermesSkillImporter",
    "MCPToolImporter",
    "OpenAIFunctionImporter",
    "PowerMemPlug",
    "PowerMemProxyPlug",
    "RAGPlug",
    "TracePlug",
]


def __getattr__(name: str):
    if name in {"PowerMemPlug", "PowerMemProxyPlug"}:
        from contextseek.plugs.powermem import PowerMemPlug, PowerMemProxyPlug

        return {
            "PowerMemPlug": PowerMemPlug,
            "PowerMemProxyPlug": PowerMemProxyPlug,
        }[name]
    if name == "RAGPlug":
        from contextseek.plugs.rag import RAGPlug

        return RAGPlug
    if name in {"HermesSkillImporter", "MCPToolImporter", "OpenAIFunctionImporter"}:
        from contextseek.plugs.skills import (
            HermesSkillImporter,
            MCPToolImporter,
            OpenAIFunctionImporter,
        )

        return {
            "HermesSkillImporter": HermesSkillImporter,
            "MCPToolImporter": MCPToolImporter,
            "OpenAIFunctionImporter": OpenAIFunctionImporter,
        }[name]
    if name == "TracePlug":
        from contextseek.plugs.trace import TracePlug

        return TracePlug
    raise AttributeError(name)
