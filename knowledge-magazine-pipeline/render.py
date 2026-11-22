#!/usr/bin/env python3
"""知识杂志 · 渲染器（确定性，无 LLM）

editor JSON + collect JSON → 校验 → 合并 → 杂志/（今日、本周、index、data）+ hub 总入口
+ vault 周目录回链。原子写入、幂等重跑、保留最近成功版本；任何校验 FAIL 都不碰生产文件。

用法: python3 render.py --data <editor.json> --collect <collect.json> [--dry-run]
"""
from __future__ import annotations

import os as _os
from pathlib import Path as _P
def _km_home(rel: str) -> Path:
    """仓库版：路径经环境变量注入（KM_HUB / KM_VAULT_ROOT），缺省落到当前用户主目录同名结构；
    本机生产目录继续以默认值工作，测试与 CI 用环境变量或 mock 覆盖。"""
    env, *sub = rel.split('|')
    return _P(_os.environ.get(env) or _P.home().joinpath(*sub))

import argparse
import datetime as dt
import hashlib
import html as H
import json
import os
import re
import sys
import shutil
from pathlib import Path

HUB = _km_home("KM_HUB|Desktop|知识杂志")
MAG = HUB / "杂志"
DATA_DIR = MAG / "data"
STATE = HUB / "模块-知识杂志" / "state"
VAULT = _km_home("KM_VAULT|Documents|Obsidian Vault|信息源")

from collect import obsidian_url_to_file, write_publication_status
from content import md_field, blocks_to_html, parse_markdown

KINDS = {"fact", "opinion", "inference"}
REL = "../"  # 页面相对 hub 的前缀；批次页在 batches/<id>/ 下时为 "../"（见 main）
VSTAT = {"verified_multi", "single_source", "unverified"}
WIRE_NAME = re.compile(r"今日-(\d{4}-\d{2}-\d{2})\.html$")
WEEK_NAME = re.compile(r"本周-(\d{4}-\d{2}-W\d)\.html$")


def fail(msg: str) -> None:
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(1)


def esc(s: str) -> str:
    return H.escape(str(s or ""), quote=True)


# ---------------------------------------------------------------- 校验

def validate(ed: dict, co: dict) -> list:
    errs = []
    if ed.get("schema") != "knowledge-magazine-editor/1":
        errs.append("schema 不对")
    if ed.get("material_date") != co["material_date"]:
        errs.append(f"material_date {ed.get('material_date')} != 采集 {co['material_date']}")
    if ed.get("week") != co["week"]:
        errs.append(f"week {ed.get('week')} != 采集 {co['week']}")

    known = set(co["known_urls"])
    week_known = known | set((co.get("week_urls") or {}).keys())  # 本周各期短讯证据（周视角/交叉核验可用）
    today = ed.get("today") or {}
    v = today.get("verdict", "")
    if not v or len(v) > 200:
        errs.append("today.verdict 必须为 1 句且 ≤200 字")

    picks = today.get("picks") or []
    if not 2 <= len(picks) <= 6:
        errs.append(f"picks 数量 {len(picks)} 应在 2-6")
    for i, p in enumerate(picks):
        if p.get("kind") not in KINDS:
            errs.append(f"picks[{i}].kind 非法")
        if not p.get("why"):
            errs.append(f"picks[{i}] 缺 why")
        for s in p.get("sources") or []:
            if s.get("url") not in known:
                errs.append(f"picks[{i}] 来源不在采集集: {s.get('url')}")
        if not p.get("sources"):
            errs.append(f"picks[{i}] 无来源")

    # 今日分组必须完整覆盖全部短讯条目（去重合并且不丢条）
    wire_hrefs = []
    for sec in co["wire"]["sections"]:
        for it in sec["items"]:
            wire_hrefs.append(it["href"])
    grouped = []
    for g in today.get("groups") or []:
        for href in g.get("hrefs") or []:
            grouped.append(href)
    if sorted(grouped) != sorted(wire_hrefs):
        miss = set(wire_hrefs) - set(grouped)
        dup = [x for x in grouped if grouped.count(x) > 1]
        errs.append(f"今日分组与短讯条目不一致 缺:{len(miss)} 重:{len(set(dup))}")
    notes = today.get("item_notes") or {}
    for href in notes:
        if href not in known:
            errs.append(f"item_notes 键不在采集集: {href}")

    wk = ed.get("week_view") or {}
    if not (wk.get("what_grew") or "").strip():
        errs.append("week.what_grew 缺失")
    topics = wk.get("topics") or []
    if not 2 <= len(topics) <= 6:
        errs.append(f"topics 数量 {len(topics)} 应在 2-6")
    wdates = set(co["week_dates"])
    for i, t in enumerate(topics):
        devs = t.get("developments") or []
        if len(devs) < 2:
            errs.append(f"topics[{i}] 发展不足 2 条")
        for d in devs:
            if d.get("date") not in wdates:
                errs.append(f"topics[{i}] 日期越界: {d.get('date')}")
            for s in d.get("sources") or []:
                if s.get("url") not in week_known:
                    errs.append(f"topics[{i}] 来源不在本周采集集（跨周/编造，同周短讯链接允许）: {s.get('url')}")
        for r in t.get("deep_read_ids") or []:
            if co["weekly"].get("articles") and not any(a["article_id"] == r for a in co["weekly"]["articles"]):
                errs.append(f"topics[{i}] deep_read_id 无对应文章: {r}")

    checks = ed.get("cross_checks") or []
    if not 2 <= len(checks) <= 3:
        errs.append(f"cross_checks 数量 {len(checks)} 应在 2-3")
    for i, c in enumerate(checks):
        srcs = c.get("sources") or []
        if len(srcs) < 2:
            errs.append(f"cross_checks[{i}] 证据不足 2 条")
        if not (c.get("primary_source") or "").strip():
            errs.append(f"cross_checks[{i}] 缺 primary_source（主证据来源归属）")
        # 独立性按底层原始证据计票：同一 primary_key 的多家转述只算一票
        pkeys = {s.get("primary_key") for s in srcs
                 if s.get("independent") and s.get("primary_key")}
        if c.get("status") == "verified_multi" and len(pkeys) < 2:
            errs.append(f"cross_checks[{i}] verified_multi 需 ≥2 个不同原始证据（primary_key），多家转述同源不算两票")
        for s in srcs:
            if s.get("url") not in week_known and not s.get("fetched"):
                errs.append(f"cross_checks[{i}] 来源既不在本周采集集也未标注实抓: {s.get('url')}")
            if s.get("independent") and not s.get("primary_key"):
                errs.append(f"cross_checks[{i}] independent 来源缺 primary_key")
        if not (c.get("counterpoint") or "").strip():
            errs.append(f"cross_checks[{i}] 缺反例/限制")
    # Obsidian 回链存在性：编辑引用的 obsidian:// 必须落到真实文件
    for u in obsidian_urls_in(ed):
        if obsidian_url_to_file(u) is None:
            errs.append(f"obsidian 回链不可解析到真实文件: {u[:90]}")
    return errs


