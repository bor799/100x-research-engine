"""Story-identity dedup: match articles by editorial content, not transport.

Root cause this closes (observed 2026-09-01): one editorial artifact arrives
through several transports — the original publisher's RSS plus aggregators
that digest/translate it (aihot.virxact.com) — or as tracking-parameter
mirrors of the same URL. Every pre-existing identity key was transport-based
(URL string at scan/queue time, exact fetched bytes at write time), so a
cross-transport duplicate was structurally invisible and became two article
files in the same week.

The insight: the absorption card (``obsidian_brief_markdown``) is already
the system's canonical editorial representation — Chinese regardless of
source language, entity- and number-preserving. Cards covering the same
story share their named entities and domain vocabulary even when the raw
pages share nothing (English original vs Chinese digest). Story identity is
therefore computed on cards, with an evidence-tier gate for precision::

    duplicate  iff  jaccard >= strong_jaccard                       # near-identical
               or  (shared_rare >= rare_min
                   and ( (containment >= 0.10                      # entity paths
                          and ( strong_shared >= strong_min         # 2+ entities
                              or (strong_shared >= 1 and title_jaccard >= title_min)
                              or title_jaccard >= title_strong))
                       or (containment >= overlap_min               # mass path
                           and shared_rare >= mass_min) ))

where a *strong* token is a number (digits survive translation: 685k, hy4,
1200) or a latin word of length >= 5 that is not discourse vocabulary
(dwarkesh, embargo, firecracker — but never "chosen/shift/poorly").
Calibrated on the 2026-08/09 vault: every true cross-transport pair carries
>= 2 strong tokens or 12+ shared rare tokens, while the false pairs the
naive rule produced — same newsletter voice ('chosen', 'doesn', 'shift'),
same tech-theme vocabulary ('理速', '到端', '精度'), same-topic different-story
(Debian vote vs trend analysis) — carry none and stop matching. When lexical
evidence is ambiguous the rule keeps both articles: a suppressed story is a
silent loss, a surviving duplicate is visible and recoverable.

``containment`` is overlap over the SMALLER token set — the right metric
for digest-vs-original pairs, whose lengths are structurally asymmetric
(Jaccard would deflate exactly the duplicates this module exists to catch:
the 2026-09-01 incident pair scores Jaccard 0.109 but containment 0.26 with
28 shared rare entities). A rare token's document frequency in the window
corpus is at most ``max_df``, so corpus-common words (openai, model, agent,
模型) never count toward the gate — only discriminating entities (dwarkesh,
seth, kubin, 685k, 拟人化) do. Two distinct stories about the same company
share at most one or two rare entities; the same story retold shares several.

The pipeline checks this post-absorption and pre-write: a suppressed
duplicate spends one absorption call but never forks a second article file
(``dedup_outcome="duplicate_story"``, task DONE pointing at the canonical).
``outputs.dedupe.dedupe_vault`` re-runs the same rule over the whole vault so
duplicates that slipped through before a deploy self-heal; those losers move
to ``.trash-dedup/story/`` which the single-level restore glob never sees.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from .updates import record_manifest_event
from .vault_index import _read_frontmatter

# Matches the managed-block fence written by outputs.obsidian._render_markdown.
_FEEDBACK_MARKER = "<!-- 100x:user-feedback:start -->"
_RAW_TEXT_MARKER = "\n## 原文"
# Card prefix read: frontmatter (bounded fields) plus the extract card. The
# 原文 section that follows is deliberately NOT indexed — raw pages of the
# same story differ wildly across transports (that is the bug being fixed).
_CARD_READ_BYTES = 32_768
# Below this the card is too thin to compare reliably; never suppress on it.
MIN_CARD_CHARS = 80

TITLE_JACCARD_MIN = 0.20   # with >=1 strong token shared (entity/number corroboration)
TITLE_JACCARD_STRONG = 0.60  # near-identical titles: evidence on their own
MASS_TOKENS_MIN = 12        # shared rare tokens at this count are mass evidence
STRONG_TOKENS_MIN = 2       # distinct strong tokens needed on the entity path
# Entity/title paths ride a lower containment floor than the mass path:
# sharing named entities is itself the evidence, so the floor only has to
# exclude same-entity-mention noise. (Calibrated: the embargo pair shares
# embargo/madhavapeddy/ocaml at containment 0.118 once generic vocabulary is
# stopped; the mass path's 0.15 would wrongly reject it.)
STRONG_EVIDENCE_OVERLAP_MIN = 0.10

_WORD_RE = re.compile(r"[0-9a-z][0-9a-z+#._-]*")
_YEAR_RE = re.compile(r"(19|20)\d{2}$")

# Discourse vocabulary: carries voice, never identity. Beyond function words
# this includes the newsletter-voice and tech-theme words that produced the
# 2026-08/09 false positives ('chosen', 'doesn', 'poorly', 'shift', 'vibe',
# 'agent', 'model') — words a real entity (dwarkesh, debian, exploitgym)
# never needs in order to be recognized.
_LATIN_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "in", "to", "for", "on", "with",
    "is", "are", "was", "were", "be", "been", "it", "its", "as", "at", "by",
    "from", "that", "this", "these", "those", "not", "but", "if", "then",
    "than", "so", "we", "you", "they", "he", "she", "his", "her", "their",
    "our", "your", "my", "will", "would", "can", "could", "may", "might",
    "has", "have", "had", "do", "does", "did", "no", "yes", "up", "out",
    "about", "into", "over", "after", "before", "more", "most", "other",
    "new", "like", "also", "just", "one", "two", "per", "via", "amid",
    "chosen", "doesn", "know", "left", "poorly", "shift", "vibe", "thing",
    "things", "want", "wants", "use", "used", "using", "make", "makes",
    "made", "making", "take", "takes", "taken", "get", "gets", "getting",
    "goes", "going", "went", "come", "comes", "look", "looks", "seem",
    "seems", "really", "actually", "simply", "basically", "mostly", "often",
    "still", "never", "always", "much", "many", "some", "such", "only",
    "even", "very", "quite", "rather", "instead", "perhaps", "maybe",
    "probably", "however", "though", "although", "while", "since", "until",
    "whether", "either", "neither", "both", "each", "all", "any", "few",
    "less", "least", "own", "same", "different", "similar", "important",
    "better", "best", "high", "low", "long", "short", "old", "next", "last",
    "first", "second", "third", "years", "year", "week", "weeks", "days",
    "day", "time", "times", "recently", "currently", "today", "people",
    "world", "great", "big", "small", "good", "bad", "service", "agent",
    "agents", "model", "models", "llm", "ai",
})

_CJK_STOPBIGRAMS = frozenset({
    "一个", "我们", "你们", "他们", "它们", "可以", "这个", "那个", "这些",
    "那些", "就是", "不是", "没有", "什么", "但是", "因为", "所以", "如果",
    "虽然", "已经", "还是", "对于", "以及", "通过", "关于", "这样", "进行",
    "可能", "应该", "或者", "之后", "同时", "目前", "其中", "以下",
    # Card scaffold ("## 关键事实" / "## 经验" / "## 信号"): template, not
    # content. In thin early-week windows their df is too low for the max_df
    # filter to catch them, so they must be stopped here.
    "关键", "键事", "事实", "信号", "经验", "核心", "心点", "要点", "摘要",
    "正文", "原文",
    # Domain boilerplate: generic tech-discourse bigrams that carry topic,
    # never identity (same role as agent/model/llm on the latin side).
    "模型", "智能", "数据", "系统", "平台", "应用", "技术", "产业", "行业",
    "公司", "产品", "能力", "提升", "发布", "开源", "性能", "成本", "价格",
    "市场", "用户",
})


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿" or "豈" <= ch <= "﫿"


def tokenize(text: str) -> frozenset[str]:
    """Language-agnostic token set: latin words, CJK bigrams, digit runs.

    Lowercased; years and function words dropped. Entity names (dwarkesh,
    seth) and discriminating numbers (685k, 14) survive translation, which is
    what makes cross-transport comparison possible at all.
    """
    lowered = text.lower()
    tokens: set[str] = set()
    for raw in _WORD_RE.findall(lowered):
        token = raw.rstrip("+#._-")
        if len(token) < 2 or token in _LATIN_STOPWORDS:
            continue
        if _YEAR_RE.fullmatch(token):
            continue
        tokens.add(token)

    run: list[str] = []
    def flush() -> None:
        for i in range(len(run) - 1):
            bigram = run[i] + run[i + 1]
            if bigram not in _CJK_STOPBIGRAMS:
                tokens.add(bigram)
        run.clear()

    for ch in lowered:
        if _is_cjk(ch):
            run.append(ch)
        elif run:
            flush()
    if run:
        flush()
    return frozenset(tokens)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def containment(a: frozenset[str], b: frozenset[str]) -> float:
    """Overlap over the smaller set: |A∩B| / min(|A|,|B|).

    Digest-vs-original pairs are length-asymmetric by construction; Jaccard
    deflates them, containment does not.
    """
    smaller = min(len(a), len(b))
    return len(a & b) / smaller if smaller else 0.0


def _card_text(path: Path) -> str:
    """The extract card of one managed article: body up to the feedback block."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            prefix = handle.read(_CARD_READ_BYTES)
    except OSError:
        return ""
    if not prefix.startswith("---\n"):
        return prefix.strip()
    end = prefix.find("\n---\n", 4)
    if end < 0:
        return ""
    body = prefix[end + 5:]
    for marker in (_FEEDBACK_MARKER, _RAW_TEXT_MARKER):
        cut = body.find(marker)
        if cut >= 0:
            body = body[:cut]
    return body.strip()


