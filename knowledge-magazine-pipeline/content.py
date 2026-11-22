#!/usr/bin/env python3
"""知识杂志 · 内容层：唯一 Markdown 安全解析，输出共用块表示。

两个出口（桌面 HTML 由 render.py、Cindy 插件由 magazine.js）都消费同一 blocks
结构，不存在第二套 Markdown 配方。规则确定性：
- 识别子集：标题 #、无序/有序列表（含 2 空格缩进嵌套）、引用 >、围栏代码、
  行内 **粗**、*斜*、`代码`、[文字](链接)。
- 链接只放行 http/https/obsidian 协议；其余协议去链接保文字。
- 不放行任何 raw HTML：尖括号一律按字面文本处理（渲染端转义）。
- 未配对的 * / [ 等标记按字面保留（纠正畸形，不阻断内容）；解析失败退化为纯文本。
- 截断只发生在块边界，绝不切断行内标记。
"""
from __future__ import annotations

import os as _os
from pathlib import Path as _P
def _km_home(rel: str) -> Path:
    """仓库版：路径经环境变量注入（KM_HUB / KM_VAULT_ROOT），缺省落到当前用户主目录同名结构；
    本机生产目录继续以默认值工作，测试与 CI 用环境变量或 mock 覆盖。"""
    env, *sub = rel.split('|')
    return _P(_os.environ.get(env) or _P.home().joinpath(*sub))

import re

SAFE_HREF = re.compile(r"^(https?://|obsidian://)", re.I)
HEADER = re.compile(r"^(#{1,6})\s+(.*)$")
UL_LINE = re.compile(r"^(\s*)[-*·]\s+(.*)$")
OL_LINE = re.compile(r"^(\s*)\d+[.、]\s+(.*)$")
QUOTE = re.compile(r"^>\s?(.*)$")
FENCE = re.compile(r"^```")
HR = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
INLINE = re.compile(r"(\*\*[^*\n]+?\*\*|\*[^*\n]+?\*|`[^`\n]+?`|\[[^\]\n]+?\]\([^)\n]+?\))")


def _inline_runs(text: str) -> list:
    """行内解析 → runs；未配对标记按字面保留。"""
    runs, pos = [], 0
    for m in INLINE.finditer(text):
        if m.start() > pos:
            runs.append({"t": "text", "text": text[pos:m.start()]})
        tok = m.group(0)
        if tok.startswith("**"):
            runs.append({"t": "b", "text": tok[2:-2]})
        elif tok.startswith("`"):
            runs.append({"t": "code", "text": tok[1:-1]})
        elif tok.startswith("["):
            lm = re.match(r"^\[([^\]]+)\]\(([^)]+)\)$", tok)
            label, href = lm.group(1), lm.group(2).strip()
            if SAFE_HREF.match(href):
                runs.append({"t": "a", "text": label, "href": href})
            else:  # 非白名单协议：去链接保文字
                runs.append({"t": "text", "text": label})
        else:
            runs.append({"t": "i", "text": tok[1:-1]})
        pos = m.end()
    if pos < len(text) or not runs:
        tail = text[pos:]
        if tail or not runs:
            runs.append({"t": "text", "text": tail})
    return [r for r in runs if r.get("text") or r["t"] == "a"]


def _flatten_runs(runs: list) -> str:
    return "".join(r["text"] for r in runs)


def parse_markdown(text: str) -> dict:
    """返回 {kind: md|plain, blocks, flat}。plain 保持原文（含合法星号），flat 恒为去标记纯文本。"""
    text = "" if text is None else str(text)
    if not any(re.search(p, text) for p in
               (r"\*\*", r"(?m)^#{1,6}\s", r"(?m)^\s*[-*·]\s+", r"(?m)^>\s?", r"`[^`\n]+`",
                r"\[[^\]\n]+\]\([^)\n]+\)", r"(?m)^(-{3,}|\*{3,}|_{3,})$")):
        return {"kind": "plain", "blocks": [{"type": "p", "runs": [{"t": "text", "text": text}]}],
                "flat": text.strip()}

    blocks, in_code, code_buf = [], False, []
    for line in text.splitlines():
        if FENCE.match(line):
            if in_code:
                blocks.append({"type": "code", "text": "\n".join(code_buf)})
                code_buf, in_code = [], False
            else:
                in_code = True
            continue
        if in_code:
            code_buf.append(line)
            continue
        if not line.strip():
            continue
        if HR.match(line.strip()):  # 分隔线：仅分段，不产生可见内容
            continue
        h = HEADER.match(line)
        if h:
            blocks.append({"type": "h", "text": h.group(2).strip()})
            continue
        if QUOTE.match(line):
            blocks.append({"type": "quote", "runs": _inline_runs(QUOTE.match(line).group(1))})
            continue
        u = UL_LINE.match(line)
        o = OL_LINE.match(line)
        if u or o:
            indent = len((u or o).group(1)) // 2
            runs = _inline_runs((u or o).group(2))
            blocks.append({"type": "li", "ordered": bool(o), "indent": indent, "runs": runs})
            continue
        blocks.append({"type": "p", "runs": _inline_runs(line.strip())})
    if in_code and code_buf:  # 未闭合围栏：按普通代码块收尾（纠正畸形）
        blocks.append({"type": "code", "text": "\n".join(code_buf)})

    if not blocks:
        return {"kind": "plain", "blocks": [{"type": "p", "runs": [{"t": "text", "text": text}]}],
                "flat": text.strip()}
    return {"kind": "md", "blocks": blocks, "flat": flat_text(blocks)}