def obsidian_urls_in(ed: dict) -> set:
    out = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "url" and isinstance(v, str) and v.startswith("obsidian://"):
                    out.add(v)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(ed)
    return out


# ---------------------------------------------------------------- 合并

def merge(ed: dict, co: dict) -> dict:
    arts = {a["article_id"]: a for a in co["weekly"]["articles"]}
    today = co["material_date"]

    # 今日分组：编辑分组 + 条目全量
    items_by_href = {}
    for sec in co["wire"]["sections"]:
        for it in sec["items"]:
            items_by_href[it["href"]] = {**it, "wire_section": sec["name"]}
    groups = []
    for g in ed["today"].get("groups") or []:
        groups.append({
            "title": g["title"],
            "thread": g.get("thread", ""),
            "items": [dict(items_by_href[h],
                           note=(ed["today"].get("item_notes") or {}).get(h, {}).get("note", ""),
                           kind=(ed["today"].get("item_notes") or {}).get(h, {}).get("kind", "fact"))
                      for h in g["hrefs"]],
        })

    new_notes = [a for a in co["weekly"]["articles"] if a["added_on"] == today]
    deep_ids = set()
    for t in ed["week_view"].get("topics") or []:
        deep_ids.update(t.get("deep_read_ids") or [])
    deep = [arts[i] for i in sorted(deep_ids) if i in arts]

    prev = co.get("prev_weekly") or {}
    cur_ids = {a["article_id"] for a in co["weekly"]["articles"]}
    delta_new = len(cur_ids - set(prev.get("ids") or []))
    delta_gone = len(set(prev.get("ids") or []) - cur_ids)

    week_urls = co.get("week_urls") or {}

    picks = []
    for pk in ed["today"]["picks"]:
        f = md_field(pk.get("why"), 400)
        picks.append({**pk, "why": f["flat"], **({"why_md": f["blocks"]} if "blocks" in f else {})})

    groups_md = []
    for g in groups:
        items = []
        for it in g["items"]:
            f = md_field(it.get("note") or "", 300)
            it = {**it, "note": f["flat"], **({"note_md": f["blocks"]} if "blocks" in f else {})}
            items.append(it)
        groups_md.append({**g, "items": items})

    def md_notes(arr):
        out = []
        for a in arr:
            f = md_field(a.get("brief"), 460)
            out.append({**a, "brief": f["flat"], **({"brief_md": f["blocks"]} if "blocks" in f else {})})
        return out

    def md_checks(arr):
        out = []
        for c in arr:
            parts = {}
            for k in ("verdict", "counterpoint"):
                f = md_field(c.get(k) or "", 260)
                parts[k] = f["flat"]
                if "blocks" in f:
                    parts[k + "_md"] = f["blocks"]
            out.append({**c, **parts})
        return out

    topics_md = []
    for t in ed["week_view"].get("topics") or []:
        devs = []
        for d in t.get("developments") or []:
            f = md_field(d.get("event"), 300)
            srcs = []
            for src in d.get("sources") or []:
                wu = week_urls.get(src.get("url"))
                srcs.append({**src, **({"wire_date": wu["date"], "wire_issue": wu["issue_no"]} if wu else {})})
            devs.append({**d, "event": f["flat"], **({"event_md": f["blocks"]} if "blocks" in f else {}), "sources": srcs})
        cps = [md_field(x, 260)["flat"] for x in t.get("counterpoints") or []]
        tvs = [md_field(x, 260)["flat"] for x in t.get("to_verify") or []]
        th = md_field(t.get("thread") or "", 260)
        topics_md.append({**t, "thread": th["flat"], **({"thread_md": th["blocks"]} if "blocks" in th else {}),
                          "developments": devs, "counterpoints": cps, "to_verify": tvs})

    what = md_field(ed["week_view"]["what_grew"], 500)
    deep_md = md_notes(deep)

    merged = {
        "schema": "knowledge-magazine/1",
        "material_date": today,
        "week": co["week"],
        "week_dates": co["week_dates"],
        "today": {
            "date": today,
            "issue_no": co["wire"]["issue_no"],
            "oneline_take": co["wire"]["oneline_take"],
            "verdict": md_field(ed["today"]["verdict"], 200)["flat"],
            "picks": picks,
            "groups": groups_md,
            "market_rows": co["wire"]["market_rows"],
            "new_notes": md_notes(new_notes),
            "cross_checks": md_checks(ed.get("cross_checks") or []),
        },
        "week": co["week"],
        "week_view": {
            "week": co["week"],
            "what_grew": what["flat"], **({"what_grew_md": what["blocks"]} if "blocks" in what else {}),
            "topics": topics_md,
            "deep_reads": deep_md,
            "prev_week": prev.get("week"),
            "delta": {"new": delta_new, "gone": delta_gone},
            "week_wires": co["week_wires"],
        },
        "coverage": build_coverage(co, ed),
    }
    return merged


def build_coverage(co: dict, ed: dict) -> dict:
    inp = co["inputs"]
    lag = ""
    for c in inp["weekly"]["all_candidates"]:
        if c["path"] != inp["weekly"]["path"] and c["mtime"][:10] != inp["weekly"]["mtime"][:10]:
            lag = f"周刊存在更新较慢的副本（{c['path']} @ {c['mtime']}），已采用最新一份"
    missing = list(co["stale_sources"])
    notes = list(ed.get("coverage_notes") or [])
    if co["material_date"] != co["collect_date"]:
        notes.append(f"今日短讯尚未出刊，本期基于最新一期 {co['material_date']}")
    return {
        "wire": {"date": inp["wire"]["date"], "mtime": inp["wire"]["mtime"], "items": inp["wire"]["item_count"]},
        "weekly": {"week": inp["weekly"]["week"], "mtime": inp["weekly"]["mtime"], "articles": inp["weekly"]["article_count"]},
        "obsidian_week": {"dir": co["obsidian_week"]["dir"], "notes": co["obsidian_week"]["note_count"]},
        "missing": missing, "notes": notes, "hub_copy_lag": lag,
        "lanes": list(co.get("lanes") or []),
    }


# ---------------------------------------------------------------- HTML

