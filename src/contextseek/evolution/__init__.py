"""Evolution module — drives the Stage progression pipeline.

raw → extracted → knowledge → skill
Plus dreaming: consolidation + divergence for creative evolution.
"""

from contextseek.evolution.engine import EvolutionEngine
from contextseek.evolution.extractor import (
    GeoExtractor,
    HeuristicExtractor,
    LLMExtractor,
)
from contextseek.evolution.merger import ConvergenceMerger
from contextseek.evolution.distiller import SkillDistiller
from contextseek.evolution.dreaming import (
    ConsolidationEngine,
    ConsolidationResult,
    DivergenceEngine,
    DivergenceResult,
    DreamEngine,
    DreamReport,
)
from contextseek.evolution.rules import EvolutionRule, DEFAULT_RULES

__all__ = [
    "ConsolidationEngine",
    "ConsolidationResult",
    "ConvergenceMerger",
    "DEFAULT_RULES",
    "DivergenceEngine",
    "DivergenceResult",
    "DreamEngine",
    "DreamReport",
    "EvolutionEngine",
    "EvolutionRule",
    "GeoExtractor",
    "HeuristicExtractor",
    "LLMExtractor",
    "SkillDistiller",
]