def flat_text(blocks: list) -> str:
    """块表示 → 可读纯文本（列表并句、标题去标记），用于插件回退、工具输出与门禁。"""
    out, li_buf = [], []
    for b in blocks:
        if b["type"] == "li":
            li_buf.append(_flatten_runs(b["runs"]))
            continue
        if li_buf:
            out.append("；".join(li_buf) + "。")
            li_buf = []
        if b["type"] == "h":
            out.append(b["text"])
        elif b["type"] in ("p", "quote"):
            t = _flatten_runs(b["runs"]).strip()
            if t:
                out.append(t)
        elif b["type"] == "code":
            out.append(b["text"])
    if li_buf:
        out.append("；".join(li_buf) + "。")
    return "\n".join(x for x in out if x).strip()


def md_field(value, limit: int | None = None) -> dict:
    """字段级包装：{flat, blocks?, kind}。blocks 仅在有实际结构/标记时携带。"""
    parsed = parse_markdown(value)
    blocks = parsed["blocks"]
    if limit:
        blocks = truncate_blocks(blocks, limit)
    out = {"flat": parsed["flat"] if not limit else flat_text(blocks), "kind": parsed["kind"]}
    if parsed["kind"] == "md":
        out["blocks"] = blocks
    return out


def truncate_blocks(blocks: list, limit: int) -> list:
    """只整块丢弃，永不切断行内标记；末尾补省略号段。"""
    kept, used = [], 0
    for b in blocks:
        n = len(b.get("text") or _flatten_runs(b.get("runs") or []))
        if used + n > limit and kept:
            kept.append({"type": "p", "runs": [{"t": "text", "text": "…"}]})
            return kept
        kept.append(b)
        used += n
    return kept


def blocks_to_html(blocks: list) -> str:
    """块表示 → 安全 HTML（全部转义，仅白名单标签；链接仅白名单协议）。"""
    import html as H
    out, ul_open, ol_open = [], False, False

    def runs_html(runs):
        parts = []
        for r in runs:
            t = H.escape(r.get("text", ""))
            if r["t"] == "b":
                parts.append(f"<strong>{t}</strong>")
            elif r["t"] == "i":
                parts.append(f"<em>{t}</em>")
            elif r["t"] == "code":
                parts.append(f"<code>{t}</code>")
            elif r["t"] == "a" and SAFE_HREF.match(r.get("href", "")):
                parts.append(f'<a href="{H.escape(r["href"], quote=True)}">{t}</a>')
            else:
                parts.append(t)
        return "".join(parts)

    def close_lists():
        nonlocal ul_open, ol_open
        if ul_open:
            out.append("</ul>")
            ul_open = False
        if ol_open:
            out.append("</ol>")
            ol_open = False

    prev_indent = 0
    for b in blocks:
        if b["type"] == "li":
            if b.get("ordered") and not ol_open:
                close_lists()
                out.append("<ol>")
                ol_open = True
            elif not b.get("ordered") and not ul_open:
                close_lists()
                out.append("<ul>")
                ul_open = True
            elif b.get("ordered") != ol_open:
                close_lists()
            cls = ' class="lv2"' if b.get("indent", 0) > 0 else ""
            out.append(f"<li{cls}>{runs_html(b['runs'])}</li>")
            prev_indent = b.get("indent", 0)
            continue
        close_lists()
        if b["type"] == "h":
            out.append(f"<h4>{H.escape(b['text'])}</h4>")
        elif b["type"] == "p":
            out.append(f"<p>{runs_html(b['runs'])}</p>")
        elif b["type"] == "quote":
            out.append(f"<blockquote>{runs_html(b['runs'])}</blockquote>")
        elif b["type"] == "code":
            out.append(f"<pre><code>{H.escape(b['text'])}</code></pre>")
    close_lists()
    return "".join(out)