CSS = """
:root{--paper:#FBFAF8;--card:#F4F1EB;--ink:#1C1A17;--muted:#8A8478;--rule:#1C1A17;--line:#E3DED4;--red:#a63228;--soft:#EFEBE2;color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:"Songti SC","STSong","Noto Serif SC",serif;line-height:1.75;-webkit-font-smoothing:antialiased}
a{color:inherit}
.page{width:min(920px,calc(100% - 36px));margin:auto;padding:28px 0 80px}
.mast{border-top:7px solid var(--ink);border-bottom:1px solid var(--rule);padding:20px 0 16px;margin-bottom:6px}
.kicker,.meta{font-family:"PingFang SC","Noto Sans CJK SC",sans-serif;letter-spacing:.12em}
.kicker{font-size:12px;color:var(--red);margin:0}
h1{font-size:clamp(30px,6vw,54px);line-height:1.05;margin:8px 0 0;font-weight:800;letter-spacing:-.03em}
.meta{font-size:12.5px;color:var(--muted);margin-top:8px}
.verdict{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--red);padding:16px 20px;margin:20px 0 6px;font-size:17.5px;font-weight:600}
nav.tabs{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0 4px;font-family:"PingFang SC",sans-serif;font-size:14px}
nav.tabs a{text-decoration:none;border:1px solid var(--rule);padding:6px 14px;border-radius:99px}
nav.tabs a.cur{background:var(--ink);color:var(--paper)}
h2.sec{font-size:21px;font-weight:800;margin:34px 0 4px;letter-spacing:-.01em}
h2.sec .sub{font-weight:400;font-size:14px;color:var(--muted);margin-left:10px}
p.thread{color:var(--muted);margin:2px 0 12px;font-size:15px}
.item{border-bottom:1px dotted var(--line);padding:12px 0}
.item .t{font-size:16.5px;font-weight:700;line-height:1.45}
.item .t a{text-decoration:none;border-bottom:1px solid var(--line)}
.item .t a:hover{color:var(--red)}
.item .en{display:block;font-size:12.5px;color:var(--muted);font-family:"PingFang SC",sans-serif;margin-top:2px}
.item .m{font-size:14.5px;color:#3d3a33;margin-top:6px}
.item .meta,.meta-row{font-family:"PingFang SC",sans-serif;font-size:12px;color:var(--muted);margin-top:4px}
.badge{display:inline-block;font-family:"PingFang SC",sans-serif;font-size:11px;letter-spacing:.08em;border:1px solid var(--rule);border-radius:99px;padding:1px 8px;margin-right:6px;vertical-align:2px}
.badge.fact{border-color:#5a7d5a;color:#3f5f3f}
.badge.opinion{border-color:#b08a3e;color:#8a6a25}
.badge.inference{border-color:var(--red);color:var(--red)}
.badge.verify{border-color:var(--red);color:var(--paper);background:var(--red)}
.note{background:var(--soft);padding:10px 14px;margin:8px 0;font-size:14.5px}
.note b{font-family:"PingFang SC",sans-serif;font-size:12px;letter-spacing:.1em;color:var(--red)}
.pick{background:var(--card);border:1px solid var(--line);padding:14px 18px;margin:12px 0}
.pick .t{font-size:17px;font-weight:800}
.pick .why{margin:6px 0 4px;font-size:15px}
.pick .src{font-family:"PingFang SC",sans-serif;font-size:12.5px;color:var(--muted)}
.pick .src a{margin-right:10px}
.check{border:1px solid var(--line);background:var(--card);padding:14px 18px;margin:12px 0}
.check .claim{font-weight:800;font-size:16px}
.check .row{font-size:14.5px;margin-top:6px}
.check .row b{font-family:"PingFang SC",sans-serif;font-size:12px;letter-spacing:.1em;color:var(--red)}
.check a{text-decoration:none;border-bottom:1px dotted var(--rule)}
.topic{border:1px solid var(--line);background:var(--card);padding:4px 20px 14px;margin:16px 0}
.topic > h3{font-size:18.5px;font-weight:800;margin:14px 0 2px}
.topic .thread{color:var(--muted);font-size:14.5px;margin:0 0 10px}
.dev{display:flex;gap:12px;padding:8px 0;border-bottom:1px dotted var(--line);font-size:15px}
.dev .d{font-family:"PingFang SC",sans-serif;font-size:12.5px;color:var(--red);white-space:nowrap;padding-top:3px}
.dev .s{font-family:"PingFang SC",sans-serif;font-size:12px;color:var(--muted);margin-top:2px}
.dev .s a{text-decoration:none;border-bottom:1px dotted var(--rule)}
.deep{border-left:3px solid var(--rule);padding:2px 0 2px 16px;margin:14px 0}
.deep .t{font-weight:800;font-size:16px}
.deep .t a{text-decoration:none}
.deep .brief{font-size:14.5px;color:#3d3a33;margin-top:4px;white-space:pre-wrap}
table.odds{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:8px}
table.odds th{font-family:"PingFang SC",sans-serif;font-size:11.5px;letter-spacing:.08em;color:var(--muted);border-bottom:1px solid var(--rule);text-align:left;padding:6px 8px}
table.odds td{border-bottom:1px dotted var(--line);padding:7px 8px;vertical-align:top}
table.odds td.q a{text-decoration:none;font-weight:600}
.coverage{background:var(--soft);padding:14px 18px;margin-top:26px;font-size:13.5px;font-family:"PingFang SC",sans-serif}
.coverage h4{margin:0 0 6px;font-size:12.5px;letter-spacing:.1em;color:var(--muted)}
.coverage ul{margin:4px 0;padding-left:18px}
.foot{margin-top:30px;font-family:"PingFang SC",sans-serif;font-size:12px;color:var(--muted);letter-spacing:.06em}
.arch{list-style:none;margin:8px 0 0;padding:0;columns:2;column-gap:36px}
.arch li{break-inside:avoid;border-bottom:1px dotted var(--line);padding:6px 0;font-size:14.5px}
.arch a{text-decoration:none}
.arch a:hover{color:var(--red)}
@media(max-width:640px){.arch{columns:1}.dev{flex-direction:column;gap:2px}}
details summary{cursor:pointer;font-weight:700;font-size:14.5px;font-family:"PingFang SC",sans-serif;margin-top:8px}
details{margin:6px 0}
"""


def kind_badge(kind: str, note_kind: bool = False) -> str:
    label = {"fact": "事实", "opinion": "观点", "inference": "推断"}.get(kind, kind)
    return f'<span class="badge {kind}">{label}</span>' if note_kind else ""


