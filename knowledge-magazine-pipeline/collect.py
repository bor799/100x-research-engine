#!/usr/bin/env python3
"""知识杂志 · 采集器（确定性，无 LLM）

三类真实材料 → state/collect-<date>.json：
  1. 每日短讯   ~/Desktop/知识杂志/每日短讯/每日短讯-YYYY-MM-DD.html（最新一期 + 本周各期）
  2. 知识萃取周刊  vault 信息源/<week>/知识萃取周刊 <week>.html 与 hub 知识萃取/ 副本，取更新者
  3. Obsidian 日/周信源  信息源/<week>/*.md（frontmatter）+ manifest.jsonl；
     另核 知识操作系统/每日巡视、每周复盘 的新旧状态（缺则如实标注）

只读，不写任何生产文件。退出码 0 成功 / 1 失败（结构变化导致解析为空）。
"""
from __future__ import annotations

import os as _os
from pathlib import Path as _P
def _km_home(rel: str) -> Path:
    """仓库版：路径经环境变量注入（KM_HUB / KM_VAULT_ROOT），缺省落到当前用户主目录同名结构；
    本机生产目录继续以默认值工作，测试与 CI 用环境变量或 mock 覆盖。"""
    env, *sub = rel.split('|')
    return _P(_os.environ.get(env) or _P.home().joinpath(*sub))

import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

HUB = _km_home("KM_HUB|Desktop|知识杂志")
VAULT = _km_home("KM_VAULT|Documents|Obsidian Vault|信息源")
WIRE_DIR = HUB / "每日短讯"
WEEKLY_HUB = HUB / "知识萃取"
STATE = HUB / "模块-知识杂志" / "state"

WIRE_NAME = re.compile(r"每日短讯-(\d{4}-\d{2}-\d{2})\.html$")
WEEK_NAME = re.compile(r"知识萃取周刊 (\d{4}-\d{2}-W\d)\.html$")


VAULT_ROOT = _km_home("KM_VAULT_ROOT|Documents|Obsidian Vault")


def obsidian_uri(rel_path: str) -> str:
    """生成以 vault 根为基准的 obsidian URI（信息源/ 前缀不可省）。"""
    return "obsidian://open?vault=Obsidian+Vault&file=" + urllib.parse.quote(rel_path)


def normalize_obsidian_url(url: str) -> str:
    """上游 ISSUE 的 file= 用周目录相对路径且缺 .md 扩展名；统一归一到 信息源/<周>/<文件>.md。
    只在归一化后能落到真实文件时才改写，否则保留原样（由存在性检查如实暴露）。"""
    if not url or not url.startswith("obsidian://"):
        return url
    m = re.search(r"[?&]file=([^&]*)", url)
    if not m:
        return url
    raw = urllib.parse.unquote_plus(m.group(1))
    for cand in (raw, raw + ".md"):
        p = VAULT_ROOT / "信息源" / cand
        if re.match(r"^\d{4}-\d{2}-W\d/", cand) and p.is_file():
            return obsidian_uri(f"信息源/{cand}")
    return url


def obsidian_url_to_file(url: str) -> Path | None:
    """obsidian URI → vault 内绝对路径（存在才返回；自动尝试补 .md）。"""
    if not url or not url.startswith("obsidian://"):
        return None
    m = re.search(r"[?&]file=([^&]*)", url)
    if not m:
        return None
    raw = urllib.parse.unquote_plus(m.group(1))
    for cand in (raw, raw + ".md"):
        p = VAULT_ROOT / cand
        if p.is_file():
            return p
    return None