@dataclass(frozen=True)
class StoryArticle:
    article_id: str
    path: Path
    week: str
    url: str
    title: str
    tokens: frozenset[str]
    title_tokens: frozenset[str]
    rare_tokens: frozenset[str]


@dataclass(frozen=True)
class StoryMatch:
    canonical: StoryArticle
    shared_rare: tuple[str, ...]
    strong_shared: int
    containment: float
    jaccard: float
    title_jaccard: float


def is_strong_token(token: str) -> bool:
    """Evidence strong enough to identify a story: a number or a real name.

    Numbers survive translation and transcription (685k, hy4, 1200, 4.6) —
    but only at length >= 3: two-digit numbers (scores, percentages, small
    counts) collide between same-domain stories by chance, as "53" did
    between the DeepSeek-V4-Pro and GLM-5.3 release posts (both cite the
    CyberGym benchmark). Latin words of length >= 5 that survived the
    stopword filter are treated as names (dwarkesh, embargo, firecracker).
    Everything else — CJK bigrams, short latin words — carries topic or
    voice, not identity.
    """
    if len(token) >= 3 and any(ch.isdigit() for ch in token):
        return True
    return len(token) >= 5 and token.isascii()


def _is_duplicate(
    *,
    shared_rare: int,
    strong_shared: int,
    containment: float,
    jaccard: float,
    title_jaccard: float,
    rare_min: int,
    mass_min: int,
    strong_min: int,
    overlap_min: float,
    strong_jaccard: float,
    title_min: float,
) -> bool:
    if jaccard >= strong_jaccard:
        return True
    if shared_rare < rare_min:
        return False
    # Entity and title evidence carries pairs whose overall overlap is thin
    # (an original and a digest agree on names, not on running text).
    if containment >= STRONG_EVIDENCE_OVERLAP_MIN:
        if strong_shared >= strong_min:
            return True
        if strong_shared >= 1 and title_jaccard >= title_min:
            return True
        if title_jaccard >= TITLE_JACCARD_STRONG:
            return True
    # Mass evidence: enough shared rare vocabulary to be a paraphrase.
    return shared_rare >= mass_min and containment >= overlap_min


