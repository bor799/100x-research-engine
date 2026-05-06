"""URL list source adapter for manually curated lists.

Reads URLs from a local file (one per line or YAML list).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import SourceConfig, SourceItem


class URLListAdapter:
    """URL list discovery adapter for manually curated sources."""

    def source_type(self) -> str:
        return "url_list"

    def discover(
        self,
        source: SourceConfig,
        *,
        lookback_days: int = 7,  # Not used for URL lists
    ) -> list[SourceItem]:
        """Discover items from URL list file."""
        if not source.path:
            return []

        path = Path(source.path)
        if not path.is_absolute():
            # Assume relative to project root
            path = Path.cwd() / path

        if not path.exists():
            return []

        # Read URLs
        urls = self._read_urls(path)

        items = []
        for url in urls:
            item = SourceItem(
                source_id=source.id,
                source_type="url_list",
                url=url,
                title=f"Manual: {url[:50]}",
                priority=source.priority,
                metadata={
                    "file_path": str(path),
                    "tags": source.tags,
                },
            )
            items.append(item)

        return items

    def _read_urls(self, path: Path) -> list[str]:
        """Read URLs from file (one per line or YAML list)."""
        content = path.read_text(encoding="utf-8")

        urls: list[str] = []

        # Try YAML format first if file ends with .yaml or .yml
        if path.suffix in (".yaml", ".yml"):
            try:
                import yaml
                data = yaml.safe_load(content)
                if isinstance(data, list):
                    urls = [str(u) for u in data if u]
            except Exception:
                pass  # Fall through to plain text

        # Plain text: one URL per line
        if not urls:
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)

        return urls
