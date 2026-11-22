#!/usr/bin/env python3
"""知识杂志 · 确定性可读性门禁（独立脚本，可手动重跑）。

只做机器可判定的检查；语义可读性由 readability_review.py（GLM 5.3 独立审核步骤）
负责，两者共同构成发布合同：确定性 PASS + 语义 PASS 记录与候选哈希绑定，
render.py 才允许切换当前批次。

硬错误（FAIL，非零退出）：
  leak_bold / leak_header / leak_bullet / leak_link ：flat 文本泄漏 Markdown 标记
  dangling_marker ：截断产生的悬空标记（* [ 等）
  raw_html / unsafe_href ：raw HTML 残留或非白名单协议链接
  dup_title / verdict_bad / missing_why ：结构缺失或重复
启发式警报（WARNING，不阻断）：超长无断句段落、重复前缀堆叠。
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
import json
import re
import sys

HARD_PATTERNS = [
    ("leak_bold", re.compile(r"\*\*")),
    ("leak_header", re.compile(r"(?m)^#{1,6}\s")),
    ("leak_bullet", re.compile(r"(?m)^\s*[-*·]\s+")),
    ("leak_link", re.compile(r"\]\(")),
    ("raw_html", re.compile(r"<[a-zA-Z!/][^>\n]*>")),
    ("unsafe_href", re.compile(r"(?i)javascript:|data:text/html")),
]
DANGLING = re.compile(r"(?:^|[^*])\*[^*\s]|[\[【]$")


def iter_fields(data: dict):
    t = data.get("today") or {}
    yield "today.verdict", t.get("verdict")
    for i, p in enumerate(t.get("picks") or []):
        yield f"today.picks[{i}].title", p.get("title")
        yield f"today.picks[{i}].why", p.get("why")
    for gi, g in enumerate(t.get("groups") or []):
        for ii, it in enumerate(g.get("items") or []):
            yield f"today.groups[{gi}].items[{ii}].note", it.get("note")
    for i, a in enumerate(t.get("new_notes") or []):
        yield f"today.new_notes[{i}].brief", a.get("brief")
    for i, c in enumerate(t.get("cross_checks") or []):
        for k in ("claim", "verdict", "counterpoint", "primary_source"):
            yield f"today.cross_checks[{i}].{k}", c.get(k)
    w = data.get("week_view") or {}
    yield "week_view.what_grew", w.get("what_grew")
    for i, tp in enumerate(w.get("topics") or []):
        yield f"week_view.topics[{i}].thread", tp.get("thread")
        for j, d in enumerate(tp.get("developments") or []):
            yield f"week_view.topics[{i}].developments[{j}].event", d.get("event")
        for j, x in enumerate(tp.get("counterpoints") or []):
            yield f"week_view.topics[{i}].counterpoints[{j}]", x


def check_data(data: dict) -> dict:
    hard, warnings = [], []
    for path, val in iter_fields(data):
        text = (val or "").strip()
        if not text:
            continue
        hit = False
        for rule, pat in HARD_PATTERNS:
            m = pat.search(text)
            if m:
                hit = True
                hard.append({"field": path, "rule": rule,
                             "hint": f"去掉 Markdown 标记后输出；上下文 …{text[max(0, m.start() - 8):m.start() + 40]}…"})
        if not hit:
            tail = text[-6:]
            if re.search(r"\*\s*$|\[$|\]\($", tail):
                hard.append({"field": path, "rule": "dangling_marker", "hint": "疑似截断悬空标记，请在完整句处截断"})
        if len(text) > 320 and len(re.findall(r"[。；！？]", text)) < 2:
            warnings.append({"field": path, "rule": "long_unbroken", "hint": "超长段落几乎无断句，建议拆句"})
    t = data.get("today") or {}
    titles = [p.get("title") for p in t.get("picks") or []]
    if len(titles) != len(set(titles)):
        hard.append({"field": "today.picks", "rule": "dup_title", "hint": "精选标题重复"})
    v = (t.get("verdict") or "").strip()
    if not v or len(v) > 200:
        hard.append({"field": "today.verdict", "rule": "verdict_bad", "hint": "总判断必须 1 句且 ≤200 字"})
    for i, p in enumerate(t.get("picks") or []):
        if not (p.get("why") or "").strip():
            hard.append({"field": f"today.picks[{i}].why", "rule": "missing_why", "hint": "缺 why"})
    return {"hard": hard, "warnings": warnings}


def check_html(path: str, build_id: str) -> list:
    """渲染后页面：正文不得再出现 ** / 行首列表符（pre/code 除外）；必须含 build_id。"""
    try:
        html = open(path, encoding="utf-8").read()
    except OSError as e:
        return [{"field": path, "rule": "html_missing", "hint": str(e)}]
    body = re.sub(r"<(pre|code)\b.*?</\1>", "", html, flags=re.S | re.I)
    out = []
    for m in re.finditer(r"\*\*|^\s*[-*]\s|]\(", body, re.M):
        out.append({"field": path, "rule": "leak_in_html",
                    "hint": f"页面文本泄漏标记 …{body[max(0, m.start() - 10):m.start() + 30]}…"})
        break
    if build_id and build_id not in html:
        out.append({"field": path, "rule": "build_mismatch", "hint": "页面未包含当前 build_id"})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="候选 merged JSON（data/magazine-data.json 或 dry-run 产物）")
    ap.add_argument("--html", nargs="*", default=[], help="渲染后的 HTML 页面路径（发布合同第二层）")
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()
    data = json.loads(open(a.data, encoding="utf-8").read())
    res = check_data(data)
    build_id = (data.get("build") or {}).get("build_id", "")
    for hp in a.html:
        res["hard"].extend(check_html(hp, build_id))
    out = {"ok": not res["hard"], "hard": res["hard"], "warnings": res["warnings"],
           "checked": build_id or a.data}
    if a.json_out:
        open(a.json_out, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0 if out["ok"] else 1)


if __name__ == "__main__":
    main()