def md5_file(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def mtime_iso(p: Path) -> str:
    return dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- 每日短讯解析

TAG_RE = re.compile(r"<[^>]+>")


def _text(s: str) -> str:
    return TAG_RE.sub("", s or "").strip()


def parse_wire(path: Path) -> dict:
    html = path.read_text(encoding="utf-8")
    date = WIRE_NAME.search(path.name).group(1)

    take = ""
    m = re.search(r'class="oneline-take">(.*?)</div>', html, re.S)
    if m:
        take = _text(m.group(1))
    issue_no = None
    m = re.search(r"第 <b>(\d+)</b> 期", html)
    if m:
        issue_no = int(m.group(1))

    sections, market_rows = [], []
    sec_iter = re.finditer(
        r'<section class="sec[^"]*">\s*<div class="sec-head">'
        r'<span class="no">(\d+)</span><h2>(.*?)</h2>(?:<span class="cnt">(.*?)</span>)?',
        html,
    )
    sec_spans = [(m.start(), m.end(), int(m.group(1)), _text(m.group(2)), _text(m.group(3) or "")) for m in sec_iter]
    for idx, (s, e, no, name, cnt) in enumerate(sec_spans):
        end = sec_spans[idx + 1][0] if idx + 1 < len(sec_spans) else len(html)
        body = html[e:end]

        items = []
        for card in re.finditer(r'<div class="top-card">(.*?)</div>\s*</div>|<div class="top-card">(.*?)</section>', body, re.S):
            pass  # top-card 由下方统一结构覆盖

        # 头条卡
        for m in re.finditer(r'<div class="top-card">(.*?)(?=<div class="top-card">|</div>\s*</section>)', body, re.S):
            c = m.group(1)
            a = re.search(r'<h3><a href="([^"]+)"[^>]*>(.*?)</a>(?:<span class="en">(.*?)</span>)?</h3>', c, re.S)
            if not a:
                continue
            items.append({
                "href": a.group(1),
                "title": _text(a.group(2)),
                "original": _text(a.group(3) or ""),
                "why": _text(re.search(r'class="why">(.*?)</p>', c, re.S).group(1)) if 'class="why"' in c else "",
                "meta": [_text(x) for x in re.findall(r"<span>(.*?)</span>", re.search(r'class="meta">(.*?)</div>', c, re.S).group(1))] if 'class="meta"' in c else [],
                "tag": _text(re.search(r'<span class="tag">(.*?)</span>', c).group(1)) if '<span class="tag"' in c else "",
                "style": "headline",
            })

        # 普通条目：以条目块为单位（到下一个条目/小节结束）
        chunks = re.split(r'(?=<div class="item">)', body)
        for c in chunks[1:]:
            c = c.split("</section>")[0]
            a = re.search(r'<div class="t"><a href="([^"]+)"[^>]*>(.*?)</a>(?:<span class="en">(.*?)</span>)?</div>', c, re.S)
            if not a:
                continue
            pts = re.search(r'<span class="pts">([^<]*)</span>', c)
            plain = [_text(x) for x in re.findall(r"<span>(.*?)</span>", c)]
            items.append({
                "href": a.group(1),
                "title": _text(a.group(2)),
                "original": _text(a.group(3) or ""),
                "why": plain[0] if plain else "",
                "meta": [_text(pts.group(1))] if pts else [],
                "tag": "",
                "style": "item",
            })

        # 预测市场行
        for m in re.finditer(r"<tr(?: data-progress=\"([^\"]*)\")?>(.*?)</tr>", body, re.S):
            row = m.group(2)
            a = re.search(r'<a href="([^"]+)"[^>]*title="([^"]*)"[^>]*>(.*?)</a>', row, re.S)
            if not a:
                continue
            cells = [_text(x) for x in re.findall(r"<td class=\"p\">(.*?)</td>|<td class=\"(?:down|up|flat)\">(.*?)</td>|<td class=\"vol\">(.*?)</td>", row, re.S) for x in ()]  # placeholder
            prob = _text(re.search(r'<td class="p">(\d+%)', row).group(1)) if '<td class="p">' in row else ""
            trend = _text(re.search(r'<td class="(?:down|up|flat)">([^<]*)', row).group(1)) if re.search(r'<td class="(?:down|up|flat)">', row) else ""
            vol = _text(re.search(r'<td class="vol">([^<]*)', row).group(1)) if '<td class="vol">' in row else ""
            market_rows.append({
                "href": a.group(1),
                "question": a.group(2),
                "title": _text(a.group(3)),
                "prob": prob, "trend": trend, "vol": vol,
                "progress": m.group(1) or "",
                "section": name,
            })
        sections.append({"no": no, "name": name, "cnt": cnt, "items": items})

    n_items = sum(len(s["items"]) for s in sections)
    if not sections or n_items == 0:
        raise SystemExit(f"FAIL: 每日短讯结构解析为空: {path}")
    return {
        "date": date, "issue_no": issue_no, "oneline_take": take,
        "sections": sections, "market_rows": market_rows,
        "item_count": n_items + len(market_rows),
    }


# ---------------------------------------------------------------- 周刊解析

def parse_weekly(path: Path) -> dict:
    html = path.read_text(encoding="utf-8")
    s = re.search(r"<script>(.*)</script>", html, re.S)
    if not s:
        raise SystemExit(f"FAIL: 周刊无 script 数据: {path}")
    i = s.group(1).find("let ISSUE=")
    obj, _ = json.JSONDecoder().raw_decode(s.group(1), s.group(1).find("{", i))
    arts, missing_files = [], []
    for a in obj.get("articles", []):
        obs = normalize_obsidian_url(a.get("obsidian_url"))
        if obs and obsidian_url_to_file(obs) is None:
            missing_files.append(a.get("path") or obs)
        arts.append({
            "article_id": a["article_id"], "title": a["title"], "path": a.get("path"),
            "added_on": a.get("added_on"), "url": a.get("url"), "final_score": a.get("final_score"),
            "signal_tier": a.get("signal_tier"), "brief": (a.get("brief") or "")[:700],
            "carryover": bool(a.get("carryover")), "complete": bool(a.get("complete")),
            "obsidian_url": obs,
        })
    if not arts:
        raise SystemExit(f"FAIL: 周刊 articles 为空: {path}")
    return {"week": obj.get("week"), "generated_at": obj.get("generated_at"),
            "counts": obj.get("counts"), "articles": arts,
            "obsidian_missing_files": missing_files[:10]}


# ---------------------------------------------------------------- Obsidian 周目录

def scan_week_dir(week: str) -> dict:
    d = VAULT / week
    notes, manifest = [], []
    if d.is_dir():
        for p in sorted(d.glob("*.md")):
            head = p.read_text(encoding="utf-8", errors="replace")[:2000]
            fm = {}
            m = re.match(r"^---\n(.*?)\n---", head, re.S)
            if m:
                for line in m.group(1).splitlines():
                    kv = re.match(r'^(\w+):\s*"?(.*?)"?\s*$', line)
                    if kv:
                        fm[kv.group(1)] = kv.group(2)
            if fm.get("type") == "knowledge-extract" or "article_id" in fm:
                notes.append({"filename": p.name, "title": fm.get("title") or p.stem,
                              "url": fm.get("url"), "article_id": fm.get("article_id"),
                              "score": fm.get("final_score") or fm.get("score"),
                              "added_on": p.name[:10]})
        mf = d / "manifest.jsonl"
        if mf.is_file():
            for line in mf.read_text(encoding="utf-8").splitlines():
                try:
                    manifest.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return {"dir": str(d), "exists": d.is_dir(), "notes": notes,
            "note_count": len(notes), "manifest_count": len(manifest)}


def staleness() -> list:
    out = []
    for label, sub in (("每日巡视", "知识操作系统/每日巡视"), ("每周复盘", "知识操作系统/每周复盘")):
        d = VAULT / sub
        files = sorted(d.glob("*.md")) if d.is_dir() else []
        if not files:
            out.append(f"{label}：目录无笔记（{sub}）")
        else:
            out.append(f"{label}：最新样本 {files[-1].name}（仅旧样本，按缺源处理）")
    return out


# ---------------------------------------------------------------- 主流程

def week_of(date_str: str) -> str:
    """vault 口径：W = ceil(day/7)（W1=1-7, W2=8-14 …），与 100X 管道一致。"""
    d = dt.date.fromisoformat(date_str)
    return f"{d.year}-{d.month:02d}-W{(d.day + 6) // 7}"


def week_dates(week: str) -> list:
    y, m, w = re.match(r"(\d{4})-(\d{2})-W(\d)", week).groups()
    start = (int(w) - 1) * 7 + 1
    days = []
    for off in range(7):
        try:
            days.append(dt.date(int(y), int(m), start + off).isoformat())
        except ValueError:
            break
    return days


LANE_DIRS = ("每日短讯", "知识萃取", "深读", "研究简报")


def scan_lanes() -> list:
    """信息源收口清单：hub 下四条原始栏目 lane 的期数与最新期（只读，确定性）。
    index.html 是各 lane 的路由页，不计入期数；目录缺失如实标注。"""
    lanes = []
    for name in LANE_DIRS:
        d = HUB / name
        if not d.is_dir():
            lanes.append({"lane": name, "missing": True})
            continue
        issues = []
        for p in d.glob("*.html"):
            if p.name == "index.html":
                continue
            issues.append({"name": p.name, "mtime": mtime_iso(p)})
        issues.sort(key=lambda x: x["mtime"], reverse=True)
        lanes.append({"lane": name, "files": len(issues),
                      "latest": issues[0] if issues else None})
    return lanes


def select_inputs() -> dict:
    """唯一材料选择器（collect 与 --plan 共用）。
    周窗口以周刊 artifact 的最终期号为准；week_wires 在窗口最终确定后收集。"""
    wires = sorted(WIRE_DIR.glob("每日短讯-*.html"), key=lambda p: WIRE_NAME.search(p.name).group(1))
    if not wires:
        raise SystemExit("FAIL: 没有每日短讯存档")
    latest_wire = wires[-1]
    wire = parse_wire(latest_wire)
    week = week_of(wire["date"])
    wdates = week_dates(week)

    # 周刊：vault 与 hub 两份，取新；缺失降级为可缺导读（不阻断今日合刊）
    cand = [p for p in [VAULT / week / f"知识萃取周刊 {week}.html",
                        WEEKLY_HUB / f"知识萃取周刊 {week}.html"] if p.is_file()]
    weekly_degraded, week_note = False, ""
    if cand:
        weekly_path = max(cand, key=lambda p: p.stat().st_mtime)
        weekly = parse_weekly(weekly_path)
        if weekly.get("week") and weekly["week"] != week:
            week_note = f"期号以周刊 artifact 为准：{weekly['week']}（按短讯日期推算为 {week}）"
            week = weekly["week"]
            wdates = week_dates(week)
    else:
        weekly_path, weekly = None, {"week": week, "generated_at": "", "articles": [],
                                     "obsidian_missing_files": []}
        weekly_degraded = True
        week_note = f"本周周刊尚未生成（{week}），本周视角以短讯+Obsidian 为准，覆盖区如实标注"

    # 本周各期短讯（跨日对照）：在最终周窗口确定后收集，避免先收后改期
    week_wires = []
    for pw in wires:
        d = WIRE_NAME.search(pw.name).group(1)
        if d in wdates and d != wire["date"]:
            try:
                w = parse_wire(pw)
                week_wires.append({"date": d, "issue_no": w["issue_no"],
                                   "oneline_take": w["oneline_take"],
                                   "sections": [{"name": sec["name"], "items": [
                                       {"href": i["href"], "title": i["title"]} for i in sec["items"]]} for sec in w["sections"]],
                                   "market_rows": [{"href": r["href"], "title": r["title"], "prob": r["prob"]}
                                                   for r in w["market_rows"]]})
            except SystemExit:
                continue
    week_wires.sort(key=lambda x: x["date"])

    # 本周证据白名单：本周各期短讯（含今日）条目与预测市场 URL → 出处日期/期号
    week_urls = {}
    for w in week_wires:
        for sec in w["sections"]:
            for it in sec["items"]:
                week_urls[it["href"]] = {"date": w["date"], "issue_no": w["issue_no"], "channel": "wire"}
        for r in w["market_rows"]:
            week_urls.setdefault(r["href"], {"date": w["date"], "issue_no": w["issue_no"], "channel": "market"})
    for sec in wire["sections"]:
        for it in sec["items"]:
            week_urls[it["href"]] = {"date": wire["date"], "issue_no": wire["issue_no"], "channel": "wire"}
    for r in wire["market_rows"]:
        week_urls.setdefault(r["href"], {"date": wire["date"], "issue_no": wire["issue_no"], "channel": "market"})

    week_dir = scan_week_dir(week)
    for n in week_dir["notes"]:
        n["obsidian_url"] = obsidian_uri(f"信息源/{week}/{n['filename']}")
    week_dir["note_urls"] = sorted({n["url"] for n in week_dir["notes"] if n.get("url")}
                                   | {n["obsidian_url"] for n in week_dir["notes"]})

    # 上周周刊（判断增量，缺则降级）
    all_weeks = sorted({WEEK_NAME.search(p.name).group(1) for p in WEEKLY_HUB.glob("知识萃取周刊 *.html")})
    prev, prev_label = None, None
    for lbl in reversed(all_weeks):
        if lbl < week:
            pth = VAULT / lbl / f"知识萃取周刊 {lbl}.html"
            if not pth.is_file():
                pth = WEEKLY_HUB / f"知识萃取周刊 {lbl}.html"
            if pth.is_file():
                prev_label = lbl
                break
    if prev_label:
        pth = VAULT / prev_label / f"知识萃取周刊 {prev_label}.html"
        if not pth.is_file():
            pth = WEEKLY_HUB / f"知识萃取周刊 {prev_label}.html"
        try:
            pw = parse_weekly(pth)
            prev = {"week": pw["week"], "titles": sorted(a["title"] for a in pw["articles"]),
                    "ids": sorted(a["article_id"] for a in pw["articles"])}
        except SystemExit:
            prev = None

    weekly_md5 = md5_file(weekly_path) if weekly_path else None
    inputs = {
        "wire": {"path": str(latest_wire), "md5": md5_file(latest_wire), "mtime": mtime_iso(latest_wire),
                 **{k: wire[k] for k in ("date", "issue_no", "item_count")}},
        "weekly": {"path": str(weekly_path) if weekly_path else None, "md5": weekly_md5,
                   "mtime": mtime_iso(weekly_path) if weekly_path else None,
                   "all_candidates": [{"path": str(p), "mtime": mtime_iso(p)} for p in cand],
                   "week": weekly.get("week") or week, "generated_at": weekly.get("generated_at", ""),
                   "article_count": len(weekly.get("articles") or []), "degraded": weekly_degraded},
    }
    return {"wire": wire, "week": week, "wdates": wdates, "week_wires": week_wires,
            "week_urls": week_urls, "weekly": weekly, "weekly_degraded": weekly_degraded,
            "weekly_path": weekly_path, "week_note": week_note, "prev_weekly": prev,
            "week_dir": week_dir, "inputs": inputs}


# ---------------------------------------------------------------- 出刊状态（唯一状态文件，插件与桌面共用）

STATUS_PATH = HUB / "杂志" / "data" / "publication-status.json"


def write_publication_status(update: dict) -> dict:
    """原子合并写 publication-status.json；一条链路里成功/跳过/失败都只写这一个文件。"""
    cur = {}
    if STATUS_PATH.is_file():
        try:
            cur = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cur = {}
    cur.update({"schema": "knowledge-magazine-status/1",
                "updated_at": dt.datetime.now().isoformat(timespec="seconds")})
    cur.update(update)
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_name(STATUS_PATH.name + ".tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATUS_PATH)
    return cur


def week_dir_fingerprint(dir_path: str) -> list:
    """周目录笔记清单指纹（precheck-last.json 同形，plan 与 render 共用）。"""
    d = Path(dir_path)
    if not d.is_dir():
        return []
    return sorted([[p.name, round(p.stat().st_mtime), p.stat().st_size] for p in d.glob("*.md")])


def plan(arg_today: str | None = None) -> dict:
    """出刊前置规划（只读）：与采集共用 select_inputs，输出决策与指纹。
    必要材料=当日短讯+周目录笔记（变化→出刊）；周刊为可缺导读（缺失降级，不阻断）。
    闭刊日=最终周窗口最后一个真实日（月份自然截断，不跨月滚动）。"""
    today = arg_today or dt.date.today().isoformat()
    sel = select_inputs()
    fp = {"wire_md5": sel["inputs"]["wire"]["md5"],
          "weekly_md5": sel["inputs"]["weekly"]["md5"],
          "week_dir": week_dir_fingerprint(sel["week_dir"]["dir"])}
    last = {}
    if (STATE / "precheck-last.json").is_file():
        try:
            last = json.loads((STATE / "precheck-last.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            last = {}
    required_changed = (not last) or fp["wire_md5"] != last.get("wire_md5") \
        or fp["week_dir"] != last.get("week_dir")
    weekly_changed = bool(last) and fp["weekly_md5"] != last.get("weekly_md5")
    window_last = sel["wdates"][-1]
    closure = window_last == today
    built_today = False
    data_path = HUB / "杂志" / "data" / "magazine-data.json"
    if data_path.is_file():
        try:
            built_today = ((json.loads(data_path.read_text(encoding="utf-8")).get("build") or {})
                           .get("built_at", "") or "").startswith(today)
        except json.JSONDecodeError:
            built_today = False
    if required_changed:
        decision, reason = "publish", "必要材料有更新（短讯/周目录笔记）"
    elif closure and not built_today:
        decision, reason = "closure_fresh", f"闭刊日（{sel['week']} 窗口末日 {window_last}）且今日未出刊"
    elif closure and built_today:
        decision, reason = "skip", f"闭刊日已出刊，材料无变化"
    else:
        decision, reason = "skip", "必要材料无变化且非闭刊日（周刊仅重组不触发出刊）"
    return {"ok": True, "decision": decision, "reason": reason, "today": today,
            "week": sel["week"], "week_window": {"first": sel["wdates"][0], "last": window_last},
            "wire_date": sel["wire"]["date"], "wire_md5": fp["wire_md5"],
            "weekly": {"degraded": sel["weekly_degraded"], "changed": weekly_changed,
                       "week": sel["inputs"]["weekly"]["week"]},
            "week_notes": len(fp["week_dir"]), "built_today": built_today}


def main() -> None:
    today = dt.date.today().isoformat()
    sel = select_inputs()
    wire, week, wdates = sel["wire"], sel["week"], sel["wdates"]
    week_dir, weekly, prev = sel["week_dir"], sel["weekly"], sel["prev_weekly"]

    known_urls = set()
    for s in wire["sections"]:
        for it in s["items"]:
            known_urls.add(it["href"])
    for r in wire["market_rows"]:
        known_urls.add(r["href"])
    for a in weekly.get("articles") or []:
        if a["url"]:
            known_urls.add(a["url"])
        if a["obsidian_url"]:
            known_urls.add(a["obsidian_url"])
    known_urls.update(week_dir.get("note_urls") or [])

    out = {
        "schema": "knowledge-magazine-collect/1",
        "collected_at": dt.datetime.now().isoformat(timespec="seconds"),
        "collect_date": today,
        "material_date": wire["date"],
        "week": week,
        "week_dates": wdates,
        "wire": wire,
        "week_wires": sel["week_wires"],
        "week_urls": sel["week_urls"],
        "weekly": weekly,
        "weekly_degraded": sel["weekly_degraded"],
        "prev_weekly": prev,
        "obsidian_week": week_dir,
        "stale_sources": staleness(),
        "week_note": sel["week_note"],
        "obsidian_missing_files": weekly.get("obsidian_missing_files", []),
        "inputs": sel["inputs"],
        "known_urls": sorted(known_urls),
        "lanes": scan_lanes(),
    }
    STATE.mkdir(parents=True, exist_ok=True)
    out_path = STATE / f"collect-{today}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(out_path), "material_date": wire["date"], "week": week,
                      "wire_items": wire["item_count"], "weekly_articles": len(weekly.get("articles") or []),
                      "week_wires": len(out["week_wires"]), "week_urls": len(out["week_urls"]),
                      "weekly_degraded": out["weekly_degraded"],
                      "obsidian_notes": week_dir["note_count"],
                      "prev_weekly": bool(prev)}, ensure_ascii=False))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true", help="只读规划：输出出刊决策与指纹，不写采集状态")
    ap.add_argument("--record-skip", metavar="PLAN_JSON", help="把 plan 的 skip 决策写入出刊状态文件")
    ap.add_argument("--today", default=None, help="测试用：覆盖当日日期（YYYY-MM-DD）")
    a = ap.parse_args()
    if a.record_skip:
        pl = json.loads(a.record_skip)
        write_publication_status({"last_skip": {"at": dt.datetime.now().isoformat(timespec="seconds"),
                                                "reason": pl.get("reason", ""), "decision": pl.get("decision"),
                                                "wire_date": pl.get("wire_date"), "week": pl.get("week")}})
        print(json.dumps({"ok": True, "recorded": "last_skip"}, ensure_ascii=False))
    elif a.plan:
        print(json.dumps(plan(a.today), ensure_ascii=False))
    else:
        main()
