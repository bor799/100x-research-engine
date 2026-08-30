"""Same-URL increment detection and the ``100x:updates`` managed block.

When a re-fetch of a known URL brings different text, one increment LLM call
decides whether anything real changed; real deltas are appended below the
original text inside a managed block that nothing else ever rewrites.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo

from ..increment_prompt import IncrementPromptError, load_increment_prompt
from ..models import FetchedContent, TypedError, utc_now
from ..prompt_parser import strip_markdown_fence
from ..queue_store import FailureKind, NextAction

SHANGHAI = ZoneInfo("Asia/Shanghai")
UPDATES_START = "<!-- 100x:updates:start -->"
UPDATES_END = "<!-- 100x:updates:end -->"

# SequenceMatcher budget per side (normalized text); mirrors the per-document
# cap the increment prompt itself applies.
SIMILARITY_CAP = 30_000
# Equal-length micro-mutations (page counters) collapse via the ratio gate; a
# real update almost always changes length, so a small length delta forces the
# LLM path even when the truncated-text ratio sits above the threshold.
LENGTH_GATE_CHARS = 150

INCREMENT_REQUIRED_FIELDS = ("has_update", "delta_summary", "new_points", "changed_facts")

_UPDATES_BLOCK_RE = re.compile(
    re.escape(UPDATES_START) + r".*?" + re.escape(UPDATES_END), re.S
)


@dataclass(frozen=True)
class UpdateOutcome:
    kind: str  # "duplicate" | "merged" | "no_update"
    path: str
    entry: dict[str, object] | None = None


def _normalize_for_similarity(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()[:SIMILARITY_CAP]


def similarity_ratio(old: str, new: str) -> float:
    return SequenceMatcher(None, _normalize_for_similarity(old), _normalize_for_similarity(new)).ratio()


def looks_like_same_content(old_full: str, new_full: str, *, threshold: float = 0.98) -> bool:
    """True when the two versions are the same content for practical purposes.

    Length delta is measured on the untruncated texts so an update appended at
    the tail of a long article cannot hide behind the similarity cap.
    """
    if abs(len(old_full) - len(new_full)) > LENGTH_GATE_CHARS:
        return False
    return similarity_ratio(old_full, new_full) >= threshold


def parse_increment_result(raw: str) -> dict[str, object] | TypedError:
    text = strip_markdown_fence(raw)
    import json

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return _increment_parse_error("Increment response is not valid JSON", detail=str(exc))
    if not isinstance(parsed, dict):
        return _increment_parse_error("Increment response must be a JSON object")
    missing = [field for field in INCREMENT_REQUIRED_FIELDS if field not in parsed]
    if missing:
        return _increment_parse_error(
            "Increment response missing fields", detail=", ".join(missing)
        )
    if not isinstance(parsed["has_update"], bool):
        return _increment_parse_error("has_update must be a boolean")
    for field in ("delta_summary",):
        parsed[field] = str(parsed.get(field) or "")
    for field in ("new_points", "changed_facts"):
        value = parsed.get(field)
        if not isinstance(value, list):
            value = []
        parsed[field] = [str(item) for item in value if str(item).strip()]
    return parsed


def _increment_parse_error(message: str, *, detail: str = "") -> TypedError:
    return TypedError(
        failure_kind=FailureKind.PARSE_ERROR,
        message=message,
        stage="increment",
        retryable=False,
        next_action=NextAction.INVESTIGATE,
        detail=detail,
    )


def sanitize_update_text(text: str) -> str:
    """Strip every HTML comment so LLM output can never smuggle managed markers.

    ``magazine._replace_block`` matches lazily between markers; a stray marker
    literal inside appended content could split a managed block. Comment
    removal makes that structurally impossible.
    """
    cleaned = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def extract_archived_text(markdown: str) -> str:
    """The archived original text: after ``## 原文``, minus the updates block."""
    marker = markdown.find("\n## 原文\n")
    body = markdown[marker + len("\n## 原文\n"):] if marker >= 0 else markdown
    return _UPDATES_BLOCK_RE.sub("", body).strip()


def append_update_entry(path: Path, entry_md: str, *, content_hash: str) -> bool:
    """Append one dated update entry; idempotent per new content_hash.

    Returns True when the file actually changed.
    """
    text = path.read_text(encoding="utf-8")
    match = _UPDATES_BLOCK_RE.search(text)
    if match and content_hash and content_hash in match.group(0):
        return False
    if match:
        updated_block = match.group(0)[: -len(UPDATES_END)].rstrip() + "\n\n"
        updated_block += entry_md.strip() + "\n" + UPDATES_END
        updated = text[: match.start()] + updated_block + text[match.end():]
    else:
        updated = text.rstrip() + f"\n\n{UPDATES_START}\n{entry_md.strip()}\n{UPDATES_END}\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(updated, encoding="utf-8")
    os.replace(tmp, path)
    return True