def rich(obj: dict, key: str) -> str:
    """内容层出口：有块表示走 blocks_to_html，否则纯文本转义。两出口共用同一块结构。"""
    md = obj.get(key + "_md")
    return blocks_to_html(md) if md else esc(obj.get(key) or "")


def src_links(sources: list) -> str:
    out = []
    for s in sources:
        tag = "独立" if s.get("independent") else ("实抓" if s.get("fetched") else "")
        wd = f'（{s["wire_date"][5:]} 短讯）' if s.get("wire_date") else ""
        out.append(f'<a href="{esc(s["url"])}" target="_blank">{esc(s.get("label") or "来源")}</a>{wd}'
                   + (f'（{tag}）' if tag else ""))
    return " ".join(out)


def render_today(m: dict, archive: list) -> str:
    REL = globals()["REL"]
    t = m["today"]
    picks = "".join(
        f'<div class="pick"><div class="t">{esc(p["title"])}</div>'
        f'<div class="why">{rich(p, "why")}</div>'
        f'<div class="src">{kind_badge(p["kind"], True)}{"<span class=\"badge verify\">待核</span>" if p.get("status") == "unverified" else ""}{src_links(p["sources"])}</div></div>'
        for p in t["picks"])
    groups = ""
    for g in t["groups"]:
        rows = ""
        for it in g["items"]:
            note = f'<div class="note"><b>编辑注</b> {rich(it, "note")}</div>' if it.get("note") else ""
            badges = kind_badge(it.get("kind") or "fact", True)
            rows += (
                f'<div class="item"><div class="t"><a href="{esc(it["href"])}" target="_blank">{esc(it["title"])}</a>'
                + (f'<span class="en">{esc(it["original"])}</span>' if it.get("original") else "")
                + f'</div><div class="m">{esc(it.get("why") or "")}</div>'
                + f'<div class="meta">{badges}{" · ".join(esc(x) for x in it.get("meta") or [])}</div>{note}</div>')
        thread = f'<p class="thread">{esc(g["thread"])}</p>' if g.get("thread") else ""
        groups += f'<h2 class="sec">{esc(g["title"])}</h2>{thread}{rows}'

    deeps = ""
    for a in t["new_notes"]:
        obs = f' · <a href="{esc(a["obsidian_url"])}">Obsidian 原文</a>' if a.get("obsidian_url") else ""
        src = f' · <a href="{esc(a["url"])}" target="_blank">原始出处</a>' if a.get("url") else ""
        deeps += (f'<div class="deep"><div class="t">{esc(a["title"])}</div>'
                  f'<div class="brief">{rich(a, "brief")}</div>'
                  f'<div class="meta-row">分层 {a.get("signal_tier")} · 评分 {a.get("final_score")}{src}{obs}</div></div>')

    checks = ""
    for c in t["cross_checks"]:
        st = {"verified_multi": "≥2 条不同原始证据", "single_source": "主证据单源（多家转述不计两票）", "unverified": "未核验"}.get(c["status"], c["status"])
        prim = f'<div class="row"><b>主证据</b> {esc(c.get("primary_source") or "—")}</div>' if c.get("primary_source") else ""
        checks += (f'<div class="check"><div class="claim">{esc(c["claim"])}</div>'
                   f'{prim}'
                   f'<div class="row"><b>证据</b> {src_links(c["sources"])}</div>'
                   f'<div class="row"><b>核验</b> {rich(c, "verdict")}（{st}）</div>'
                   f'<div class="row"><b>分歧/限制</b> {rich(c, "counterpoint")}</div></div>')

    odds = ""
    if t["market_rows"]:
        rows = "".join(
            f'<tr><td class="q"><a href="{esc(r["href"])}" target="_blank" title="{esc(r["question"])}">{esc(r["title"])}</a></td>'
            f'<td>{esc(r["prob"])}</td><td>{esc(r["trend"])}</td><td>{esc(r["vol"])}</td></tr>'
            for r in t["market_rows"])
        odds = (f'<h2 class="sec">市场变化<span class="sub">预测市场 · 概率/趋势/事件成交量</span></h2>'
                f'<table class="odds"><thead><tr><th>问题</th><th>概率</th><th>趋势</th><th>成交量</th></tr></thead><tbody>{rows}</tbody></table>')

    cov = m["coverage"]
    cov_html = (f'<div class="coverage"><h4>覆盖与缺口</h4><ul>'
                f'<li>每日短讯 {esc(cov["wire"]["date"])}（{cov["wire"]["items"]} 条，文件 {esc(cov["wire"]["mtime"])}）</li>'
                f'<li>知识萃取周刊 {esc(cov["weekly"]["week"])}（{cov["weekly"]["articles"]} 篇，文件 {esc(cov["weekly"]["mtime"])}）</li>'
                f'<li>Obsidian 周信源 {cov["obsidian_week"]["notes"]} 篇笔记</li>'
                + "".join(
                    (f'<li>栏目 {esc(x["lane"])}：目录缺失</li>' if x.get("missing")
                     else f'<li>栏目 {esc(x["lane"])}：{x["files"]} 期，最新 {esc(x["latest"]["name"] if x.get("latest") else "无")}</li>')
                    for x in cov.get("lanes") or [])
                + "".join(f'<li>缺源：{esc(x)}</li>' for x in cov["missing"])
                + "".join(f'<li>{esc(x)}</li>' for x in cov["notes"])
                + (f'<li>{esc(cov["hub_copy_lag"])}</li>' if cov["hub_copy_lag"] else "")
                + '</ul></div>')

    arch_rows = "".join(f'<li><a href="{esc(a["file"])}">{esc(a["label"])}</a></li>' for a in archive[:12])
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>知识杂志 · 今日 {t["date"]}</title><style>{CSS}</style></head><body><div class="page">
<header class="mast"><p class="kicker">KNOWLEDGE MAGAZINE · 今日</p>
<h1>{t["date"]} 的知识杂志</h1>
<p class="meta">材料日 {t["date"]} · 短讯第 {t["issue_no"]} 期 · 批次 {MAG_BUILD} · 生成 {MAG_BUILT_AT}</p></header>
<nav class="tabs"><a class="cur" href="#">今日</a><a href="本周-{m["week"]}.html">本周</a><a href="index.html">往期</a></nav>
<div class="verdict">{esc(t["verdict"])}</div>
<h2 class="sec">今日重点<span class="sub">主编精选 · {len(t["picks"])} 条</span></h2>
{picks}
<h2 class="sec">变了什么<span class="sub">按主题分组 · 全量短讯</span></h2>
{groups}
{odds}
<h2 class="sec">值得深读<span class="sub">今日新增萃取笔记 · {len(t["new_notes"])} 篇</span></h2>
{deeps or '<p class="thread">今日无新增萃取笔记。</p>'}
<h2 class="sec">交叉核验<span class="sub">重要判断的证据与反例</span></h2>
{checks}
{cov_html}
<p class="foot">本周脉络见 <a href="本周-{m["week"]}.html">本周 {m["week"]}</a> · 短讯原文 <a href="{REL}每日短讯/每日短讯-{t["date"]}.html">每日短讯 {t["date"]}</a> · 周刊 <a href="{REL}知识萃取/知识萃取周刊 {m["week"]}.html">知识萃取周刊 {m["week"]}</a></p>
<details><summary>往期</summary><ul class="arch">{arch_rows}</ul></details>
</div></body></html>"""


def render_week(m: dict, archive: list) -> str:
    REL = globals()["REL"]
    w = m["week_view"]
    topics = ""
    for t in w["topics"]:
        arts = {a["article_id"]: a for a in m["week_view"]["deep_reads"]}
        devs = ""
        for d in t["developments"]:
            devs += (f'<div class="dev"><span class="d">{esc(d["date"][5:])}</span><div>'
                     f'<div>{rich(d, "event")}</div><div class="s">{src_links(d["sources"])}</div></div></div>')
        counters = "".join(f'<li>{esc(x)}</li>' for x in t.get("counterpoints") or [])
        verifs = "".join(f'<li>{esc(x)}</li>' for x in t.get("to_verify") or [])
        deeps = "".join(
            f'<div class="deep"><div class="t">{esc(arts[i]["title"])}</div><div class="brief">{rich(arts[i], "brief")}</div>'
            f'<div class="meta-row"><a href="{esc(arts[i]["obsidian_url"])}">Obsidian 原文</a> · <a href="{esc(arts[i]["url"])}" target="_blank">原始出处</a></div></div>'
            for i in t.get("deep_read_ids") or [] if i in arts)
        topics += (f'<div class="topic" id="{esc(t["slug"])}"><h3>{esc(t["title"])}</h3>'
                   f'<p class="thread">{esc(t["thread"])}</p>{devs}'
                   + (f'<details open><summary>证据与分歧</summary><ul>{counters}</ul></details>' if counters else "")
                   + (f'<details><summary>仍待验证</summary><ul>{verifs}</ul></details>' if verifs else "")
                   + (f'<details><summary>值得展开的深读（{len(t.get("deep_read_ids") or [])}）</summary>{deeps}</details>' if deeps else "")
                   + '</div>')

    arch_rows = "".join(f'<li><a href="{esc(a["file"])}">{esc(a["label"])}</a></li>' for a in archive[:16])
    prev = f'上周 {w["prev_week"]}：本周新进 {w["delta"]["new"]} 篇、移出 {w["delta"]["gone"]} 篇' if w.get("prev_week") else ''
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>知识杂志 · 本周 {w["week"]}</title><style>{CSS}</style></head><body><div class="page">
<header class="mast"><p class="kicker">KNOWLEDGE MAGAZINE · 本周</p>
<h1>{w["week"]} 一周脉络</h1>
<p class="meta">{esc(m["week_dates"][0])} ~ {esc(m["week_dates"][-1])} · 周内滚动重建 · 批次 {MAG_BUILD} · 生成 {MAG_BUILT_AT}</p></header>
<nav class="tabs"><a href="今日-{m["material_date"]}.html">今日</a><a class="cur" href="#">本周</a><a href="index.html">往期</a></nav>
<div class="verdict">{rich(w, "what_grew")}</div>
<h2 class="sec">主题<span class="sub">跨日对照 · 发展 / 分歧 / 待核</span></h2>
{topics}
<div class="coverage"><h4>本周口径</h4><ul><li>{esc(prev)}</li>
<li>材料：每日短讯 {len(w["week_wires"]) + 1} 期 + 知识萃取周刊 + Obsidian 周信源</li></ul></div>
<details open><summary>本周各日短讯一句话</summary><ul>{"".join(f'<li>{esc(x["date"][5:])}：{esc(x["oneline_take"])}</li>' for x in w["week_wires"])}</ul></details>
<p class="foot">回到 <a href="今日-{m["material_date"]}.html">今日</a> · 批次 {MAG_BUILD}</p>
<details><summary>往期</summary><ul class="arch">{arch_rows}</ul></details>
</div></body></html>"""