class StoryIdentityIndex:
    """Card-token index over recent week folders; answers story matches.

    Week labels (``YYYY-MM-WN``) sort lexically in chronological order, so a
    window is simply the last ``window_weeks`` directories. ``window_weeks=None``
    means the whole vault (used by the reconciliation pass).
    """

    def __init__(self, root: Path, *, window_weeks: int | None = 2, max_df: int = 2) -> None:
        self.root = Path(root).resolve()
        self.window_weeks = window_weeks
        self.max_df = max_df
        self.articles: list[StoryArticle] = []
        self._df: Counter[str] = Counter()

    def rebuild(self) -> None:
        articles: list[StoryArticle] = []
        for path in sorted(self.root.glob("????-??-W?/*.md")):
            metadata = _read_frontmatter(path)
            if metadata.get("type") != "knowledge-extract":
                continue
            article_id = str(metadata.get("article_id") or "").strip()
            if not article_id:
                continue
            card = _card_text(path)
            if len(card) < MIN_CARD_CHARS:
                continue
            title = str(metadata.get("title") or path.stem)
            articles.append(StoryArticle(
                article_id=article_id,
                path=path.resolve(),
                week=path.parent.name,
                url=str(metadata.get("url") or "").strip(),
                title=title,
                tokens=tokenize(f"{title}\n{card}"),
                title_tokens=tokenize(title),
                rare_tokens=frozenset(),
            ))
        if self.window_weeks is not None:
            # Path order is chronological; keep the newest window_weeks weeks.
            weeks = sorted({art.week for art in articles})
            keep = set(weeks[-self.window_weeks:]) if self.window_weeks > 0 else set(weeks)
            articles = [art for art in articles if art.week in keep]

        df: Counter[str] = Counter()
        for art in articles:
            df.update(art.tokens)
        self._df = df
        self.articles = [
            replace(art, rare_tokens=frozenset(
                token for token in art.tokens if df[token] <= self.max_df
            ))
            for art in articles
        ]

    def match(
        self,
        text: str,
        title: str,
        *,
        rare_min: int = 3,
        mass_min: int = MASS_TOKENS_MIN,
        strong_min: int = STRONG_TOKENS_MIN,
        overlap_min: float = 0.15,
        strong_jaccard: float = 0.60,
        title_min: float = TITLE_JACCARD_MIN,
        exclude_ids: frozenset[str] = frozenset(),
        against: list[StoryArticle] | None = None,
    ) -> StoryMatch | None:
        """Best duplicate among indexed articles for a candidate card.

        ``against`` restricts the comparison set; default is the window.
        """
        if len(text.strip()) < MIN_CARD_CHARS:
            return None
        candidate_tokens = tokenize(f"{title}\n{text}")
        if not candidate_tokens:
            return None
        return self._best_match(
            candidate_tokens,
            tokenize(title),
            frozenset(token for token in candidate_tokens if self._df[token] <= self.max_df),
            rare_min=rare_min,
            mass_min=mass_min,
            strong_min=strong_min,
            overlap_min=overlap_min,
            strong_jaccard=strong_jaccard,
            title_min=title_min,
            exclude_ids=exclude_ids,
            against=against,
        )

    def match_article(
        self,
        art: StoryArticle,
        *,
        against: list[StoryArticle],
        rare_min: int = 3,
        mass_min: int = MASS_TOKENS_MIN,
        strong_min: int = STRONG_TOKENS_MIN,
        overlap_min: float = 0.15,
        strong_jaccard: float = 0.60,
        title_min: float = TITLE_JACCARD_MIN,
    ) -> StoryMatch | None:
        """Article-vs-article form used by the vault reconciliation pass."""
        return self._best_match(
            art.tokens,
            art.title_tokens,
            art.rare_tokens,
            rare_min=rare_min,
            mass_min=mass_min,
            strong_min=strong_min,
            overlap_min=overlap_min,
            strong_jaccard=strong_jaccard,
            title_min=title_min,
            against=against,
        )

    def _best_match(
        self,
        candidate_tokens: frozenset[str],
        candidate_title: frozenset[str],
        candidate_rare: frozenset[str],
        *,
        rare_min: int,
        mass_min: int,
        strong_min: int,
        overlap_min: float,
        strong_jaccard: float,
        title_min: float,
        exclude_ids: frozenset[str] = frozenset(),
        against: list[StoryArticle] | None = None,
    ) -> StoryMatch | None:
        best: StoryMatch | None = None
        for art in against if against is not None else self.articles:
            if art.article_id in exclude_ids:
                continue
            overlap = len(candidate_tokens & art.tokens)
            containment = overlap / min(len(candidate_tokens), len(art.tokens)) if overlap else 0.0
            jaccard_value = jaccard(candidate_tokens, art.tokens)
            title_jaccard = jaccard(candidate_title, art.title_tokens)
            shared = tuple(sorted(candidate_rare & art.rare_tokens))
            if not _is_duplicate(
                shared_rare=len(shared),
                strong_shared=sum(1 for token in shared if is_strong_token(token)),
                containment=containment,
                jaccard=jaccard_value,
                title_jaccard=title_jaccard,
                rare_min=rare_min,
                mass_min=mass_min,
                strong_min=strong_min,
                overlap_min=overlap_min,
                strong_jaccard=strong_jaccard,
                title_min=title_min,
            ):
                continue
            score = (len(shared), containment)
            if best is None or score > (len(best.shared_rare), best.containment):
                best = StoryMatch(
                    canonical=art,
                    shared_rare=shared,
                    strong_shared=sum(1 for token in shared if is_strong_token(token)),
                    containment=containment,
                    jaccard=jaccard_value,
                    title_jaccard=title_jaccard,
                )
        return best


