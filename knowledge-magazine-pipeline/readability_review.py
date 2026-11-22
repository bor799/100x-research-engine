#!/usr/bin/env python3
"""知识杂志 · 语义可读性审核（记录与校验工具）。

审稿是同一 GLM 5.3 的独立步骤：先读候选（state/dryrun-candidate.json），按契约逐字段
给出结构化结论，写入报告文件，再由 --record 校验后落账。脚本绝不从候选自填「已审」：
covered 集合完全来自报告本身，缺项/重复/空报告/手填 pass 一律非零阻断。

必审集合（核心可见字段，逐条给 verdict + 短转述）：
  today.verdict / today.picks[i] / today.cross_checks[i] /
  week_view.what_grew / week_view.topics[i]
分组结论（上游材料为主的辅助字段，每组一个 verdict）：
  today.new_notes（今日新增萃取简报）/ week_view.deep_reads（本周深读简报）

契约（每条必审须能回答）：谁做了什么、为什么值得看；适用范围/证据限制；
关键术语首现随文说明；数字带口径；转述不混作独立证据；无主语句/重复标签/泛化空句
（如：同一提示词的一次比较不得概括成模型整体排名）。
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
import json
import sys
from pathlib import Path

STATE = _km_home("KM_STATE|Desktop|知识杂志|模块-知识杂志|state")
RULES_VERSION = "readability/2026-11-21b"
MUST_VERDICTS = {"pass", "fail"}
GROUP_FIELDS = {"today.new_notes", "week_view.deep_reads"}


def candidate_sha256(path: str) -> str:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data.pop("build", None)  # build 含时间戳，排除后哈希仅绑定内容
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def must_review_set(candidate: dict) -> list:
    """必审字段路径（与候选结构一致；路径必须真实存在于候选，属结构校验而非审稿代填）。"""
    out = []
    t = candidate.get("today") or {}
    if (t.get("verdict") or "").strip():
        out.append("today.verdict")
    out += [f"today.picks[{i}]" for i in range(len(t.get("picks") or []))]
    out += [f"today.cross_checks[{i}]" for i in range(len(t.get("cross_checks") or []))]
    w = candidate.get("week_view") or {}
    if (w.get("what_grew") or "").strip():
        out.append("week_view.what_grew")
    out += [f"week_view.topics[{i}]" for i in range(len(w.get("topics") or []))]
    return out


def path_exists(candidate: dict, path: str) -> bool:
    node = candidate
    tokens = path.split(".")
    for ti, tok in enumerate(tokens):
        if "[" in tok:
            key, idx = tok.split("[")[0], int(tok.split("[")[1].rstrip("]"))
            if not isinstance(node, dict) or key not in node:
                return False
            node = node[key]
            if not isinstance(node, list) or idx >= len(node):
                return False
            node = node[idx]
        else:
            if not isinstance(node, dict) or tok not in node:
                return False
            node = node[tok]
    return True


def validate_report(report: dict, candidate: dict) -> list:
    errs = []
    reviews = report.get("reviews")
    if not isinstance(reviews, list) or not reviews:
        return ["报告缺 reviews（空报告＝未审，不得记录 pass）"]
    seen = {}
    for i, r in enumerate(reviews):
        f = r.get("field")
        if not f:
            errs.append(f"reviews[{i}] 缺 field")
            continue
        if f in seen:
            errs.append(f"重复审查条目: {f}")
        seen[f] = r
        if r.get("verdict") not in MUST_VERDICTS:
            errs.append(f"{f}: verdict 非法（{r.get('verdict')}），只能 pass|fail")
        para = (r.get("paraphrase") or "").strip()
        if len(para) < 8:
            errs.append(f"{f}: 缺短转述（paraphrase ≥8 字，证明真读了内容）")
        if not path_exists(candidate, f):
            errs.append(f"{f}: 路径在候选中不存在（结构不符）")
    must = must_review_set(candidate)
    missing = [m for m in must if m not in seen]
    if missing:
        errs.append("必审字段缺失: " + ", ".join(missing))
    extra = [f for f in seen if f not in must]
    if extra:
        errs.append("非必审字段混入 reviews（分组字段请放 groups）: " + ", ".join(extra))
    groups = report.get("groups") or []
    gseen = set()
    for g in groups:
        gf = g.get("field")
        if gf not in GROUP_FIELDS:
            errs.append(f"groups 非法字段: {gf}（允许 {sorted(GROUP_FIELDS)}）")
        if gf in gseen:
            errs.append(f"groups 重复: {gf}")
        gseen.add(gf)
        if g.get("verdict") not in MUST_VERDICTS:
            errs.append(f"{gf}: 分组 verdict 非法")
    for gf in GROUP_FIELDS - gseen:
        errs.append(f"缺分组结论: {gf}")
    return errs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="候选 merged JSON（dryrun-candidate.json 或 data/magazine-data.json）")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--report", default=None, help="审稿报告 JSON：{reviews:[{field,verdict,paraphrase,issue?,scope?}],groups:[{field,verdict,note?}]}")
    ap.add_argument("--reviewer", default=None)
    a = ap.parse_args()
    sha = candidate_sha256(a.data)
    candidate = json.loads(Path(a.data).read_text(encoding="utf-8"))
    if not a.record:
        print(json.dumps({"candidate_sha256": sha, "rules_version": RULES_VERSION,
                          "must_review": must_review_set(candidate),
                          "groups_required": sorted(GROUP_FIELDS),
                          "contract": "每条必审：谁做了什么/为什么值得看/适用范围与证据限制；转述不混独立证据；同一提示词单次比较不得概括成模型排名。"}, ensure_ascii=False, indent=1))
        return
    if not a.report:
        print(json.dumps({"ok": False, "error": "--record 需要 --report（结构化审稿报告）"}), file=sys.stderr)
        sys.exit(2)
    report = json.loads(Path(a.report).read_text(encoding="utf-8"))
    errs = validate_report(report, candidate)
    if errs:
        print(json.dumps({"ok": False, "error": "审稿报告不合规，未记录", "problems": errs[:12]}, ensure_ascii=False))
        sys.exit(1)
    fails = [r["field"] for r in report["reviews"] if r.get("verdict") == "fail"] \
        + [g["field"] for g in report.get("groups") or [] if g.get("verdict") == "fail"]
    covered = [r["field"] for r in report["reviews"]] + [g["field"] for g in report.get("groups") or []]
    rec = {"schema": "knowledge-magazine-readability/2", "rules_version": RULES_VERSION,
           "candidate_sha256": sha, "pass": not fails, "verdict": "pass" if not fails else "fail",
           "covered_fields": covered, "review_fail_fields": fails,
           "report": report, "reviewer": a.reviewer or "glm-5.3",
           "at": dt.datetime.now().isoformat(timespec="seconds")}
    out = STATE / "readability-latest.json"
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "recorded": str(out), "pass": rec["pass"],
                      "covered": len(covered), "fails": fails,
                      "note": "fail 同样落账供修订重审；render 以 pass 且 sha 一致为放行条件"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
