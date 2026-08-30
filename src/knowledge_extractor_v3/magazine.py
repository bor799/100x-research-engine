"""Weekly reading magazine, durable feedback state, and localhost API.

The Markdown article is the source of truth.  The HTML issue is a derived,
portable reading surface and can always be rebuilt from the week folders.
Interactive edits are accepted only by the loopback service.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import threading
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import yaml

from .config_loader import ConfigLoader
from .llm.live_provider import create_live_provider
from .models import TypedError
from .outputs.push_ledger import week_label
from .routing import PUSH_FINAL_SCORE_MIN

SHANGHAI = ZoneInfo("Asia/Shanghai")
STATE_NAME = "阅读状态 {week}.json"
ISSUE_NAME = "知识萃取周刊 {week}.html"
# Operator decision 2026-08-30: the magazine is the reading surface for
# push-band content only. The 6.0-7.4 archive band stays on disk in the week
# folder (searchable in Obsidian) but never enters the issue or reading state.
MAGAZINE_MIN_FINAL_SCORE = PUSH_FINAL_SCORE_MIN
MAX_BODY_BYTES = 128 * 1024
FEEDBACK_START = "<!-- 100x:user-feedback:start -->"
FEEDBACK_END = "<!-- 100x:user-feedback:end -->"
REVIEW_START = "<!-- 100x:ai-review:start -->"
REVIEW_END = "<!-- 100x:ai-review:end -->"


@dataclass(frozen=True)
class Article:
    article_id: str
    title: str
    path: str
    week: str
    added_on: str
    source: str
    url: str
    final_score: float
    signal_tier: str
    brief: str
    managed: bool


def current_week(day: date | None = None) -> str:
    return week_label(day or datetime.now(SHANGHAI).date())


def _frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        return {}, text
    marker = text.find("\n---\n", 4)
    if marker < 0:
        return {}, text
    try:
        data = yaml.safe_load(text[4:marker]) or {}
    except yaml.YAMLError:
        return {}, text
    return (data if isinstance(data, dict) else {}), text[marker + 5 :]


def _brief_body(body: str) -> str:
    end = body.find(FEEDBACK_START)
    if end < 0:
        end = body.find("\n## 原文")
    return body[:end].strip() if end >= 0 else body.strip()


def read_article(path: Path, root: Path, *, allow_legacy: bool = False) -> Article | None:
    try:
        metadata, body = _frontmatter(path.read_text(encoding="utf-8"))
        relative = path.resolve().relative_to(root.resolve()).as_posix()
        managed = metadata.get("type") == "knowledge-extract"
        if not managed:
            if not allow_legacy or metadata.get("type") or not metadata.get("title"):
                return None
            if path.name.startswith(("一周关注简报", "微信推送")):
                return None
        article_id = str(metadata.get("article_id") or "").strip()
        if not article_id:
            article_id = hashlib.sha256(relative.encode()).hexdigest()
        brief = _brief_body(body) if managed else str(metadata.get("description") or "").strip()
        if not brief:
            useful = [line for line in body.splitlines() if line.strip() and not line.lstrip().startswith("![")]
            brief = "\n".join(useful[:8])[:2_000]
        return Article(
            article_id=article_id,
            title=str(metadata.get("title") or path.stem),
            path=relative,
            week=path.parent.name,
            added_on=str(metadata.get("processed_at") or metadata.get("created") or "")[:10],
            source=str(metadata.get("source") or "未知"),
            url=str(metadata.get("url") or ""),
            final_score=float(metadata.get("final_score") or 0.0),
            signal_tier=str(metadata.get("signal_tier") or ""),
            brief=brief,
            managed=managed,
        )
    except (OSError, ValueError, TypeError):
        return None


def scan_articles(root: Path) -> list[Article]:
    articles: list[Article] = []
    legacy_weeks = {current_week()}
    for state in root.glob("????-??-W?/阅读状态 ????-??-W?.json"):
        legacy_weeks.add(state.parent.name)
    for path in sorted(root.glob("????-??-W?/*.md")):
        article = read_article(path, root, allow_legacy=path.parent.name in legacy_weeks)
        if article:
            articles.append(article)
    return articles


def _empty_article_state(article: Article) -> dict[str, object]:
    return {
        "article_id": article.article_id,
        "week": article.week,
        "path": article.path,
        "added_on": article.added_on,
        "read_at": None,
        "disposition": None,
        "comment": "",
        "annotations": [],
        # Same-URL increment history; update_pending re-shelves a completed
        # article into the current issue until the reader disposes again.
        "updates": [],
        "update_pending": False,
        "review": {"revision": 0, "status": "idle", "result": "", "error": ""},
    }


class MagazineStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._lock = threading.RLock()

    def state_path(self, week: str) -> Path:
        if not re.fullmatch(r"\d{4}-\d{2}-W[1-5]", week):
            raise ValueError("invalid week")
        return self.root / week / STATE_NAME.format(week=week)

    def load_week(self, week: str) -> dict[str, object]:
        path = self.state_path(week)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("week", week)
                data.setdefault("articles", {})
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return {"week": week, "updated_at": "", "articles": {}}

    def save_week(self, week: str, data: dict[str, object]) -> None:
        path = self.state_path(week)
        path.parent.mkdir(parents=True, exist_ok=True)
        data["week"] = week
        data["updated_at"] = datetime.now(SHANGHAI).isoformat(timespec="seconds")
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def ensure_articles(self, articles: list[Article]) -> dict[str, dict[str, object]]:
        states: dict[str, dict[str, object]] = {}
        with self._lock:
            by_week: dict[str, list[Article]] = {}
            for article in articles:
                by_week.setdefault(article.week, []).append(article)
            for week, group in by_week.items():
                data = self.load_week(week)
                entries = data.setdefault("articles", {})
                if not isinstance(entries, dict):
                    entries = {}
                    data["articles"] = entries
                changed = False
                for article in group:
                    if article.article_id not in entries:
                        entries[article.article_id] = _empty_article_state(article)
                        changed = True
                    states[article.article_id] = entries[article.article_id]
                if changed:
                    self.save_week(week, data)
        return states

    def find(self, article_id: str) -> tuple[Article, dict[str, object], dict[str, object]]:
        article = next((a for a in scan_articles(self.root) if a.article_id == article_id), None)
        if article is None:
            raise KeyError(article_id)
        self.ensure_articles([article])
        week_data = self.load_week(article.week)
        entries = week_data["articles"]
        assert isinstance(entries, dict)
        state = entries[article_id]
        assert isinstance(state, dict)
        return article, state, week_data

    def update(self, article_id: str, patch: dict[str, object]) -> dict[str, object]:
        allowed = {"read", "disposition", "comment"}
        if not set(patch).issubset(allowed):
            raise ValueError("unsupported state field")
        with self._lock:
            article, state, week_data = self.find(article_id)
            if "read" in patch:
                state["read_at"] = (
                    datetime.now(SHANGHAI).isoformat(timespec="seconds") if patch["read"] else None
                )
            if "disposition" in patch:
                disposition = patch["disposition"]
                if disposition not in (None, "commented", "no_comment"):
                    raise ValueError("invalid disposition")
                state["disposition"] = disposition
                if disposition is not None:
                    state["update_pending"] = False
            if "comment" in patch:
                comment = str(patch["comment"]).strip()[:20_000]
                state["comment"] = comment
                if comment:
                    state["disposition"] = "commented"
                    state["update_pending"] = False
            self.save_week(article.week, week_data)
            if article.managed:
                self._write_feedback(article, state)
            return state

    def add_annotation(self, article_id: str, payload: dict[str, object]) -> dict[str, object]:
        quote = str(payload.get("quote") or "").strip()[:8_000]
        comment = str(payload.get("comment") or "").strip()[:8_000]
        if not quote:
            raise ValueError("quote is required")
        with self._lock:
            article, state, week_data = self.find(article_id)
            annotations = state.setdefault("annotations", [])
            if not isinstance(annotations, list):
                annotations = []
                state["annotations"] = annotations
            annotations.append(
                {
                    "id": f"a{len(annotations) + 1}",
                    "quote": quote,
                    "prefix": str(payload.get("prefix") or "")[-500:],
                    "suffix": str(payload.get("suffix") or "")[:500],
                    "comment": comment,
                    "created_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
                }
            )
            state["disposition"] = "commented"
            state["update_pending"] = False
            self.save_week(article.week, week_data)
            if article.managed:
                self._write_feedback(article, state)
            return state

    def record_update(self, article_id: str, entry: dict[str, object]) -> dict[str, object]:
        """Append a same-URL increment entry and re-shelve the article.

        Idempotent per the new content hash: a retried increment merge never
        doubles the entry. The markdown write belongs to the updater; this
        only owns state.
        """
        with self._lock:
            article, state, week_data = self.find(article_id)
            updates = state.get("updates")
            if not isinstance(updates, list):
                updates = []
                state["updates"] = updates
            content_hash = str(entry.get("content_hash") or "")
            if content_hash and any(
                isinstance(item, dict) and item.get("content_hash") == content_hash
                for item in updates
            ):
                return state
            updates.append(entry)
            state["update_pending"] = True
            self.save_week(article.week, week_data)
            return state

    def mark_review(self, article_id: str, *, status: str, result: str = "", error: str = "") -> dict[str, object]:
        with self._lock:
            article, state, week_data = self.find(article_id)
            review = state.setdefault("review", {})
            if not isinstance(review, dict):
                review = {}
                state["review"] = review
            if status == "queued":
                review["revision"] = int(review.get("revision") or 0) + 1
            review.update({"status": status, "result": result, "error": error})
            review["updated_at"] = datetime.now(SHANGHAI).isoformat(timespec="seconds")
            self.save_week(article.week, week_data)
            if status == "done" and article.managed:
                self._write_review(article, result)
            return state

    def _safe_article_path(self, article: Article) -> Path:
        path = (self.root / article.path).resolve()
        path.relative_to(self.root)
        return path

    def _replace_block(self, article: Article, start: str, end: str, body: str) -> None:
        path = self._safe_article_path(article)
        text = path.read_text(encoding="utf-8")
        pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
        replacement = f"{start}\n{body.strip()}\n{end}"
        if not pattern.search(text):
            raise ValueError(f"managed block missing: {path}")
        updated = pattern.sub(lambda _: replacement, text, count=1)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(updated, encoding="utf-8")
        os.replace(tmp, path)

    def _write_feedback(self, article: Article, state: dict[str, object]) -> None:
        lines = ["## 阅读反馈", ""]
        comment = str(state.get("comment") or "").strip()
        if comment:
            lines.extend([comment, ""])
        annotations = state.get("annotations")
        if isinstance(annotations, list) and annotations:
            lines.extend(["### 划线与批注", ""])
            for item in annotations:
                if not isinstance(item, dict):
                    continue
                lines.append(f"> {str(item.get('quote') or '').replace(chr(10), chr(10) + '> ')}")
                if item.get("comment"):
                    lines.append(f"\n批注：{item['comment']}")
                lines.append("")
        if len(lines) == 2:
            lines.append("已读，无评论。" if state.get("disposition") == "no_comment" else "尚未提交评论。")
        self._replace_block(article, FEEDBACK_START, FEEDBACK_END, "\n".join(lines))

    def _write_review(self, article: Article, result: str) -> None:
        self._replace_block(article, REVIEW_START, REVIEW_END, "## AI 定向再萃取\n\n" + result)


def issue_payload(root: Path, week: str | None = None) -> dict[str, object]:
    root = Path(root).resolve()
    selected_week = week or current_week()
    articles = scan_articles(root)
    # Push-band only (operator decision 2026-08-30): managed extractions below
    # the magazine line stay on disk in the week folder but never enter the
    # issue or reading state. Unmanaged/legacy files carry no score and are
    # left untouched by this filter.
    articles = [a for a in articles if not a.managed or a.final_score >= MAGAZINE_MIN_FINAL_SCORE]
    store = MagazineStore(root)
    states = store.ensure_articles(articles)
    included: list[dict[str, object]] = []
    for article in articles:
        state = states[article.article_id]
        complete = bool(state.get("read_at")) and state.get("disposition") in {"commented", "no_comment"}
        pending = bool(state.get("update_pending"))
        if article.week == selected_week or (article.week < selected_week and (not complete or pending)):
            row = asdict(article)
            row["state"] = state
            row["carryover"] = article.week != selected_week
            row["complete"] = complete
            row["update_pending"] = pending
            updates = state.get("updates")
            row["latest_update"] = updates[-1] if isinstance(updates, list) and updates else None
            row["obsidian_url"] = "obsidian://open?" + urllib.parse.urlencode(
                {"vault": root.parent.name, "file": article.path.removesuffix(".md")}
            )
            included.append(row)
    included.sort(key=lambda item: (bool(item["carryover"]), item["added_on"], -float(item["final_score"])))
    return {
        "week": selected_week,
        "generated_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "articles": included,
        "counts": {
            "total": len(included),
            "unread": sum(not bool(a["state"].get("read_at")) for a in included),
            "unfinished": sum(not bool(a["complete"]) for a in included),
            "carryover": sum(bool(a["carryover"]) for a in included),
            "updates_pending": sum(bool(a["update_pending"]) for a in included),
        },
    }


def _html_document(payload: dict[str, object]) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    week = html.escape(str(payload["week"]))
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>知识萃取周刊 {week}</title>
<style>
:root{{--paper:#f3efe5;--ink:#171612;--muted:#6c675e;--rule:#24221d;--red:#a63228;--soft:#e7e0d2}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font-family:"Songti SC","STSong",serif;line-height:1.75}}
button,textarea{{font:inherit}} a{{color:inherit}} .page{{width:min(1180px,calc(100% - 36px));margin:auto;padding:42px 0 100px}}
.mast{{border-top:8px solid var(--ink);border-bottom:1px solid var(--rule);padding:26px 0 22px;display:grid;grid-template-columns:1fr auto;gap:24px;align-items:end}}
.kicker,.meta,.day-label,.source{{font-family:"PingFang SC","Noto Sans CJK SC",sans-serif;letter-spacing:.12em;text-transform:uppercase}}
.kicker{{font-size:13px;color:var(--red)}} h1{{font-size:clamp(50px,9vw,112px);line-height:.88;margin:8px 0 0;font-weight:800;letter-spacing:-.06em}}
.issue{{text-align:right;font-size:14px}} .summary{{display:grid;grid-template-columns:2fr repeat(3,1fr);border-bottom:1px solid var(--rule)}}
.summary>div{{padding:22px 18px;border-right:1px solid var(--rule)}} .summary>div:last-child{{border:0}} .number{{font-size:34px;line-height:1}}
.timeline{{display:flex;gap:8px;overflow:auto;padding:18px 0 30px}} .timeline a{{min-width:78px;text-align:center;text-decoration:none;border-bottom:3px solid var(--ink);padding:10px 6px;font-family:sans-serif;font-size:13px}}
.backlog{{background:var(--ink);color:var(--paper);padding:24px 28px;margin:0 0 42px;display:none}} .backlog.show{{display:block}}
.day{{margin-top:52px}} .day-head{{display:grid;grid-template-columns:120px 1fr;gap:24px;border-bottom:3px solid var(--rule);align-items:end}}
.day-label{{font-size:13px;padding-bottom:10px}} h2{{font-size:clamp(32px,5vw,56px);line-height:1;margin:0 0 8px}}
.article{{display:grid;grid-template-columns:80px minmax(0,1fr) 280px;gap:26px;padding:34px 0;border-bottom:1px solid #b8b0a1}}
.rank{{font-size:54px;line-height:1;color:#b1a99b;font-style:italic}} .source{{font-size:11px;color:var(--red)}} h3{{font-size:clamp(25px,3vw,40px);line-height:1.16;margin:7px 0 15px;text-wrap:balance}}
.brief{{font-size:17px;white-space:pre-wrap;text-wrap:pretty}} .brief h1,.brief h2{{font-size:20px;margin:18px 0 6px}} .brief h3{{font-size:18px;margin:14px 0 4px}} .brief p{{margin:8px 0}} .update-note{{color:var(--red);margin-top:6px;font-size:15px}}
.controls{{border-left:1px solid var(--rule);padding-left:22px;font-family:"PingFang SC",sans-serif;font-size:14px}}
.status-row{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}} button,.link{{min-height:44px;border:1px solid var(--ink);background:transparent;padding:9px 12px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}}
button.active{{background:var(--ink);color:var(--paper)}} textarea{{width:100%;min-height:100px;background:rgba(255,255,255,.28);border:1px solid #8e877b;padding:10px;resize:vertical;font-size:16px}}
.secondary{{border-color:#8e877b;color:#4d4942}} .annotation{{background:var(--soft);padding:10px;margin:10px 0}} .review{{margin-top:12px;padding-top:12px;border-top:1px solid #aaa}}
.toast{{position:fixed;right:20px;bottom:20px;background:var(--ink);color:var(--paper);padding:12px 16px;display:none;z-index:5}}
.offline{{background:#d8c7a2;padding:10px 14px;font-family:sans-serif;font-size:13px;margin:14px 0;display:none}}
@media(max-width:860px){{.article{{grid-template-columns:48px 1fr}}.controls{{grid-column:2;border-left:0;border-top:1px solid var(--rule);padding:18px 0 0}}.summary{{grid-template-columns:1fr 1fr}}.mast{{grid-template-columns:1fr}}.issue{{text-align:left}}}}
@media(max-width:520px){{.page{{width:min(100% - 24px,1180px);padding-top:20px}}h1{{font-size:52px}}.article{{grid-template-columns:1fr;gap:12px}}.rank{{font-size:28px}}.controls{{grid-column:1}}.day-head{{grid-template-columns:1fr;gap:0}}.summary{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main class="page">
<header class="mast"><div><div class="kicker">100X · Weekly Knowledge Review</div><h1>知识萃取<br>周刊</h1></div><div class="issue">{week}<br><span id="generated"></span></div></header>
<div id="offline" class="offline">这是可携带的只读副本。要记录阅读、划线和评论，请通过本机服务打开 http://127.0.0.1:8765/ 。</div>
<section id="summary" class="summary"></section><nav id="timeline" class="timeline"></nav><aside id="backlog" class="backlog"></aside><div id="days"></div>
</main><div id="toast" class="toast"></div><script>const ISSUE={data};
const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const md=s=>esc(s).replace(/^### (.*)$/gm,'<h3>$1</h3>').replace(/^## (.*)$/gm,'<h2>$1</h2>').replace(/^# (.*)$/gm,'<h1>$1</h1>').replace(/^[-*] (.*)$/gm,'<p>— $1</p>').replace(/\\n\\n/g,'</p><p>');
const connected=location.protocol.startsWith('http'); if(!connected) $('#offline').style.display='block';
function done(a){{return !!a.state.read_at&&['commented','no_comment'].includes(a.state.disposition)}}
function toast(s){{const t=$('#toast');t.textContent=s;t.style.display='block';setTimeout(()=>t.style.display='none',1800)}}
async function api(path,opt={{}}){{if(!connected)throw Error('只读副本');const r=await fetch(path,{{headers:{{'Content-Type':'application/json'}},...opt}});if(!r.ok)throw Error(await r.text());return r.json()}}
function group(){{return ISSUE.articles.reduce((m,a)=>((m[a.added_on||'日期未知']??=[]).push(a),m),{{}})}}
function render(){{
 $('#generated').textContent='更新 '+ISSUE.generated_at.replace('T',' ');
 const c=ISSUE.counts;$('#summary').innerHTML=`<div><b>本周阅读判断</b><br><span class="meta">萃取在前，原文在后；未完成自动结转。</span></div><div><span class="number">${{c.total}}</span><br>进入周刊</div><div><span class="number">${{c.unread}}</span><br>尚未阅读</div><div><span class="number">${{c.carryover}}</span><br>往日结转</div>`;
 const groups=group();$('#timeline').innerHTML=Object.keys(groups).map(d=>`<a href="#d-${{d}}">${{d.slice(5)}}</a>`).join('');
 const carry=ISSUE.articles.filter(a=>a.carryover&&(!done(a)||a.state.update_pending));const pend=ISSUE.articles.filter(a=>a.state.update_pending).length;const b=$('#backlog');if(carry.length){{b.classList.add('show');b.innerHTML=`<b>未完阅读架</b> · ${{carry.length}} 篇从此前日期结转${{pend?` · ${{pend}} 篇有更新`:''}}<br>${{carry.slice(0,5).map(a=>esc(a.title)).join('　/　')}}`;}}
 $('#days').innerHTML=Object.entries(groups).map(([day,arts])=>`<section class="day" id="d-${{day}}"><header class="day-head"><div class="day-label">${{day}}</div><h2>${{arts.filter(a=>!a.carryover).length}} 篇新增 · ${{arts.filter(a=>!done(a)).length}} 篇待完成</h2></header>${{arts.map(card).join('')}}</section>`).join('');
}}
function card(a,i){{const anns=Array.isArray(a.state.annotations)?a.state.annotations:[];return `<article class="article" data-id="${{esc(a.article_id)}}"><div class="rank">${{String(i+1).padStart(2,'0')}}</div><div><div class="source">${{esc(a.source)}} · TIER ${{esc(a.signal_tier)}} · ${{Number(a.final_score).toFixed(2)}}${{a.carryover?' · 结转':''}}${{a.state.update_pending?' · 有更新':''}}</div><h3>${{esc(a.title)}}</h3><div class="brief">${{md(a.brief)}}</div>${{a.latest_update?`<div class="brief update-note">有更新 ${{esc(a.latest_update.date)}}：${{esc(a.latest_update.summary||'')}}</div>`:''}}</div><aside class="controls"><div class="status-row"><button class="${{a.state.read_at?'active':''}}" onclick="setRead('${{a.article_id}}',${{!a.state.read_at}})">已阅读</button><button class="${{a.state.disposition==='no_comment'?'active':''}}" onclick="noComment('${{a.article_id}}')">无需评论</button></div><button class="secondary" onclick="highlight('${{a.article_id}}')">保存当前划线</button><div>${{anns.map(x=>`<div class="annotation">「${{esc(x.quote)}}」${{x.comment?'<br>'+esc(x.comment):''}}</div>`).join('')}}</div><textarea id="comment-${{a.article_id}}" placeholder="留下你的判断、疑问或反例……">${{esc(a.state.comment||'')}}</textarea><div class="status-row"><button onclick="saveComment('${{a.article_id}}')">保存评论</button><button onclick="submitReview('${{a.article_id}}')">交给 AI</button></div><a class="link secondary" href="${{esc(a.obsidian_url)}}">回到 Obsidian 原文</a><div class="review">AI：${{esc(a.state.review?.status||'idle')}}${{a.state.review?.result?'<br>'+esc(a.state.review.result):''}}</div></aside></article>`}}
async function patch(id,p){{try{{const s=await api('/api/articles/'+id,{{method:'PATCH',body:JSON.stringify(p)}});Object.assign(ISSUE.articles.find(a=>a.article_id===id).state,s);render();toast('已保存')}}catch(e){{toast(e.message)}}}}
function setRead(id,v){{patch(id,{{read:v}})}} function noComment(id){{patch(id,{{read:true,disposition:'no_comment'}})}}
function saveComment(id){{patch(id,{{read:true,comment:$('#comment-'+CSS.escape(id)).value}})}}
async function highlight(id){{const sel=window.getSelection();const quote=sel?sel.toString().trim():'';const node=sel?.anchorNode;const el=node?.nodeType===1?node:node?.parentElement;const owner=el?.closest?.('.article');if(!quote||owner?.dataset.id!==id)return toast('请先在这篇萃取正文中划线');const comment=prompt('为这段划线补一句评论（可以留空）','')??'';try{{const s=await api('/api/articles/'+id+'/annotations',{{method:'POST',body:JSON.stringify({{quote,comment}})}});Object.assign(ISSUE.articles.find(a=>a.article_id===id).state,s);render();toast('划线已保存')}}catch(e){{toast(e.message)}}}}
async function submitReview(id){{try{{const s=await api('/api/articles/'+id+'/submit',{{method:'POST',body:'{{}}'}});Object.assign(ISSUE.articles.find(a=>a.article_id===id).state,s);render();toast('已提交定向再萃取')}}catch(e){{toast(e.message)}}}}
render();</script></body></html>'''


def build_issue(root: Path, week: str | None = None) -> Path:
    payload = issue_payload(root, week)
    target = Path(root) / str(payload["week"]) / ISSUE_NAME.format(week=payload["week"])
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(_html_document(payload), encoding="utf-8")
    os.replace(tmp, target)
    return target


ReviewCallable = Callable[[Article, dict[str, object]], str]


def build_reviewer(config, root: Path) -> ReviewCallable:
    """Create the explicit-submit reviewer used by the localhost service."""
    provider = create_live_provider(config.llm)
    root = Path(root).resolve()

    def review(article: Article, state: dict[str, object]) -> str:
        article_path = (root / article.path).resolve()
        article_path.relative_to(root)
        source_text = article_path.read_text(encoding="utf-8")[:60_000]
        feedback = {
            "comment": state.get("comment") or "",
            "annotations": state.get("annotations") or [],
        }
        prompt = """你是 100X 知识萃取系统的定向复核器。只围绕用户评论和划线重新阅读文章，不做泛泛总结。
