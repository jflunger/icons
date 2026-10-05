from .base import Enricher, EnrichData
from .bootstrap import BootstrapEnricher
from .googlefonts import GoogleFontsEnricher
from .lucide import LucideEnricher
from .phosphor import PhosphorEnricher
from .tabler import TablerEnricher

ALL: list[type[Enricher]] = [LucideEnricher, TablerEnricher, PhosphorEnricher, BootstrapEnricher,
                             GoogleFontsEnricher]


def for_prefix(prefix: str) -> list[Enricher]:
    return [cls() for cls in ALL if cls.prefix == prefix]