def render_index(m: dict, archive: list) -> str:
    REL = globals()["REL"]
    arch_rows = "".join(f'<li><a href="{esc(a["file"])}">{esc(a["label"])}</a></li>' for a in archive)
    topics = " · ".join(f'<a href="本周-{m["week"]}.html#{esc(t["slug"])}">{esc(t["title"])}</a>' for t in m["week_view"]["topics"])
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>知识杂志 · 目录</title><style>{CSS}</style></head><body><div class="page">
<header class="mast"><p class="kicker">KNOWLEDGE MAGAZINE</p><h1>知识杂志</h1>
<p class="meta">今日变化 · 本周脉络 · 证据可回溯 · 批次 {MAG_BUILD}</p></header>
<nav class="tabs"><a href="今日-{m["material_date"]}.html">今日 {m["material_date"]}</a><a href="本周-{m["week"]}.html">本周 {m["week"]}</a><a class="cur" href="#">往期</a></nav>
<p class="thread">本期主题：{topics}</p>
<ul class="arch">{arch_rows}</ul>
<p class="foot">数据 magazine-data.json 与页面同批生成 · 桌面总入口 {REL}index.html</p>
</div></body></html>"""


def render_hub_index(m: dict) -> str:
    t, w = m["today"], m["week"]
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>知识杂志 · 总入口</title><style>{CSS}</style></head><body><div class="page">
<header class="mast"><p class="kicker">KNOWLEDGE MAGAZINE · 总入口</p><h1>知识杂志</h1>
<p class="meta">今日变化 → 本周脉络 → 证据核对 · 批次 {MAG_BUILD} · 生成 {MAG_BUILT_AT}</p></header>
<nav class="tabs">
<a class="cur" href="杂志/今日-{m["material_date"]}.html">今日合刊 {m["material_date"]}</a>
<a href="杂志/本周-{m["week"]}.html">本周脉络 {m["week"]}</a>
<a href="杂志/index.html">杂志往期</a></nav>
<p class="thread">{esc(t["verdict"])}</p>
<h2 class="sec">三条原始栏目<span class="sub">杂志的上游</span></h2>
<ul class="arch">
<li><a href="每日短讯/index.html">每日短讯</a>（最新 <a href="每日短讯/每日短讯-{t["date"]}.html">{t["date"]}</a>）</li>
<li><a href="知识萃取/index.html">知识萃取周刊</a>（<a href="知识萃取/知识萃取周刊 {m["week"]}.html">{m["week"]}</a>）</li>
<li><a href="深读/index.html">深读特刊</a> · <a href="研究简报/index.html">研究简报</a></li>
</ul>
<p class="foot">合刊由 事实(短讯) + 萃取(周刊) + 信源(Obsidian) 三路材料融合 · 每日出刊 · Cindy 内同名插件可阅读与发起更新</p>
</div></body></html>"""