输出中文 Markdown，依次给出：
1. 用户真正关注的问题；
2. 原文中支持或反驳它的证据（区分事实、作者推断、用户判断）；
3. 一条可记住的结论；
4. 仍需验证的问题。
不得编造原文没有的事实。"""
        content = "用户反馈：\n" + json.dumps(feedback, ensure_ascii=False, indent=2) + "\n\n文章：\n" + source_text
        result = provider.complete(content, prompt, stage="targeted_review")
        if isinstance(result, TypedError):
            raise RuntimeError(result.message)
        return result.strip()

    return review


class ReviewQueue:
    def __init__(self, store: MagazineStore, reviewer: ReviewCallable | None) -> None:
        self.store = store
        self.reviewer = reviewer

    def submit(self, article_id: str) -> dict[str, object]:
        state = self.store.mark_review(article_id, status="queued")
        thread = threading.Thread(target=self._run, args=(article_id,), daemon=True)
        thread.start()
        return state

    def _run(self, article_id: str) -> None:
        try:
            article, state, _ = self.store.find(article_id)
            self.store.mark_review(article_id, status="running")
            if self.reviewer is None:
                raise RuntimeError("AI reviewer is not configured")
            result = self.reviewer(article, json.loads(json.dumps(state, ensure_ascii=False)))
            self.store.mark_review(article_id, status="done", result=result)
            build_issue(self.store.root, current_week())
        except Exception as exc:
            self.store.mark_review(article_id, status="failed", error=str(exc)[:500])


class MagazineServer:
    def __init__(self, root: Path, *, host: str = "127.0.0.1", port: int = 8765, reviewer: ReviewCallable | None = None) -> None:
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("magazine service must bind to loopback")
        self.root = Path(root).resolve()
        self.store = MagazineStore(self.root)
        self.reviews = ReviewQueue(self.store, reviewer)
        self.server = ThreadingHTTPServer((host, port), self._handler())

    def _handler(self):
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def _allowed(self) -> bool:
                host = self.headers.get("Host", "").split(":", 1)[0]
                origin = self.headers.get("Origin", "")
                return host in {"127.0.0.1", "localhost"} and (
                    not origin or origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost")
                )

            def _json(self, value: object, status: int = 200) -> None:
                body = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def _body(self) -> dict[str, object]:
                size = int(self.headers.get("Content-Length", "0"))
                if size > MAX_BODY_BYTES:
                    raise ValueError("request body too large")
                value = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(value, dict):
                    raise ValueError("JSON object required")
                return value

            def do_GET(self):
                if not self._allowed(): return self._json({"error": "loopback only"}, 403)
                if self.path in {"/", "/issues/current"}:
                    path = build_issue(parent.root)
                    body = path.read_bytes(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
                if self.path == "/api/issues/current": return self._json(issue_payload(parent.root))
                return self._json({"error": "not found"}, 404)

            def _article_route(self):
                match = re.fullmatch(r"/api/articles/([A-Za-z0-9._-]+)(/annotations|/submit)?", urllib.parse.urlsplit(self.path).path)
                return match.groups() if match else None

            def do_PATCH(self): self._mutate("patch")
            def do_POST(self): self._mutate("post")

            def _mutate(self, method: str):
                if not self._allowed(): return self._json({"error": "loopback only"}, 403)
                route = self._article_route()
                if not route: return self._json({"error": "not found"}, 404)
                article_id, suffix = route
                try:
                    body = self._body()
                    if method == "patch" and suffix is None: state = parent.store.update(article_id, body)
                    elif method == "post" and suffix == "/annotations": state = parent.store.add_annotation(article_id, body)
                    elif method == "post" and suffix == "/submit": state = parent.reviews.submit(article_id)
                    else: return self._json({"error": "method not allowed"}, 405)
                    build_issue(parent.root)
                    return self._json(state)
                except KeyError: return self._json({"error": "article not found"}, 404)
                except (ValueError, json.JSONDecodeError) as exc: return self._json({"error": str(exc)}, 400)

            def log_message(self, format, *args):
                return

        return Handler

    def serve_forever(self) -> None:
        self.server.serve_forever(poll_interval=0.5)

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="100x-magazine", daemon=True)
        thread.start()
        return thread

    def close(self) -> None:
        self.server.shutdown(); self.server.server_close()


def _configured_root() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    loader = ConfigLoader(project_root=project_root)
    config = loader.load()
    if not config.outputs.obsidian_root:
        raise RuntimeError("outputs.obsidian_root is not configured")
    return loader.expand_path(config.outputs.obsidian_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and serve the 100X weekly reading magazine")
    parser.add_argument("command", choices=["build", "digest", "serve", "dedupe"])
    parser.add_argument("--root", default="")
    parser.add_argument("--week", default="")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--dry-run", action="store_true", help="dedupe: report only, touch nothing")
    parser.add_argument("--restore", action="store_true", help="dedupe: only restore orphans from .trash-dedup")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser() if args.root else _configured_root()
    if args.command == "dedupe":
        from .outputs.dedupe import dedupe_vault, restore_orphans

        if args.restore:
            restored = restore_orphans(root, dry_run=args.dry_run)
            print(json.dumps({"restored": [str(p) for p in restored], "dry_run": args.dry_run}, ensure_ascii=False))
            return 0
        report = dedupe_vault(root, dry_run=args.dry_run)
        print(json.dumps({
            "dry_run": args.dry_run,
            "restored": report.restored,
            "merged_groups": report.merged_groups,
            "trashed_files": report.trashed_files,
            "state_migrations": report.state_migrations,
            "weeks_rebuilt": report.weeks_rebuilt,
            "errors": report.errors,
        }, ensure_ascii=False, indent=2))
        return 1 if report.errors else 0
    if args.command in {"build", "digest"}:
        path = build_issue(root, args.week or None)
        payload = issue_payload(root, args.week or None)
        if args.command == "digest":
            print(json.dumps({"path": str(path), "week": payload["week"], "counts": payload["counts"]}, ensure_ascii=False))
        else:
            print(path)
        return 0
    server = MagazineServer(root, port=args.port)
    print(f"100X magazine: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