class StoryDedupService:
    """Pipeline seam: check a freshly absorbed card, record suppressions."""

    def __init__(
        self,
        root: Path,
        *,
        rare_min: int = 3,
        mass_min: int = MASS_TOKENS_MIN,
        strong_min: int = STRONG_TOKENS_MIN,
        overlap_min: float = 0.15,
        strong_jaccard: float = 0.60,
        title_min: float = TITLE_JACCARD_MIN,
        window_weeks: int = 2,
        max_df: int = 2,
    ) -> None:
        self.root = Path(root).resolve()
        self.rare_min = rare_min
        self.mass_min = mass_min
        self.strong_min = strong_min
        self.overlap_min = overlap_min
        self.strong_jaccard = strong_jaccard
        self.title_min = title_min
        self.index = StoryIdentityIndex(self.root, window_weeks=window_weeks, max_df=max_df)

    def check(self, *, url: str, title: str, brief_markdown: str) -> StoryMatch | None:
        """Rebuild the window index and match; any failure means no match.

        The index rebuild per check matches the LiveObsidianWriter convention
        (rebuild-per-write): the just-written canonical must be visible to the
        next task, and the window is small enough that re-reading it is cheap.
        """
        try:
            self.index.rebuild()
        except OSError:
            return None
        try:
            return self.index.match(
                brief_markdown,
                title,
                rare_min=self.rare_min,
                mass_min=self.mass_min,
                strong_min=self.strong_min,
                overlap_min=self.overlap_min,
                strong_jaccard=self.strong_jaccard,
                title_min=self.title_min,
            )
        except (OSError, ValueError):
            return None

    def record_suppression(
        self,
        match: StoryMatch,
        *,
        url: str,
        task_id: int | None,
        final_score: float = 0.0,
    ) -> None:
        record_manifest_event(match.canonical.path.parent, {
            "kind": "story_duplicate_suppressed",
            "url": url,
            "task_id": task_id,
            "final_score": final_score,
            "article_id": match.canonical.article_id,
            "canonical": match.canonical.path.name,
            "shared_rare": list(match.shared_rare[:8]),
            "strong_shared": match.strong_shared,
            "containment": round(match.containment, 3),
            "jaccard": round(match.jaccard, 3),
            "title_jaccard": round(match.title_jaccard, 3),
        })