def render_backlink(m: dict) -> str:
    t, w = m["today"], m["week_view"]
    topics = "\n".join(f"- [[{t2['title']}|{t2['title']}]]：{t2['thread']}" for t2 in w["topics"])
    return f"""---
title: "知识杂志回链 {w['week']}"
type: "magazine-backlink"
generated_at: "{MAG_BUILT_AT}"
build_id: "{MAG_BUILD}"
---

# 知识杂志回链 {w['week']}

本周知识杂志（今日合刊 + 本周脉络）由管道自动重建，本文件是杂志→信源的回链索引，幂等覆盖，不改动任何笔记原文。

- 本周脉络：`~/Desktop/知识杂志/杂志/batches/<build_id>/本周-{w['week']}.html`
- 最新今日合刊：`~/Desktop/知识杂志/杂志/batches/<build_id>/今日-{t['date']}.html`（材料日 {t['date']}）
- 桌面总入口：`~/Desktop/知识杂志/index.html`（指针路由入口）
- 批次：{MAG_BUILD}

## 本周主题

{topics}

## 本周新增萃取笔记（杂志已收录）

""" + "\n".join(f"- [[{a['title']}]]（{a['added_on']}）" for a in t["new_notes"])


# ---------------------------------------------------------------- 原子写入与幂等

def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def write_precheck_fp(co: dict) -> None:
    """出刊成功（含幂等跳过）后写预检指纹：wire md5 + weekly md5 + 周目录笔记清单。
    与 collect.plan() 共用同一指纹 contract（collect.week_dir_fingerprint）。"""
    from collect import week_dir_fingerprint
    fp = {"wire_md5": co["inputs"]["wire"]["md5"],
          "weekly_md5": co["inputs"]["weekly"]["md5"],
          "week_dir": week_dir_fingerprint(co["obsidian_week"]["dir"])}
    atomic_write(STATE / "precheck-last.json", json.dumps(fp, ensure_ascii=False))


