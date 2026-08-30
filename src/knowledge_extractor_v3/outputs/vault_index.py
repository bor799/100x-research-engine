"""Frontmatter index over the Obsidian vault for write-time dedup.

Scans the same ``????-??-W?/*.md`` surface as ``magazine.scan_articles``
(dot-directories such as ``.trash-dedup`` are invisible to the glob), reads
only each file's frontmatter, and answers two questions for an incoming
article: does its ``content_hash`` already exist, and does its URL exist
under a different hash (a same-source update candidate)?
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..sources.dedupe import normalize_url

_FRONTMATTER_PREFIX_BYTES = 8192


@dataclass(frozen=True)
class VaultArticleRef:
    article_id: str
    path: Path
    week: str
    url: str
    title: str


@dataclass(frozen=True)
class DedupLookup:
    by_hash: VaultArticleRef | None
    by_url: VaultArticleRef | None


def _read_frontmatter(path: Path) -> dict[str, object]:
    """Parse the frontmatter mapping of one managed article file.

    Mirrors ``magazine._frontmatter`` without importing magazine (that module
    pulls the live LLM provider chain). Reads a bounded prefix first and falls
    back to a full read only when the closing fence is not in the prefix.
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            prefix = handle.read(_FRONTMATTER_PREFIX_BYTES)
        if not prefix.startswith("---\n"):
            return {}
        marker = prefix.find("\n---\n", 4)
        if marker < 0:
            text = path.read_text(encoding="utf-8")
            if not text.startswith("---\n"):
                return {}
            marker = text.find("\n---\n", 4)
            if marker < 0:
                return {}
            raw = text[4:marker]
        else:
            raw = prefix[4:marker]
        data = yaml.safe_load(raw) or {}
    except (OSError, yaml.YAMLError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class VaultIndex:
    """In-memory article_id/URL index over the week folders."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.by_id: dict[str, list[VaultArticleRef]] = {}
        self.by_url: dict[str, VaultArticleRef] = {}

    def rebuild(self) -> None:
        """Rescan the vault; paths sort oldest-first so ``by_id[h][0]`` is canonical."""
        self.by_id = {}
        self.by_url = {}
        for path in sorted(self.root.glob("????-??-W?/*.md")):
            metadata = _read_frontmatter(path)
            if metadata.get("type") != "knowledge-extract":
                continue
            article_id = str(metadata.get("article_id") or "").strip()
            if not article_id:
                continue
            ref = VaultArticleRef(
                article_id=article_id,
                path=path.resolve(),
                week=path.parent.name,
                url=str(metadata.get("url") or "").strip(),
                title=str(metadata.get("title") or path.stem),
            )
            self.by_id.setdefault(article_id, []).append(ref)
            url_key = normalize_url(ref.url) if ref.url else ""
            if url_key and url_key not in self.by_url:
                self.by_url[url_key] = ref
        return None

    def lookup(self, *, content_hash: str, url: str) -> DedupLookup:
        """Resolve hash-first; the URL hit only counts for a different article."""
        by_hash = self.by_id.get(content_hash, [None])[0]
        by_url = None
        url_key = normalize_url(url) if url else ""
        if url_key:
            candidate = self.by_url.get(url_key)
            if candidate is not None and candidate.article_id != content_hash:
                by_url = candidate
        return DedupLookup(by_hash=by_hash, by_url=by_url)


class VaultDedupService:
    """Single seam for the pipeline: lookup plus same-URL increment merge."""

    def __init__(
        self,
        index: VaultIndex,
        *,
        complete_fn,
        similarity_threshold: float = 0.98,
        project_root: Path | None = None,
    ) -> None:
        from .updates import ArticleUpdater

        self.index = index
        self._updater = ArticleUpdater(
            index.root,
            complete_fn=complete_fn,
            similarity_threshold=similarity_threshold,
            project_root=project_root,
        )

    def lookup(self, *, content_hash: str, url: str) -> DedupLookup:
        return self.index.lookup(content_hash=content_hash, url=url)

    def merge_update(self, ref: VaultArticleRef, fetched):
        return self._updater.merge_update(ref, fetched)
