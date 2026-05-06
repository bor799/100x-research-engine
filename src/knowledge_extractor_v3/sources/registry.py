"""Source registry for loading and managing source configurations.

Loads source configs from V3Config and provides discovery adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import SourceConfig, SourceItem


class SourceAdapter(Protocol):
    """Protocol for source discovery adapters."""

    def discover(self, source: SourceConfig, *, lookback_days: int) -> list[SourceItem]:
        """Discover items from the source."""
        ...

    def source_type(self) -> str:
        """Return the source type this adapter handles."""
        ...


class SourceRegistry:
    """Registry of source configurations and their adapters.

    Loads sources from config and provides discovery functionality.
    """

    def __init__(
        self,
        sources: list[SourceConfig],
        *,
        project_root: Path | None = None,
    ) -> None:
        self._sources = [s for s in sources if s.enabled]
        self._project_root = project_root or Path.cwd()
        self._adapters: dict[str, SourceAdapter] = {}

    def register_adapter(self, adapter: SourceAdapter) -> None:
        """Register a source adapter for a type."""
        self._adapters[adapter.source_type()] = adapter

    def get_adapter(self, source_type: str) -> SourceAdapter | None:
        """Get adapter for a source type."""
        return self._adapters.get(source_type)

    def sources_by_type(self, source_type: str) -> list[SourceConfig]:
        """Get all enabled sources of a given type."""
        return [s for s in self._sources if s.type == source_type]

    def all_enabled_sources(self) -> list[SourceConfig]:
        """Get all enabled sources."""
        return list(self._sources)

    def discover_items(
        self,
        source: SourceConfig,
        *,
        lookback_days: int = 7,
    ) -> list[SourceItem]:
        """Discover items from a specific source."""
        adapter = self.get_adapter(source.type)
        if adapter is None:
            return []

        return adapter.discover(source, lookback_days=lookback_days)

    def discover_all(
        self,
        *,
        lookback_days: int = 7,
        per_source_limit: int = 10,
    ) -> dict[str, list[SourceItem]]:
        """Discover items from all enabled sources.

        Returns dict mapping source_id to list of discovered items.
        """
        results: dict[str, list[SourceItem]] = {}

        for source in self._sources:
            items = self.discover_items(source, lookback_days=lookback_days)
            # Apply per-source limit
            results[source.id] = items[:per_source_limit]

        return results