def build_update_entry(
    fetched: FetchedContent, parsed: dict[str, object]
) -> dict[str, object]:
    summary = sanitize_update_text(str(parsed.get("delta_summary") or ""))[:500]
    new_points = [sanitize_update_text(item)[:500] for item in parsed.get("new_points", [])][:5]
    changed_facts = [sanitize_update_text(item)[:500] for item in parsed.get("changed_facts", [])][:5]
    return {
        "date": datetime.now(SHANGHAI).date().isoformat(),
        "source": sanitize_update_text(fetched.source)[:200],
        "url": fetched.url,
        "summary": summary,
        "new_points": new_points,
        "changed_facts": changed_facts,
        "content_hash": fetched.content_hash,
    }


def render_update_entry_md(entry: dict[str, object]) -> str:
    lines = [f"### {entry['date']} · 来源 {entry['source']}", ""]
    if entry.get("summary"):
        lines.append(f"- 增量：{entry['summary']}")
    points = entry.get("new_points") or []
    if points:
        lines.append("- 新增要点：" + "；".join(str(p) for p in points))
    facts = entry.get("changed_facts") or []
    if facts:
        lines.append("- 变化事实：" + "；".join(str(f) for f in facts))
    if entry.get("url"):
        lines.append(f"- 链接：{entry['url']}")
    # Invisible idempotency marker: append_update_entry matches the new
    # content_hash inside the block to stay retry-safe. It is added by the
    # renderer (after sanitize), so LLM output can never forge or strip it.
    content_hash = str(entry.get("content_hash") or "")
    if content_hash:
        lines.append(f"<!-- 100x:update:{content_hash} -->")
    return "\n".join(lines)


class ArticleUpdater:
    """Merge a re-fetched version of a known article into its canonical file."""

    def __init__(
        self,
        root: Path,
        *,
        complete_fn,
        similarity_threshold: float = 0.98,
        project_root: Path | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.complete_fn = complete_fn
        self.similarity_threshold = similarity_threshold
        self._project_root = Path(project_root) if project_root else Path.cwd()

    def merge_update(self, ref, fetched: FetchedContent) -> UpdateOutcome | TypedError:
        from ..magazine import MagazineStore, build_issue, current_week

        try:
            markdown = ref.path.read_text(encoding="utf-8")
        except OSError as exc:
            return TypedError(
                failure_kind=FailureKind.OUTPUT_FAILED,
                message="Cannot read the archived article for increment merge",
                stage="increment",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=f"{ref.path}: {exc}",
            )
        archived = extract_archived_text(markdown)
        if looks_like_same_content(archived, fetched.text, threshold=self.similarity_threshold):
            return UpdateOutcome(kind="duplicate", path=str(ref.path))

        try:
            prompt = load_increment_prompt(self._project_root)
        except IncrementPromptError as exc:
            return TypedError(
                failure_kind=FailureKind.RUNTIME_GUARD,
                message=str(exc),
                stage="increment",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
            )
        content = (
            "已归档版本：\n" + archived[:30_000]
            + "\n\n新抓取版本：\n" + fetched.text[:30_000]
        )
        raw = self.complete_fn(content, prompt.text, stage="increment")
        if isinstance(raw, TypedError):
            return raw
        parsed = parse_increment_result(raw)
        if isinstance(parsed, TypedError):
            return parsed
        if not parsed["has_update"]:
            return UpdateOutcome(kind="no_update", path=str(ref.path))

        entry = build_update_entry(fetched, parsed)
        append_update_entry(
            ref.path, render_update_entry_md(entry), content_hash=fetched.content_hash
        )
        MagazineStore(self.root).record_update(ref.article_id, entry)
        try:
            build_issue(self.root, current_week())
        except Exception:
            pass  # the HTML is a rebuildable derivative; never fail the merge on it
        return UpdateOutcome(kind="merged", path=str(ref.path), entry=entry)


def record_manifest_event(week_dir: Path, entry: dict[str, object]) -> None:
    """Best-effort dedup event line in the week's manifest (append-only)."""
    import json

    manifest = week_dir / "manifest.jsonl"
    line = json.dumps({"event": "dedup", "timestamp": utc_now(), **entry}, ensure_ascii=False, sort_keys=True)
    try:
        with manifest.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass  # Manifest failure should not block output
