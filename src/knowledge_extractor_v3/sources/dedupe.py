"""URL deduplication utilities for scheduler.

Provides normalization and deduplication of discovered URLs. Normalization
is a dedup KEY only — the queue stores the normalized form so the UNIQUE(url)
constraint collapses transport variants (tracking parameters, fragments,
default ports) of the same location into one task.
"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse

# Analytics/click-through parameters that never identify content. utm_* is a
# prefix family; the rest are the well-known click IDs and share trackers.
_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAM_NAMES = frozenset({
    "gclid", "fbclid", "mc_cid", "mc_eid", "igshid", "spm", "spm_id_from",
    "vd_source", "ref_src", "ref_url", "scid", "cmpid", "share_id",
})


def _clean_query(query: str) -> str:
    """Drop tracking parameters and sort the remainder (order-insensitive)."""
    kept = []
    for part in query.split("&"):
        if not part:
            continue
        key = part.split("=", 1)[0].lower()
        if key.startswith(_TRACKING_PARAM_PREFIXES) or key in _TRACKING_PARAM_NAMES:
            continue
        kept.append(part)
    return "&".join(sorted(kept))


def normalize_url(url: str) -> str:
    """Normalize URL for deduplication.

    - Lowercase scheme and netloc
    - Remove default ports
    - Remove fragment
    - Strip tracking parameters (utm_*, gclid, ...) and sort the rest
    - Strip trailing slash
    """
    url = url.strip()

    try:
        parsed = urlparse(url)
    except Exception:
        return url

    # Lowercase scheme and netloc
    scheme = parsed.scheme.lower() if parsed.scheme else ""
    netloc = parsed.netloc.lower() if parsed.netloc else ""

    # Remove default ports
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]

    # Reconstruct without fragment, with a cleaned query
    normalized = urlunparse((
        scheme,
        netloc,
        parsed.path,
        parsed.params,
        _clean_query(parsed.query),
        "",  # Remove fragment
    ))

    # Strip trailing slash
    if normalized.endswith("/") and len(normalized) > 1:
        normalized = normalized[:-1]

    return normalized


class URLDeduper:
    """Track seen URLs for deduplication."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def is_seen(self, url: str) -> bool:
        """Check if URL has been seen."""
        normalized = normalize_url(url)
        return normalized in self._seen

    def mark_seen(self, url: str) -> None:
        """Mark URL as seen."""
        normalized = normalize_url(url)
        self._seen.add(normalized)

    def filter_unseen(self, urls: list[str]) -> list[str]:
        """Filter list to only unseen URLs."""
        return [u for u in urls if not self.is_seen(u)]

    def count(self) -> int:
        """Return count of seen URLs."""
        return len(self._seen)