def scan_archive(today_file: str, week_file: str) -> list:
    out = []
    if BATCHES.is_dir():  # 新结构：批次目录内的期刊（自新到旧）
        for d in sorted(BATCHES.iterdir(), reverse=True):
            meta_p = d / "batch.json"
            if not meta_p.is_file():
                continue
            try:
                meta = json.loads(meta_p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            rel = "" if d.name == MAG_BUILD else f"../{d.name}/"
            out.append({"label": f"今日 · {meta.get('material_date')}（{d.name[:13]}）", "file": f"{rel}{today_file_name(meta)}"})
    days = sorted((WIRE_NAME.search(p.name).group(1), p.name) for p in MAG.glob("今日-*.html"))
    weeks = sorted((WEEK_NAME.search(p.name).group(1), p.name) for p in MAG.glob("本周-*.html"))
    out += [{"label": f"本周 · {w}（旧版式）", "file": f"../../{n}"} for w, n in reversed(weeks)]
    out += [{"label": f"今日 · {d}（旧版式）", "file": f"../../{n}"} for d, n in reversed(days)]
    return out


def today_file_name(meta: dict) -> str:
    return f"今日-{meta.get('material_date')}.html"


# ---------------------------------------------------------------- 批次一致性：不可变批次 + 唯一指针
# 结构：杂志/batches/<build_id>/（一次成型的完整成品：今日/本周/index/data/batch.json）
#      杂志/batch-pointer.js（唯一指针，单文件原子切换 = 两个读取器共同的提交边界）
# 桌面 HTML 与 Cindy worker 都按指针选批；指针未切 = 读者永远读到上一完整成品。
# 旧固定路径文件（杂志/今日-*.html 等）冻结为只读兼容历史，不再参与发布。

POINTER = MAG / "batch-pointer.js"
BATCHES = MAG / "batches"
POINTER_PREFIX = "window.MAGAZINE_CURRENT_BATCH = "
ROUTER_TOKEN = "km-router-v2"


def md5_str(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def batch_manifest(build_id: str, built_at: str, merged: dict, files: dict) -> dict:
    return {"schema": "knowledge-magazine-batch/2", "build_id": build_id, "built_at": built_at,
            "material_date": merged["material_date"], "week": merged["week"], "files": files}


def read_pointer() -> dict | None:
    try:
        raw = POINTER.read_text(encoding="utf-8").strip()
        body = raw[len(POINTER_PREFIX):].rstrip(";").strip()
        return json.loads(body)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def write_pointer(meta: dict) -> None:
    """唯一提交点：单文件原子替换。读者要么见旧指针要么见新指针，永不见半批。"""
    content = POINTER_PREFIX + json.dumps(meta, ensure_ascii=False, indent=1) + ";\n"
    tmp = POINTER.with_name(POINTER.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, POINTER)


def batch_ok(meta: dict) -> bool:
    """按 batch.json 清单核对该批次目录完整且 md5 一致。"""
    d = MAG / meta.get("dir", "")
    if not d.is_dir():
        return False
    for name, want in (meta.get("files") or {}).items():
        f = d / name
        if not f.is_file() or md5_str(f.read_text(encoding="utf-8")) != want:
            return False
    return bool((d / "batch.json").is_file())


def verify_or_heal() -> str:
    """指针完整性检查 + 自愈：当前批损坏 → 重指到最新完整批次（批次目录本身不可变、写一半不影响读者）。"""
    cur = read_pointer()
    if cur and batch_ok(cur):
        return "consistent"
    best = None
    if BATCHES.is_dir():
        for d in sorted(BATCHES.iterdir()):
            try:
                meta = json.loads((d / "batch.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            meta["dir"] = f"batches/{d.name}"
            if batch_ok(meta) and (best is None or (meta["built_at"], meta["build_id"]) > (best["built_at"], best["build_id"])):
                best = meta
    if best is None and cur is None:
        return "no-pointer"
    if best and (not cur or best["build_id"] != cur["build_id"]):
        write_pointer(best)
        write_publication_status({"consistency": {"state": "healed", "repointed_to": best["build_id"],
                                                  "broken": (cur or {}).get("build_id"),
                                                  "at": dt.datetime.now().isoformat(timespec="seconds")}})
        return "healed"
    write_publication_status({"consistency": {"state": "degraded", "broken": (cur or {}).get("build_id"),
                                              "at": dt.datetime.now().isoformat(timespec="seconds")}})
    return "degraded"


def stage_batch(payload: dict, meta: dict) -> Path:
    """完整批次写入 batches/<build_id>/（目录名即 build_id，写一半无人可读），返回批次目录。"""
    d = BATCHES / meta["build_id"]
    tmpd = BATCHES / (meta["build_id"] + ".staging")
    if tmpd.exists():
        shutil.rmtree(tmpd)
    tmpd.mkdir(parents=True)
    for name, content in payload.items():
        f = tmpd / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        if name.endswith(".html") and meta["build_id"] not in content:
            raise RuntimeError(f"staged 页面缺 build_id：{name}")
        if name == "magazine-data.json":
            json.loads(content)
    (tmpd / "batch.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    if d.exists():
        shutil.rmtree(d)  # 同 build 重发（--fresh）：整目录替换仍原子于 rename
    os.replace(tmpd, d)
    return d


ROUTER_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>知识杂志 · 当前批次</title>
<style>
:root{--paper:#FBFAF8;--ink:#1C1A17;--muted:#8A8478;--rule:#E3DED4;color-scheme:light}
*{box-sizing:border-box}html,body{margin:0;background-color:#FBFAF8;background-image:linear-gradient(#FBFAF8,#FBFAF8)}
body{color:var(--ink);font-family:"Songti SC","Noto Serif SC",serif;line-height:1.75;padding:24px}
.page{max-width:760px;margin:auto}.mast{border-top:6px solid var(--ink);padding:14px 0 10px;margin-bottom:8px}
.k{font-family:"PingFang SC",sans-serif;font-size:12px;letter-spacing:.16em;color:#a63228}
h1{font-size:30px;margin:6px 0 0}ul{list-style:none;padding:0}li{border-bottom:1px dotted var(--rule);padding:9px 0}
a{color:inherit;text-decoration:none}a:hover{color:#a63228}
.meta,.s{font-family:"PingFang SC",sans-serif;font-size:12.5px;color:var(--muted)}
</style></head><body><div class="page" id="app">正在读取当前批次指针…</div>
<script src="BATCH_POINTER_SRC"></script>
<script>
(function(){
  var b = window.MAGAZINE_CURRENT_BATCH;
  var app = document.getElementById("app");
  if(!b){ app.textContent = "指针缺失：尚未出刊或批次不完整（保留上一版本）。"; return; }
  var hist = b.history || [];
  var h = '<header class="mast"><p class="k">KNOWLEDGE MAGAZINE · 当前批次</p><h1>知识杂志</h1>'
    + '<p class="meta">材料日 ' + (b.material_date||"—") + ' · 批次 ' + b.build_id + ' · 生成 ' + (b.built_at||"—") + '</p></header>'
    + '<ul><li><a href="' + b.dir + '/今日-' + b.material_date + '.html">今日合刊 ' + b.material_date + '</a></li>'
    + '<li><a href="' + b.dir + '/本周-' + b.week + '.html">本周脉络 ' + b.week + '</a></li></ul>'
    + '<p class="meta">两个阅读出口（本页与 Cindy 插件）都按同一指针选批；批次目录不可变，出刊失败保留上一完整批次。</p>';
  if(hist.length > 1){
    h += '<p class="s">往期批次</p><ul>';
    hist.slice(1).forEach(function(x){
      h += '<li><a href="batches/' + x.build_id + '/今日-' + x.material_date + '.html">' + x.material_date + ' · ' + x.build_id + '</a></li>';
    });
    h += '</ul>';
  }
  h += '<p class="s">上游栏目：<a href="../每日短讯/index.html">每日短讯</a> · <a href="../知识萃取/index.html">知识萃取周刊</a> · <a href="../深读/index.html">深读</a> · <a href="../研究简报/index.html">研究简报</a></p>';
  app.innerHTML = h;
})();
</script></body></html>
"""


def ensure_routers() -> None:
    """稳定入口页（不随批次重写）：桌面总入口 + 杂志目录入口，都读同一指针文件。"""
    for page, ptr_src, lanes_rel in ((HUB / "index.html", "杂志/batch-pointer.js", "../"),
                                     (MAG / "index.html", "batch-pointer.js", "../")):
        need = page.exists() and ROUTER_TOKEN not in page.read_text(encoding="utf-8")
        if not page.exists() or need:
            html = ROUTER_HTML.replace("BATCH_POINTER_SRC", ptr_src).replace("../每日短讯", lanes_rel + "每日短讯")
            html = html.replace("../知识萃取", lanes_rel + "知识萃取").replace("../深读", lanes_rel + "深读")
            html = html.replace("../研究简报", lanes_rel + "研究简报")
            html = html.replace("<title>", "<!--" + ROUTER_TOKEN + "--><title>")
            atomic_write(page, html)


def gate_result(merged: dict) -> dict:
    from readability_gate import check_data
    return check_data(merged)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--collect", required=True)
    ap.add_argument("--dry-run", action="store_true", help="完整校验与渲染，但绝不发布（不写任何生产文件/状态成功）")
    ap.add_argument("--fresh", action="store_true", help="跳过幂等早退（闭刊日用：同内容也刷新 built_at 重写批次）")
    args = ap.parse_args()

    heal_state = verify_or_heal()
    ensure_routers()

    ed = json.loads(Path(args.data).read_text(encoding="utf-8"))
    co = json.loads(Path(args.collect).read_text(encoding="utf-8"))

    errs = validate(ed, co)
    if errs:
        if not args.dry_run:
            write_publication_status({"last_attempt": {"at": dt.datetime.now().isoformat(timespec="seconds"),
                                                       "ok": False, "stage": "validate", "error": errs[0]}})
        fail("校验 FAIL：" + "；".join(errs[:8]))

    merged = merge(ed, co)
    det = gate_result(merged)
    if det["hard"]:
        if not args.dry_run:
            write_publication_status({"last_attempt": {"at": dt.datetime.now().isoformat(timespec="seconds"),
                                                       "ok": False, "stage": "readability-gate",
                                                       "error": f"{det['hard'][0]['field']} {det['hard'][0]['rule']}"}})
        print(json.dumps({"ok": False, "stage": "readability-gate", "hard": det["hard"][:5]}, ensure_ascii=False))
        sys.exit(1)

    canonical = json.dumps(merged, ensure_ascii=False, sort_keys=True)
    global MAG_BUILD, MAG_BUILT_AT
    MAG_BUILT_AT = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    MAG_BUILD = f"{merged['material_date']}-{hashlib.md5(canonical.encode()).hexdigest()[:8]}"
    merged["build"] = {"build_id": MAG_BUILD, "built_at": MAG_BUILT_AT, "model": ed.get("model") or "glm-5.3",
                       "collect_md5": co["inputs"]["wire"]["md5"], "weekly_md5": co["inputs"]["weekly"]["md5"]}

    today_file = f"今日-{merged['material_date']}.html"
    week_file = f"本周-{merged['week']}.html"

    # 语义可读性发布合同：结构化审稿记录存在、哈希绑定候选、结论 pass；缺失/过期/FAIL 一律保留旧批
    want_sha = candidate_sha256_from(merged)
    rec_path = STATE / "readability-latest.json"
    if not args.dry_run:
        if not rec_path.is_file():
            write_publication_status({"last_attempt": {"at": dt.datetime.now().isoformat(timespec="seconds"),
                                                       "ok": False, "stage": "readability-review",
                                                       "error": "缺语义审核记录"}})
            fail("可读性 FAIL：缺结构化审稿记录（readability_review.py --record --report …）")
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
        if rec.get("candidate_sha256") != want_sha:
            write_publication_status({"last_attempt": {"at": dt.datetime.now().isoformat(timespec="seconds"),
                                                       "ok": False, "stage": "readability-review",
                                                       "error": "审稿记录与候选哈希不一致（改稿后须重审）"}})
            fail("可读性 FAIL：审稿记录与候选哈希不一致（内容已改需重审）")
        if not rec.get("pass"):
            write_publication_status({"last_attempt": {"at": dt.datetime.now().isoformat(timespec="seconds"),
                                                       "ok": False, "stage": "readability-review",
                                                       "error": "语义审核未通过"}})
            fail("可读性 FAIL：语义审核未通过，保留上一完整批次")

    # 幂等：指针已指同批且批次完整 → 跳过（不重写任何文件）
    cur = read_pointer()
    if not args.dry_run and not args.fresh and cur and cur.get("build_id") == MAG_BUILD and batch_ok(cur):
        write_precheck_fp(co)
        write_publication_status({"last_success": {"build_id": MAG_BUILD, "built_at": cur.get("built_at"),
                                                   "material_date": merged["material_date"], "week": merged["week"]},
                                  "last_attempt": {"at": dt.datetime.now().isoformat(timespec="seconds"),
                                                   "ok": True, "stage": "idempotent"}})
        print(json.dumps({"ok": True, "idempotent": True, "build_id": MAG_BUILD, "heal": heal_state}, ensure_ascii=False))
        return

    archive = scan_archive(today_file, week_file)
    globals()["REL"] = "../"  # 批次页位于 杂志/batches/<build>/，比旧固定文件深一层
    payload = {
        today_file: render_today(merged, archive),
        week_file: render_week(merged, archive),
        "index.html": render_index(merged, archive),
        "magazine-data.json": json.dumps(merged, ensure_ascii=False, indent=1),
    }
    meta = batch_manifest(MAG_BUILD, MAG_BUILT_AT, merged,
                          {name: md5_str(c) for name, c in payload.items()})
    meta["dir"] = f"batches/{MAG_BUILD}"

    if args.dry_run:
        from readability_gate import check_html
        html_hard, tmps = [], []
        for name, content in (("dryrun-today.html", payload[today_file]), ("dryrun-week.html", payload[week_file])):
            tp = STATE / name
            tp.write_text(content, encoding="utf-8")
            tmps.append(tp)
            html_hard.extend(check_html(str(tp), MAG_BUILD))
        cand = STATE / "dryrun-candidate.json"
        cand.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
        for tp in tmps:
            tp.unlink(missing_ok=True)
        print(json.dumps({"ok": True and not html_hard, "dry_run": True, "build_id": MAG_BUILD,
                          "would_publish": [f"杂志/batches/{MAG_BUILD}/{n}" for n in payload],
                          "deterministic_gate": {"ok": not det["hard"] and not html_hard,
                                                 "warnings": det["warnings"][:5], "html_hard": html_hard[:3]},
                          "readability_required_sha": want_sha,
                          "candidate_for_review": str(STATE / "dryrun-candidate.json"),
                          "heal": heal_state}, ensure_ascii=False))
        sys.exit(0 if not html_hard else 1)

    # 发布：批次目录完整成型并校验 → 单指针原子切换（唯一提交边界）→ 回链（桌面，非读取边界）
    stage_batch(payload, meta)
    if not batch_ok(meta):
        write_publication_status({"last_attempt": {"at": dt.datetime.now().isoformat(timespec="seconds"),
                                                   "ok": False, "stage": "stage-batch", "error": "批次目录校验失败"}})
        fail("FAIL：staged 批次校验不完整，指针未切，读者仍见上一批次")
    history = [{"build_id": MAG_BUILD, "material_date": merged["material_date"], "built_at": MAG_BUILT_AT}]
    if cur and cur.get("build_id") != MAG_BUILD:
        history += [h for h in (cur.get("history") or []) if h.get("build_id") != MAG_BUILD][:29]
    meta["history"] = history
    write_pointer(meta)

    backlink_path = MAG / "回链" / f"知识杂志回链 {merged['week']}.md"
    atomic_write(backlink_path, render_backlink(merged))

    write_precheck_fp(co)
    write_publication_status({"last_success": {"build_id": MAG_BUILD, "built_at": MAG_BUILT_AT,
                                               "material_date": merged["material_date"], "week": merged["week"]},
                              "last_attempt": {"at": dt.datetime.now().isoformat(timespec="seconds"),
                                               "ok": True, "stage": "publish"}, "consistency": {"state": "ok"}})
    print(json.dumps({"ok": True, "build_id": MAG_BUILD, "built_at": MAG_BUILT_AT,
                      "batch_dir": str(BATCHES / MAG_BUILD), "pointer": str(POINTER),
                      "today": merged["material_date"], "week": merged["week"],
                      "topics": len(merged["week_view"]["topics"]), "picks": len(merged["today"]["picks"]),
                      "cross_checks": len(merged["today"]["cross_checks"]), "heal": heal_state}, ensure_ascii=False))


def candidate_sha256_from(merged: dict) -> str:
    d = {k: v for k, v in merged.items() if k != "build"}
    return __import__("hashlib").sha256(
        json.dumps(d, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


if __name__ == "__main__":
    main()
