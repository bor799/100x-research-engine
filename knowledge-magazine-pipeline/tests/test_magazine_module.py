#!/usr/bin/env python3
"""知识杂志模块单元测试：解析器、校验门禁、渲染幂等。不读写生产文件。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MOD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MOD))
import collect  # noqa: E402
import render  # noqa: E402

WIRE_FIXTURE = """<!doctype html><html><body>
<header><div class="issue"><span>第 <b>64</b> 期</span></div></header>
<div class="wrap-take"><div class="oneline-take">今日一句话：<b>测试导语。</b></div></div>
<section class="sec sec-signal"><div class="sec-head"><span class="no">01</span><h2>今日三件事</h2></div>
<div class="top-card"><span class="tag">治理</span><h3><a href="https://hn/1">指控甲</a><span class="en">Claim A</span></h3>
<p class="why">为什么重要。</p><div class="meta"><span>2026-11-18</span><span>Hacker News</span><span>10 分</span></div></div>
</section>
<section class="sec sec-money"><div class="sec-head"><span class="no">03</span><h2>钱与市场</h2></div>
<table class="odds"><tbody>
<tr data-progress="70%"><td class="q"><a href="https://pm/1" title="Q?">【进展】某盘 70%</a></td><td class="p">70%</td><td class="down">月 -1%</td><td class="vol">$1K</td></tr>
</tbody></table></section>
</body></html>"""


def make_collect(tmp: Path) -> Path:
    wire = tmp / "每日短讯-2026-11-18.html"
    wire.write_text(WIRE_FIXTURE, encoding="utf-8")
    parsed = collect.parse_wire(wire)
    return parsed


class TestWireParser(unittest.TestCase):
    def test_parse_wire(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "每日短讯-2026-11-18.html"
            p.write_text(WIRE_FIXTURE, encoding="utf-8")
            w = collect.parse_wire(p)
            self.assertEqual(w["date"], "2026-11-18")
            self.assertEqual(w["issue_no"], 64)
            self.assertIn("测试导语", w["oneline_take"])
            items = w["sections"][0]["items"]
            self.assertEqual(items[0]["href"], "https://hn/1")
            self.assertEqual(items[0]["original"], "Claim A")
            self.assertEqual(items[0]["why"], "为什么重要。")
            self.assertEqual(len(w["market_rows"]), 1)
            self.assertEqual(w["market_rows"][0]["prob"], "70%")

    def test_empty_structure_aborts(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "每日短讯-2026-11-18.html"
            p.write_text("<html><body>结构变了</body></html>", encoding="utf-8")
            with self.assertRaises(SystemExit):
                collect.parse_wire(p)

    def test_week_numbering(self):
        self.assertEqual(collect.week_of("2026-11-18"), "2026-11-W3")
        self.assertEqual(collect.week_of("2026-11-07"), "2026-11-W1")
        self.assertEqual(collect.week_dates("2026-11-W3")[0], "2026-11-15")
        self.assertEqual(collect.week_dates("2026-11-W3")[-1], "2026-11-21")


def co_fixture() -> dict:
    return {
        "schema": "knowledge-magazine-collect/1",
        "collect_date": "2026-11-19",
        "material_date": "2026-11-18",
        "week": "2026-11-W3",
        "week_dates": collect.week_dates("2026-11-W3"),
        "wire": {"date": "2026-11-18", "issue_no": 64, "oneline_take": "测试导语",
                 "item_count": 2,
                 "sections": [{"no": 1, "name": "今日三件事", "cnt": "", "items": [
                     {"href": "https://hn/1", "title": "指控甲", "original": "Claim A", "why": "为什么重要。",
                      "meta": ["2026-11-18"], "tag": "", "style": "headline"},
                     {"href": "https://hn/2", "title": "工具乙", "original": "", "why": "说明。",
                      "meta": ["2026-11-18"], "tag": "", "style": "item"}]}],
                 "market_rows": [{"href": "https://pm/1", "question": "Q?", "title": "某盘 70%",
                                  "prob": "70%", "trend": "月 -1%", "vol": "$1K", "progress": "70%", "section": "钱与市场"}]},
        "week_wires": [],
        "weekly": {"week": "2026-11-W3", "generated_at": "x",
                   "articles": [{"article_id": "a1", "title": "深读一", "path": "p", "added_on": "2026-11-18",
                                 "url": "https://x/1", "final_score": 0.8, "signal_tier": "A",
                                 "brief": "brief" * 50, "carryover": False, "complete": False,
                                 "obsidian_url": "obsidian://open?vault=V&file=f1"}]},
        "prev_weekly": {"week": "2026-11-W2", "titles": [], "ids": []},
        "obsidian_week": {"dir": "/d", "exists": True, "notes": [], "note_count": 1, "manifest_count": 1, "note_urls": []},
        "stale_sources": ["每日巡视：无"],
        "inputs": {"wire": {"path": "w", "md5": "m1", "mtime": "2026-11-19 08:30", "date": "2026-11-18",
                            "issue_no": 64, "item_count": 2},
                   "weekly": {"path": "p", "md5": "m2", "mtime": "2026-11-19 17:20",
                              "all_candidates": [{"path": "p", "mtime": "2026-11-19 17:20"}],
                              "week": "2026-11-W3", "generated_at": "x", "article_count": 1}},
        "known_urls": ["https://hn/1", "https://hn/2", "https://pm/1", "https://x/1",
                       "obsidian://open?vault=V&file=f1", "https://ext/1", "https://ext/2"],
        "lanes": [{"lane": "每日短讯", "files": 2, "latest": {"name": "每日短讯-2026-11-18.html", "mtime": "2026-11-18 08:30:00"}},
                  {"lane": "知识萃取", "files": 1, "latest": {"name": "知识萃取周刊 2026-11-W3.html", "mtime": "2026-11-18 17:20:00"}},
                  {"lane": "深读", "files": 1, "latest": {"name": "深读特刊-x.html", "mtime": "2026-09-12 10:00:00"}},
                  {"lane": "研究简报", "files": 0, "latest": None},
                  {"lane": "不存在栏目", "missing": True}],
    }


def ed_fixture() -> dict:
    return {
        "schema": "knowledge-magazine-editor/1", "material_date": "2026-11-18",
        "week": "2026-11-W3", "model": "glm-5.3",
        "today": {
            "verdict": "一句总判断。",
            "picks": [{"title": "甲", "why": "为何", "kind": "fact", "status": "verified_multi",
                       "sources": [{"url": "https://hn/1", "label": "HN"}]},
                      {"title": "乙", "why": "为何二", "kind": "inference", "status": "single_source",
                       "sources": [{"url": "https://x/1", "label": "笔记"}]}],
            "groups": [{"title": "主题一", "thread": "线", "hrefs": ["https://hn/1", "https://hn/2"]}],
            "item_notes": {"https://hn/1": {"kind": "opinion", "note": "指控帖。"}},
        },
        "week_view": {
            "what_grew": "本周新增了 X 判断。",
            "topics": [{"slug": "t1", "title": "主题一", "thread": "线",
                        "developments": [{"date": "2026-11-17", "event": "e1", "sources": [{"url": "https://hn/2", "label": "HN"}]},
                                          {"date": "2026-11-18", "event": "e2", "sources": [{"url": "https://x/1", "label": "笔记"}]}],
                        "counterpoints": ["反证一"], "to_verify": ["待核一"], "deep_read_ids": ["a1"]},
                         {"slug": "t2", "title": "主题二", "thread": "线二",
                          "developments": [{"date": "2026-11-16", "event": "e0", "sources": [{"url": "https://pm/1", "label": "PM"}]},
                                           {"date": "2026-11-18", "event": "e3", "sources": [{"url": "https://hn/2", "label": "HN"}]}],
                          "counterpoints": [], "to_verify": [], "deep_read_ids": []}],
        },
        "cross_checks": [
            {"claim": "c1", "status": "verified_multi", "primary_source": "研究甲", "verdict": "口径对上", "counterpoint": "限制一",
             "sources": [{"url": "https://x/1", "label": "笔记", "independent": False},
                         {"url": "https://ext/1", "label": "外部", "independent": True, "fetched": True, "primary_key": "study-a"},
                         {"url": "https://ext/2", "label": "外部二", "independent": True, "fetched": True, "primary_key": "study-b"}]},
            {"claim": "c2", "status": "single_source", "primary_source": "底账乙", "verdict": "单源", "counterpoint": "限制二",
             "sources": [{"url": "https://x/1", "label": "笔记"}, {"url": "https://hn/2", "label": "HN", "independent": True, "primary_key": "ledger-b"}]}],
        "coverage_notes": [],
    }


class TestValidate(unittest.TestCase):
    def setUp(self):
        self.co = co_fixture()
        import unittest.mock
        self._patcher = unittest.mock.patch.object(render, "obsidian_url_to_file", lambda u: Path("/tmp/x") if u else None)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_valid_passes(self):
        self.assertEqual(render.validate(ed_fixture(), self.co), [])

    def test_invented_source_rejected(self):
        ed = ed_fixture()
        ed["today"]["picks"][0]["sources"] = [{"url": "https://invented/1", "label": "x"}]
        errs = render.validate(ed, self.co)
        self.assertTrue(any("不在采集集" in e for e in errs))

    def test_missing_group_coverage_rejected(self):
        ed = ed_fixture()
        ed["today"]["groups"][0]["hrefs"] = ["https://hn/1"]
        errs = render.validate(ed, self.co)
        self.assertTrue(any("分组与短讯条目不一致" in e for e in errs))

    def test_cross_check_needs_two_sources(self):
        ed = ed_fixture()
        ed["cross_checks"][0]["sources"] = ed["cross_checks"][0]["sources"][:1]
        errs = render.validate(ed, self.co)
        self.assertTrue(any("证据不足" in e for e in errs))

    def test_same_primary_key_not_two_votes(self):
        ed = ed_fixture()
        ed["cross_checks"][0]["sources"][2]["primary_key"] = "study-a"  # 与第一条同底层证据
        errs = render.validate(ed, self.co)
        self.assertTrue(any("不同原始证据" in e for e in errs))

    def test_missing_primary_source_rejected(self):
        ed = ed_fixture()
        ed["cross_checks"][0].pop("primary_source")
        errs = render.validate(ed, self.co)
        self.assertTrue(any("primary_source" in e for e in errs))

    def test_week_mismatch_rejected(self):
        ed = ed_fixture()
        ed["week"] = "2026-11-W2"
        errs = render.validate(ed, self.co)
        self.assertTrue(errs)


class TestLanes(unittest.TestCase):
    def test_scan_lanes_counts_and_missing(self):
        import unittest.mock
        with tempfile.TemporaryDirectory() as td:
            hub = Path(td)
            (hub / "每日短讯").mkdir()
            (hub / "每日短讯" / "每日短讯-2026-11-18.html").write_text("x", encoding="utf-8")
            (hub / "每日短讯" / "index.html").write_text("x", encoding="utf-8")
            (hub / "深读").mkdir()
            with unittest.mock.patch.object(collect, "HUB", hub):
                lanes = {l["lane"]: l for l in collect.scan_lanes()}
            self.assertEqual(lanes["每日短讯"]["files"], 1)  # index.html 不计期数
            self.assertEqual(lanes["每日短讯"]["latest"]["name"], "每日短讯-2026-11-18.html")
            self.assertEqual(lanes["深读"]["files"], 0)
            self.assertIsNone(lanes["深读"]["latest"])
            self.assertTrue(lanes["知识萃取"]["missing"])

    def test_coverage_carries_lanes(self):
        m = render.merge(ed_fixture(), co_fixture())
        lanes = m["coverage"]["lanes"]
        self.assertEqual([x["lane"] for x in lanes][:4],
                         ["每日短讯", "知识萃取", "深读", "研究简报"])
        render.MAG_BUILD, render.MAG_BUILT_AT = "test-build", "test-time"
        html = render.render_today(m, [])
        self.assertIn("栏目 每日短讯：2 期", html)
        self.assertIn("栏目 不存在栏目：目录缺失", html)


class TestRenderDeterminism(unittest.TestCase):
    def test_same_inputs_same_build(self):
        co = co_fixture()
        ed = ed_fixture()
        m1 = render.merge(ed, co)
        m2 = render.merge(ed, co)
        import hashlib, json as J
        h1 = hashlib.md5(J.dumps(m1, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        h2 = hashlib.md5(J.dumps(m2, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.assertEqual(h1, h2)

    def test_today_html_contains_essentials(self):
        m = render.merge(ed_fixture(), co_fixture())
        render.MAG_BUILD, render.MAG_BUILT_AT = "test-build", "test-time"
        html = render.render_today(m, [])
        for needle in ["一句总判断", "主题一", "指控帖", "交叉核验", "覆盖与缺口", "某盘 70%", "obsidian://open?vault=V&amp;file=f1"]:
            self.assertIn(needle, html)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------- v2：周证据范围 + 内容层 + 门禁 + 批次

WEEKLY_HTML = """<html><body><script>let ISSUE={"week":"%s","generated_at":"x","articles":[
{"article_id":"a1","title":"深读一","added_on":"2026-11-18","url":"https://x/1",
 "brief":"## 经验\\n- **核心点**: 用自己的固定提示词横向评新模型，比较一致性","final_score":0.8,
 "signal_tier":"A","obsidian_url":null}]}</script></body></html>"""


class TestWeekUrlScopes(unittest.TestCase):
    def setUp(self):
        self.co = co_fixture()
        self.co["week_urls"] = {"https://wire-earlier/1": {"date": "2026-11-17", "issue_no": 63, "channel": "wire"}}

    def _ed(self):
        import copy
        return copy.deepcopy(ed_fixture())

    def test_week_url_allowed_in_topics_and_checks(self):
        ed = self._ed()
        ed["week_view"]["topics"][0]["developments"][0]["sources"] = [
            {"url": "https://wire-earlier/1", "label": "短讯·17日"}]
        ed["cross_checks"][0]["sources"][1]["url"] = "https://wire-earlier/1"
        ed["cross_checks"][0]["sources"][1]["primary_key"] = "wire-17"
        import unittest.mock
        with unittest.mock.patch.object(render, "obsidian_url_to_file", lambda u: Path("/tmp/x") if u else None):
            self.assertEqual(render.validate(ed, self.co), [])

    def test_week_url_rejected_in_today_picks(self):
        ed = self._ed()
        ed["today"]["picks"][0]["sources"] = [{"url": "https://wire-earlier/1", "label": "x"}]
        import unittest.mock
        with unittest.mock.patch.object(render, "obsidian_url_to_file", lambda u: Path("/tmp/x") if u else None):
            errs = render.validate(ed, self.co)
            self.assertTrue(any("不在采集集" in e for e in errs))

    def test_cross_week_url_rejected_everywhere(self):
        ed = self._ed()
        ed["week_view"]["topics"][0]["developments"][0]["sources"] = [
            {"url": "https://other-week/9", "label": "x"}]
        import unittest.mock
        with unittest.mock.patch.object(render, "obsidian_url_to_file", lambda u: Path("/tmp/x") if u else None):
            errs = render.validate(ed, self.co)
            self.assertTrue(any("不在本周采集集" in e for e in errs))

    def test_merge_enriches_wire_date(self):
        self.co["week_urls"]["https://hn/2"] = {"date": "2026-11-17", "issue_no": 63, "channel": "wire"}
        ed = self._ed()
        m = render.merge(ed, self.co)
        self.assertIn("wire_date", m["week_view"]["topics"][0]["developments"][0]["sources"][0] or {})


class TestContentLayer(unittest.TestCase):
    def test_user_example_renders_without_leak(self):
        from content import parse_markdown, blocks_to_html, flat_text
        raw = "## 经验\n- **核心点**: 用自己的固定提示词横向评新模型\n- **核心点**: 给长程 Agent 下目标"
        r = parse_markdown(raw)
        self.assertEqual(r["kind"], "md")
        self.assertNotIn("**", r["flat"])
        html = blocks_to_html(r["blocks"])
        self.assertIn("<strong>核心点</strong>", html)
        self.assertIn("<ul><li>", html)

    def test_plain_asterisk_preserved(self):
        from content import parse_markdown
        r = parse_markdown("C* 运算与 a*b=c 的星号保留")
        self.assertEqual(r["kind"], "plain")
        self.assertIn("*", r["flat"])

    def test_malformed_safe(self):
        from content import parse_markdown, blocks_to_html
        html = blocks_to_html(parse_markdown("- **未闭合\n- [x](javascript:alert(1)) <script>")["blocks"])
        self.assertNotIn("<script>", html)
        self.assertNotIn("javascript:", html)

    def test_truncate_at_block_boundary(self):
        from content import md_field
        f = md_field("\n".join(["- 甲条目内容较长"] * 20), limit=40)
        self.assertNotIn("**", f["flat"])
        self.assertTrue("…" in f["flat"] and len(f["flat"]) <= 120)

    def test_merge_emits_md_fields_and_gate_passes(self):
        co = co_fixture()
        co["weekly"]["articles"][0]["brief"] = "## 经验\n- **核心点**: 用同一条提示词评新模型"
        co["week_view_stub"] = None
        ed = ed_fixture()
        ed["week_view"]["topics"][0]["deep_read_ids"] = ["a1"]
        m = render.merge(ed, co)
        joined = [a for a in m["week_view"]["deep_reads"] if a["article_id"] == "a1"][0]
        self.assertIn("brief_md", joined)
        self.assertNotIn("**", joined["brief"])
        from readability_gate import check_data
        res = check_data(m)
        self.assertEqual(res["hard"], [])

    def test_gate_catches_leak_and_overgeneralized_label(self):
        from readability_gate import check_data
        bad = {"today": {"verdict": "今日判断。",
                         "picks": [{"title": "A", "why": "- **核心点**: 泛化句子", "kind": "fact"},
                                   {"title": "B", "why": "正常句子。", "kind": "fact"},
                                   {"title": "A", "why": "重复标题。", "kind": "fact"}]}}
        res = check_data(bad)
        rules = {h["rule"] for h in res["hard"]}
        self.assertIn("leak_bold", rules)
        self.assertIn("leak_bullet", rules)
        self.assertIn("dup_title", rules)


class TestPlannerAndBatch(unittest.TestCase):
    """collect --plan 决策矩阵 + render 批次提交/自愈/幂等（全部临时目录）。"""

    def _hub(self, td: Path, wire_dates, week="2026-11-W3", weekly=True):
        import unittest.mock
        hub = td / "hub"; hub.mkdir()
        (hub / "每日短讯").mkdir()
        for d in wire_dates:
            (hub / "每日短讯" / f"每日短讯-{d}.html").write_text(
                WIRE_FIXTURE.replace("2026-11-18", d), encoding="utf-8")
        (hub / "杂志" / "data").mkdir(parents=True)
        vault_root = td / "vault"
        vault = vault_root / week; vault.mkdir(parents=True)
        if weekly:
            (hub / "知识萃取").mkdir()
            (hub / "知识萃取" / f"知识萃取周刊 {week}.html").write_text(WEEKLY_HTML % week, encoding="utf-8")
        state = td / "state"; state.mkdir()
        (vault / "2026-11-17 note.md").write_text("---\ntitle: n\n---\nbody", encoding="utf-8")
        return hub, vault_root, state

    def _patch(self, hub, vault, state):
        import unittest.mock
        import collect as C
        return [unittest.mock.patch.object(C, "HUB", hub),
                unittest.mock.patch.object(C, "VAULT", vault),
                unittest.mock.patch.object(C, "WIRE_DIR", hub / "每日短讯"),
                unittest.mock.patch.object(C, "WEEKLY_HUB", hub / "知识萃取"),
                unittest.mock.patch.object(C, "STATE", state),
                unittest.mock.patch.object(C, "STATUS_PATH", hub / "杂志" / "data" / "publication-status.json")]

    def test_plan_matrix(self):
        import collect as C
        with tempfile.TemporaryDirectory() as td:
            hub, vault, state = self._hub(Path(td), ["2026-11-18", "2026-11-20"])
            patches = self._patch(hub, vault, state)
            for p in patches: p.start()
            try:
                # 无指纹 → publish
                self.assertEqual(C.plan("2026-11-20")["decision"], "publish")
                # 写入指纹后材料不变 → skip（指纹与 plan 同源同形）
                (state / "precheck-last.json").write_text(json.dumps(
                    {"wire_md5": C.select_inputs()["inputs"]["wire"]["md5"],
                     "weekly_md5": C.select_inputs()["inputs"]["weekly"]["md5"],
                     "week_dir": C.week_dir_fingerprint(C.select_inputs()["week_dir"]["dir"])}), encoding="utf-8")
                pl = C.plan("2026-11-20")
                self.assertEqual(pl["decision"], "skip")
                self.assertIn("非闭刊日", pl["reason"])
                # 新短讯到达 → publish（11-21 = 窗口末日同时也是新材料）
                (hub / "每日短讯" / "每日短讯-2026-11-21.html").write_text(
                    WIRE_FIXTURE.replace("2026-11-18", "2026-11-21"), encoding="utf-8")
                self.assertEqual(C.plan("2026-11-21")["decision"], "publish")
                # 材料不变且今日已出刊 → 闭刊日也 skip
                (hub / "杂志" / "data" / "magazine-data.json").write_text(
                    json.dumps({"build": {"built_at": "2026-11-21 17:40"}, "week": "2026-11-W3"}), encoding="utf-8")
                self.assertEqual(C.plan("2026-11-21")["decision"], "publish")  # wire md5 已变，先重置指纹
                (state / "precheck-last.json").write_text(json.dumps(
                    {"wire_md5": C.select_inputs()["inputs"]["wire"]["md5"],
                     "weekly_md5": None, "week_dir": C.week_dir_fingerprint(C.select_inputs()["week_dir"]["dir"])}), encoding="utf-8")
                pl = C.plan("2026-11-21")
                self.assertEqual(pl["decision"], "skip")
                self.assertIn("闭刊日已出刊", pl["reason"])
            finally:
                for p in self._patch(hub, vault, state): p.stop()

    def test_plan_month_end_window(self):
        """W5 窗口在月末自然截断（11 月只有 30 日），不滚成 12 月。"""
        import collect as C
        self.assertEqual(C.week_dates("2026-11-W5"), ["2026-11-29", "2026-11-30"])
        self.assertEqual(C.week_dates("2026-11-W4")[-1], "2026-11-28")

    def test_plan_weekly_missing_degrades(self):
        import collect as C
        with tempfile.TemporaryDirectory() as td:
            hub, vault, state = self._hub(Path(td), ["2026-11-18"], weekly=False)
            patches = self._patch(hub, vault, state)
            for p in patches: p.start()
            try:
                sel = C.select_inputs()
                self.assertTrue(sel["weekly_degraded"])
                self.assertEqual(sel["weekly"]["articles"], [])
                pl = C.plan("2026-11-18")
                self.assertEqual(pl["decision"], "publish")
                self.assertTrue(pl["weekly"]["degraded"])
            finally:
                for p in patches: p.stop()

    def test_batch_pointer_semantics(self):
        """不可变批次 + 唯一指针：故障态读者读旧批，指针切换后读新批，损坏自愈重指。"""
        import render as R
        import shutil as sh
        with tempfile.TemporaryDirectory() as td:
            hub, vault, state = self._hub(Path(td), ["2026-11-18"])
            mag = hub / "杂志"
            import unittest.mock
            import collect as C
            keep = [unittest.mock.patch.object(R, "HUB", hub), unittest.mock.patch.object(R, "MAG", mag),
                    unittest.mock.patch.object(R, "STATE", state),
                    unittest.mock.patch.object(R, "POINTER", mag / "batch-pointer.js"),
                    unittest.mock.patch.object(R, "BATCHES", mag / "batches"),
                    unittest.mock.patch.object(C, "STATUS_PATH", mag / "data" / "publication-status.json")]
            for p in keep: p.start()
            try:
                def batch(bid, body):
                    payload = {f"今日-{bid}.html": f"<html>{body}<!--{bid}--></html>",
                               "magazine-data.json": json.dumps({"build": {"build_id": bid}})}
                    meta = {"schema": "knowledge-magazine-batch/2", "build_id": bid, "built_at": f"2026-11-{bid[-2:]} 18:00",
                            "material_date": f"2026-11-{bid[-2:]}", "week": "2026-11-W3",
                            "files": {n: R.md5_str(c) for n, c in payload.items()}}
                    return payload, meta
                p1, m1 = batch("2026-11-19-b1aaaaaa", "b1")
                p2, m2 = batch("2026-11-20-b2bbbbbb", "b2")
                R.stage_batch(p1, m1); m1["dir"] = "batches/" + m1["build_id"]
                self.assertTrue(R.batch_ok(m1))
                R.write_pointer(m1)
                self.assertEqual(R.read_pointer()["build_id"], m1["build_id"])

                # 故障A：新批完整写好但指针未切 → 读者（指针解析）仍读 b1
                R.stage_batch(p2, m2); m2["dir"] = "batches/" + m2["build_id"]
                self.assertEqual(R.read_pointer()["build_id"], m1["build_id"], "指针未切＝读者读旧完整批")
                self.assertEqual(R.verify_or_heal(), "consistent")

                # 成功提交：切指针 → 读者读 b2；history 记 b1
                m2["history"] = [{"build_id": m2["build_id"], "material_date": m2["material_date"]},
                                 {"build_id": m1["build_id"], "material_date": m1["material_date"]}]
                R.write_pointer(m2)
                self.assertEqual(R.read_pointer()["build_id"], m2["build_id"])

                # 故障B：当前批目录损坏 → 自愈重指 b1
                (R.BATCHES / m2["build_id"] / "今日-2026-11-20-b2bbbbbb.html").unlink()
                self.assertEqual(R.verify_or_heal(), "healed")
                self.assertEqual(R.read_pointer()["build_id"], m1["build_id"])

                # 故障C：staging 半成品目录存在（rename 前）→ 不影响指针与读者
                stg = R.BATCHES / "2026-11-21-b3.staging"
                stg.mkdir(parents=True, exist_ok=True)
                (stg / "今日-x.html").write_text("half", encoding="utf-8")
                self.assertEqual(R.verify_or_heal(), "consistent")
            finally:
                for p in keep: p.stop()
