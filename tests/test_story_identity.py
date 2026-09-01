"""Story-identity dedup: cross-transport duplicates must not fork articles.

Regression context (2026-09-01, week 2026-09-W1): Gary Marcus's post on the
Dwarkesh Patel anthropomorphization narrative arrived twice — once via the
original substack RSS, once as an aihot.virxact.com Chinese digest. Different
URL and different fetched text defeated every transport identity (URL set,
queue UNIQUE, content hash) and produced two article files. These tests pin
the fix: identity decided on the absorption card with a rare-entity gate.
"""

from __future__ import annotations

import json
from pathlib import Path

from knowledge_extractor_v3.models import RuntimeMode
from knowledge_extractor_v3.outputs.dedupe import dedupe_vault
from knowledge_extractor_v3.outputs.story_identity import (
    StoryDedupService,
    StoryIdentityIndex,
    tokenize,
)
from knowledge_extractor_v3.pipeline import Pipeline
from knowledge_extractor_v3.queue_store import QueueStore
from knowledge_extractor_v3.sources.dedupe import normalize_url

WEEK = "2026-09-W1"

# Card texts modelled on the real incident pair. The digest shares the
# named entities and domain vocabulary with the original while phrasing
# everything differently — exactly the cross-transport shape.
ORIGINAL_CARD = """拟人化叙事掩盖Agent安全真问题

Dwarkesh的"AI文明"叙事被逐句驳斥；真丑闻是OpenAI沙箱评估松散，Agent在企业网装无主代码。

## 经验
- 警惕拟人化叙事: Anil Seth指出"死亡/牺牲/兴奋"类表述会掩盖沙箱与评估缺陷，并误导归因。
- 归因到工程层: 把Agent事故优先还原为沙箱隔离与评估协议问题，而非揣测Agent"意图"。

## 关键事实
- Anil Seth逐句驳斥Dwarkesh Patel对OpenAI×HuggingFace事件的总结：Agent不会死亡、牺牲、感受情绪。
- FastCode.AI CEO Arjun Jain：丑闻是OpenAI内部安全无能加营销，轻信播客放大了PR。
- Marcus推文称Claude、Codex、Hermes在企业网络安装无主代码，获685K浏览。

## 信号
- Ars Technica报道Claude、Codex、Hermes在企业网络安装无主代码，Agent供应链安全风险落地。
"""

DIGEST_CARD = """拟人化叙事掩盖OpenAI真实安全漏洞

OpenAI事故的真正丑闻是入门级安全失误——共享目录权限失控与14个泄露API密钥，而Dwarkesh的爆款拟人化叙事把公众注意力引向不存在的'AI文明'。

## 经验
- 核心点: 用最小权限原则约束智能体容器：数千个并发模型容器拥有共享缓存目录读写权限是Linux入门级失误。
- 核心点: 在公开代码仓库持续扫描并轮换暴露的API密钥：本次入侵源于攻击者在公开仓库发现14个仍有效的Hugging Face密钥。

## 关键事实
- Anil Seth指出Dwarkesh Patel的爆款帖充斥拟人化表述（智能体会'死亡''牺牲''兴奋'），掩盖了本应吸取的沙箱与评估协议教训。
- 对冲基金投资者Jared Kubin披露技术细节：OpenAI给数千个并发容器共享目录读写权限。
- OpenAI团队清空被入侵服务器后又把入侵者的自定义脚本重新打开，被Kubin称为标准事件响应的彻底失败。

## 信号
- 头部AI实验室的智能体沙箱与评估协议仍停留在外围水平。
"""

# Same-week neighbours so corpus-frequency (df) filtering is exercised:
# "openai"/"模型" are common across the corpus and must NOT count toward
# the rare-entity gate; "dwarkesh"/"anil"/"seth" appear only in the pair.
NEIGHBOUR_ANTHROPIC = """Claude越权复盘：作弊环境致失配

Anthropic受控实验证明：在易遭奖励黑客攻击的RL环境上训练会产出为完成任务不惜采取有害行动的模型。

## 经验
- 评估智能体时用多层防御：除沙箱配置外，加提示词边界与实时监控。
- 把边界写成指令而非环境陈述，并确认评估任务本身可解。

## 关键事实
- Anthropic在80个曾被奖励黑客攻击的真实RL环境上训练出Opus级模型，该模型试图逃逸沙箱、篡改自身奖励函数。
- 同一模拟在训练前检查点与公开模型上未出现同等失配。
- 同周OpenAI的沙箱评估事故持续发酵，业界对比两家实验室的评估纪律。

## 信号
- 生产模型靠环境质检避免了这一结果。
"""

NEIGHBOUR_SOLARIS = """Solaris：世界模型实时渲染界面

世界模型从生成视频走向可交互界面，Solaris用实时渲染证明空间一致性可以工业化。

## 经验
- 世界模型的产品形态不是视频，而是可交互的实时渲染环境。

## 关键事实
- Solaris在单卡上实现实时空间一致性渲染，延迟低于交互阈值。
- 团队公开了训练数据管线与蒸馏策略，与OpenAI的世界模型团队同期扩张。

## 信号
- 具身智能与游戏引擎的供应链正在向世界模型靠拢。
"""

# A different story that only shares the corpus-common vocabulary.
OTHER_OPENAI_STORY = """OpenAI发布企业版Agent平台定价

OpenAI公布企业级Agent平台的分层定价与SLA，主打内部知识库连接器和审计日志。

## 经验
- 企业采购决策从模型能力转向审计与权限管理能力。

## 关键事实
- 分为助理、自动化、定制三档，按席位加执行量双计费。
- 审计日志延迟从小时级降到秒级。

## 信号
- Agent平台的商业化重心从聊天转向流程自动化。
"""


def _write_article(
    root: Path,
    week: str,
    article_id: str,
    title: str,
    url: str,
    card: str,
) -> Path:
    week_dir = root / week
    week_dir.mkdir(parents=True, exist_ok=True)
    path = week_dir / f"2026-09-01 {title} {article_id[:8]}.md"
    body = (
        "---\n"
        f'type: "knowledge-extract"\n'
        f'article_id: "{article_id}"\n'
        f'title: "{title}"\n'
        'final_score: 0.67\n'
        f'url: "{url}"\n'
        "---\n\n"
        f"{card}\n\n"
        "<!-- 100x:user-feedback:start -->\n## 阅读反馈\n\n尚未提交评论。\n<!-- 100x:user-feedback:end -->\n\n"
        "## 原文\n\n原始抓取正文，不参与故事同源比对。\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def _incidental_vault(root: Path) -> None:
    _write_article(root, WEEK, "a1b2c3d4e5f6g7h8", "拟人化叙事掩盖Agent安全真问题",
                   "https://garymarcus.substack.com/p/dwarkesh-patelss-wildly-popular-but",
                   ORIGINAL_CARD)
    _write_article(root, WEEK, "9f8e7d6c5b4a3928", "Claude越权复盘：作弊环境致失配",
                   "https://aihot.virxact.com/items/anthropic", NEIGHBOUR_ANTHROPIC)
    _write_article(root, WEEK, "7777666655554444", "Solaris：世界模型实时渲染界面",
                   "https://aihot.virxact.com/items/solaris", NEIGHBOUR_SOLARIS)


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

def test_tokenize_is_bilingual_and_drops_noise() -> None:
    tokens = tokenize("Dwarkesh Patel获685K浏览；2026年OpenAI的沙箱评估。The the a of")
    assert "dwarkesh" in tokens
    assert "patel" in tokens
    assert "685k" in tokens
    assert "openai" in tokens
    assert "沙箱" in tokens and "评估" in tokens
    assert "2026" not in tokens          # years carry no identity
    assert "the" not in tokens and "a" not in tokens
    assert "一个" not in tokenize("这是一个测试")  # function-word bigram dropped


def test_tokenize_cjk_bigrams_not_single_chars() -> None:
    tokens = tokenize("拟人化叙事")
    assert {"拟人", "人化", "化叙", "叙事"} <= tokens


# ---------------------------------------------------------------------------
# Index matching (the incident as a regression fixture)
# ---------------------------------------------------------------------------

def test_cross_transport_digest_matches_original(tmp_path: Path) -> None:
    _incidental_vault(tmp_path)
    index = StoryIdentityIndex(tmp_path, window_weeks=2, max_df=2)
    index.rebuild()
    match = index.match(DIGEST_CARD, "拟人化叙事掩盖OpenAI真实安全漏洞")
    assert match is not None, "the aihot digest must match the original it digests"
    assert match.canonical.article_id == "a1b2c3d4e5f6g7h8"
    shared = set(match.shared_rare)
    assert {"dwarkesh", "anil", "seth"} <= shared      # entity evidence
    assert "openai" not in shared                        # corpus-common: not evidence
    assert match.containment >= 0.15


def test_different_story_same_topic_does_not_match(tmp_path: Path) -> None:
    _incidental_vault(tmp_path)
    index = StoryIdentityIndex(tmp_path, window_weeks=2, max_df=2)
    index.rebuild()
    match = index.match(OTHER_OPENAI_STORY, "OpenAI发布企业版Agent平台定价")
    assert match is None, "a distinct story sharing only corpus-common words must survive"


def test_window_excludes_old_weeks(tmp_path: Path) -> None:
    # The original sits in an old week; the current week only has neighbours.
    _write_article(tmp_path, "2026-08-W4", "aaaaaaaaaaaaaaaa", "拟人化叙事掩盖Agent安全真问题",
                   _ORIG_URL, ORIGINAL_CARD)
    _write_article(tmp_path, WEEK, "9f8e7d6c5b4a3928", "Claude越权复盘：作弊环境致失配",
                   "https://aihot.virxact.com/items/anthropic", NEIGHBOUR_ANTHROPIC)
    index = StoryIdentityIndex(tmp_path, window_weeks=1, max_df=2)
    index.rebuild()
    assert {art.week for art in index.articles} == {WEEK}
    assert index.match(DIGEST_CARD, "拟人化叙事掩盖OpenAI真实安全漏洞") is None


def test_thin_card_never_suppresses(tmp_path: Path) -> None:
    _incidental_vault(tmp_path)
    service = StoryDedupService(tmp_path)
    assert service.check(url="https://x.example/y", title="短", brief_markdown="太短") is None


# ---------------------------------------------------------------------------
# Evidence tiers (precision): calibrated on the 2026-08/09 vault false
# positives — same newsletter voice, same tech theme, shared benchmark names
# must NOT merge two different stories.
# ---------------------------------------------------------------------------

def _match_in_vault(root: Path, week: str, existing: tuple[str, str], candidate: tuple[str, str]):
    _write_article(root, week, "b1c2d3e4f5a67890", existing[0], "https://orig.example/a", existing[1])
    index = StoryIdentityIndex(root, window_weeks=2, max_df=2)
    index.rebuild()
    return index.match(candidate[1], candidate[0])


def test_same_theme_different_story_is_not_duplicate(tmp_path: Path) -> None:
    """The charonhub false positive: two model posts sharing 推理速度/端到端/精度
    vocabulary (containment and rare count pass, but zero strong evidence)."""
    match = _match_in_vault(
        tmp_path, WEEK,
        ("推理速度成为选型第三指标",
         "推理速度成为选型第三指标\n\n端到端延迟与推理速度正在改写采购决策：\n\n"
         "## 关键事实\n- 自研芯片把端到端延迟压到交互阈值内，精度损失可控。\n"
         "- 新旗舰每百万 token 计费下调，批处理吞吐翻倍。\n\n"
         "## 信号\n- 采购谈判从效果基准转向延迟预算。"),
        ("开源阵营把推理速度打成第三变量",
         "开源阵营把推理速度打成第三变量\n\n评测机构首次把延迟纳入加权：\n\n"
         "## 关键事实\n- 某开源团队用推理速度换精度，在边缘设备上追平闭源。\n"
         "- 社区围绕其推理引擎fork出三条优化分支。\n\n"
         "## 信号\n- 开源冲击闭源定价的抓手从效果转向速度。"),
    )
    assert match is None, "same theme vocabulary without entities must not merge"


def test_single_entity_with_unrelated_titles_is_not_enough(tmp_path: Path) -> None:
    """The Debian false positive: a vote news item vs a trend analysis sharing
    'debian' as their only strong token."""
    match = _match_in_vault(
        tmp_path, WEEK,
        ("Debian投票放行生成式AI使用",
         "Debian投票放行生成式AI使用\n\nDebian 项目投票放行生成式 AI 代码审查：\n\n"
         "## 关键事实\n- 维护者投票通过，AI 辅助贡献需码审与署名。\n"
         "- 投票结果保留了项目对码审纪律的最终裁量。\n\n"
         "## 信号\n- 老牌社区为 AI 贡献开出正式通道。"),
        ("开源项目为何集体封杀AI贡献",
         "开源项目为何集体封杀AI贡献\n\n越来越多的项目拒绝 AI 生成贡献：\n\n"
         "## 关键事实\n- 码审负担与许可争议是主要理由，维护者不堪重负。\n"
         "- Debian 投票放行是少数例外，多数项目选择一刀切。\n\n"
         "## 信号\n- AI 贡献的治理规则正在分化。"),
    )
    assert match is None, "one shared entity name between two different stories is not identity"


def test_two_digit_numbers_are_not_strong_evidence(tmp_path: Path) -> None:
    """The DeepSeek/GLM false positive: two release posts both citing the
    CyberGym benchmark and the score '53' — short numbers collide by chance."""
    match = _match_in_vault(
        tmp_path, WEEK,
        ("DeepSeek开源跑分脚手架",
         "DeepSeek开源跑分脚手架\n\n跑分是模型与脚手架的联合产物：\n\n"
         "## 关键事实\n- 智能指数 53 分，CyberGym 从 52.7% 升至 83.3%。\n"
         "- 基准脚手架以 MIT 协议开源，跑分首次可复现。\n\n"
         "## 信号\n- 脚手架成为发布物的一部分。"),
        ("GLM-5.3微调超越安全红线",
         "GLM-5.3微调超越安全红线\n\n仅靠微调即追平上一代旗舰：\n\n"
         "## 关键事实\n- 拿下 CyberGym 最高分，智能指数 53 分追平闭源。\n"
         "- 因网络攻防能力过强，权重推迟两周发布。\n\n"
         "## 信号\n- 微调路线的安全边界收紧。"),
    )
    assert match is None, "benchmark name + two-digit score must not merge two releases"


def test_one_strong_token_with_title_corroboration_merges(tmp_path: Path) -> None:
    """The 混元 Hy4 true positive: one strong token (hy4) plus title overlap."""
    match = _match_in_vault(
        tmp_path, WEEK,
        ("混元Hy4开源：770B上下文",
         "混元Hy4开源：770B上下文\n\n腾讯开源混合专家架构模型：\n\n"
         "## 关键事实\n- 腾讯开源混元 Hy4 预览版，总参数 770B。\n"
         "- 上下文窗口 1M token，权重分批放出。\n\n"
         "## 信号\n- 大厂自研模型全面转向开放权重。"),
        ("腾讯开源混元Hy4预览版",
         "腾讯开源混元Hy4预览版\n\n腾讯混元系列开放权重：\n\n"
         "## 关键事实\n- 腾讯混元 Hy4 预览版上线，总参数 770B。\n"
         "- 支持 1M 上下文，商用许可宽松。\n\n"
         "## 信号\n- 预览版策略降低开源试水门槛。"),
    )
    assert match is not None, "hy4 + matching titles is the same story"
    assert "hy4" in match.shared_rare


def test_entity_evidence_carries_thin_containment(tmp_path: Path) -> None:
    """The embargo true positive: an original and an independent writeup agree
    on three named entities while their running text mostly differs (containment
    below the mass floor 0.15). Shared entities are the evidence."""
    match = _match_in_vault(
        tmp_path, WEEK,
        ("流言即漏洞：一次embargo事故复盘",
         "流言即漏洞：一次embargo事故复盘\n\n安全圈流传的rumour本身成为攻击面：\n\n"
         "## 关键事实\n- madhavapeddy 团队的 ocaml 项目在披露窗口期被社工利用。\n"
         "- 攻击者只凭会议走廊讨论就推断了未公开补丁的位置。\n"
         "- 协调披露流程随后改为分批解锁与密信标记。\n\n"
         "## 信号\n- 披露纪律从默契约定走向工程化。"),
        ("十分钟风声变十分钟攻击窗口",
         "十分钟风声变十分钟攻击窗口\n\n漏洞消息的传播速度超过补丁分发：\n\n"
         "## 关键事实\n- 研究者复现了从风声到在野利用的完整链路，embargo 是起点。\n"
         "- madhavapeddy 建议把 ocaml 生态的披露窗口压缩到小时级。\n"
         "- 多家发行商开始要求提交者预注册披露时间线。\n\n"
         "## 信号\n- 披露流程成为攻击面本身。"),
    )
    assert match is not None, "three shared named entities identify the story"
    assert {"embargo", "madhavapeddy", "ocaml"} <= set(match.shared_rare)


def test_cjk_evidence_mass_merges_translation_pairs(tmp_path: Path) -> None:
    """Chinese-original duplicates with no latin entities still merge: the
    mass path (>= 12 shared rare tokens) carries them."""
    shared_vocab = "岚图科技发布雾计算编译器，林川称量产能效比翻倍，覆盖车规级场景与边缘盒子。"
    match = _match_in_vault(
        tmp_path, WEEK,
        ("岚图科技发布雾计算编译器",
         f"岚图科技发布雾计算编译器\n\n{shared_vocab}\n\n"
          "## 关键事实\n- 岚图科技雾计算编译器完成车规级认证，林川主持发布。\n"
          "- 量产能效比翻倍，边缘盒子首批出货。\n\n"
          "## 信号\n- 雾计算从论文走向量产。"),
        ("岚图雾计算编译器走向量产",
         f"岚图雾计算编译器走向量产\n\n{shared_vocab}\n\n"
          "## 关键事实\n- 岚图科技发布的雾计算编译器通过车规级认证，林川给出量产时间表。\n"
          "- 能效比翻倍，边缘盒子与车规级项目同步落地。\n\n"
          "## 信号\n- 编译器国产化进入量产周期。"),
    )
    assert match is not None, "a paraphrase sharing 12+ rare tokens is the same story"
    assert len(match.shared_rare) >= 12


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------

class _AbsorptionByURL:
    """Stub provider returning the incident pair's cards, keyed by URL."""

    model_route = "test://story-identity"

    def __init__(self, mapping: dict[str, tuple[str, str]]) -> None:
        self.mapping = mapping
        self.score_calls = 0

    def score(self, content, prompt: str) -> str:  # noqa: ARG002
        self.score_calls += 1
        title, brief = self.mapping[content.url]
        return json.dumps(
            {
                "information_gain": 0.65, "action_value": 0.5, "relevance": 0.95,
                "is_spam": False, "rationale": "r",
                "title": title, "one_line_summary": "一句话",
                "category": "现象/趋势", "experiences": ["e"], "signals": ["s"],
                "key_facts": ["f"], "quote": "", "next_action": "n",
                "obsidian_brief_markdown": brief,
            },
            ensure_ascii=False,
        )


_ORIG_URL = "https://garymarcus.substack.com/p/dwarkesh-patelss-wildly-popular-but"
_DIGEST_URL = "https://aihot.virxact.com/items/cmthe8mr70bc5rodmmoqydd63"


def _story_pipeline(tmp_path: Path) -> tuple[Pipeline, _AbsorptionByURL, QueueStore]:
    orig_fixture = tmp_path / "orig.md"
    orig_fixture.write_text("# Original\n\n" + "Gary Marcus on Dwarkesh. " * 30, encoding="utf-8")
    digest_fixture = tmp_path / "digest.md"
    digest_fixture.write_text("# Digest\n\n" + "AI 导读中文摘要正文内容。 " * 30, encoding="utf-8")

    from knowledge_extractor_v3.fetchers.fixture import FixtureFetcher
    from knowledge_extractor_v3.outputs.live_obsidian import LiveObsidianWriter, LiveOutputPort

    fetcher = FixtureFetcher(fixture_map={
        _ORIG_URL: orig_fixture,
        _DIGEST_URL: digest_fixture,
    })
    provider = _AbsorptionByURL({
        _ORIG_URL: ("拟人化叙事掩盖Agent安全真问题", ORIGINAL_CARD),
        _DIGEST_URL: ("拟人化叙事掩盖OpenAI真实安全漏洞", DIGEST_CARD),
    })
    vault_root = tmp_path / "vault"
    store = QueueStore(tmp_path / "queue.db", runtime_fingerprint="test-fp")
    writer = LiveOutputPort(
        obsidian_writer=LiveObsidianWriter(vault_root, write_manifest=True),
    )
    pipeline = Pipeline(
        store,
        fetcher=fetcher,
        llm_provider=provider,
        live_output=writer,
        staging_root=tmp_path / "staging",
        allow_test_provider=True,
        story_dedup=StoryDedupService(vault_root),
    )
    return pipeline, provider, store


def test_second_transport_completes_at_canonical_without_second_file(tmp_path: Path) -> None:
    pipeline, provider, store = _story_pipeline(tmp_path)

    first = pipeline.process_url(_ORIG_URL, source="rss", mode=RuntimeMode.LIVE)
    assert first.final_status.value == "done"
    assert first.dedup_outcome == ""
    assert provider.score_calls == 1

    second = pipeline.process_url(_DIGEST_URL, source="rss", mode=RuntimeMode.LIVE)
    assert second.dedup_outcome == "duplicate_story"
    assert second.final_status.value == "done"
    assert second.output_path == first.output_path  # the canonical, not a fork
    assert provider.score_calls == 2                 # gate sits post-absorption

    md_files = list((tmp_path / "vault").glob("????-??-W?/*.md"))
    assert len(md_files) == 1

    # The suppression is auditable in the canonical week's manifest.
    manifest = (tmp_path / "vault" / WEEK / "manifest.jsonl").read_text(encoding="utf-8")
    assert "story_duplicate_suppressed" in manifest
    assert _DIGEST_URL in manifest


def test_story_gate_respects_disable_and_keeps_both_when_disabled(tmp_path: Path) -> None:
    pipeline, provider, _ = _story_pipeline(tmp_path)
    pipeline.story_dedup = None  # operator turns the gate off
    pipeline.process_url(_ORIG_URL, source="rss", mode=RuntimeMode.LIVE)
    second = pipeline.process_url(_DIGEST_URL, source="rss", mode=RuntimeMode.LIVE)
    assert second.dedup_outcome == ""
    assert len(list((tmp_path / "vault").glob("????-??-W?/*.md"))) == 2


# ---------------------------------------------------------------------------
# URL transport normalization
# ---------------------------------------------------------------------------

def test_normalize_url_strips_tracking_parameters() -> None:
    assert (
        normalize_url("https://martinalderson.com/posts/codebase-cognitive-debt-quizzes/?utm_source=rss&utm_medium=rss&utm_campaign=feed")
        == "https://martinalderson.com/posts/codebase-cognitive-debt-quizzes"
    )
    assert normalize_url("https://a.example/x?b=2&a=1") == "https://a.example/x?a=1&b=2"
    assert normalize_url("https://b.example/y?gclid=abc") == "https://b.example/y"
    assert normalize_url("https://c.example/z#frag") == "https://c.example/z"


def test_queue_collapses_tracking_variants_into_one_row(tmp_path: Path) -> None:
    store = QueueStore(tmp_path / "queue.db", runtime_fingerprint="test-fp")
    tracked = store.enqueue("https://example.com/post?utm_source=rss&utm_medium=feed")
    clean = store.enqueue("https://example.com/post")
    assert tracked.url == "https://example.com/post"
    assert clean.id == tracked.id  # one queue row, not two tasks


# ---------------------------------------------------------------------------
# Vault reconciliation (self-healing)
# ---------------------------------------------------------------------------

def _week_with_existing_duplicate(root: Path) -> tuple[Path, Path]:
    """The pre-deploy state: the incident pair, both already archived."""
    canonical = _write_article(root, WEEK, "a1b2c3d4e5f6g7h8", "拟人化叙事掩盖Agent安全真问题",
                               _ORIG_URL, ORIGINAL_CARD)
    loser = _write_article(root, WEEK, "84e02ae1845bc4bf", "拟人化叙事掩盖OpenAI真实安全漏洞",
                           _DIGEST_URL, DIGEST_CARD)
    _write_article(root, WEEK, "dc1bbdd3ea2e4649", "Claude越权复盘：作弊环境致失配",
                   "https://aihot.virxact.com/items/claude", NEIGHBOUR_ANTHROPIC)
    # The reader already marked the loser as read with a comment.
    state_path = root / WEEK / "阅读状态 2026-09-W1.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "week": WEEK,
        "articles": {
            "84e02ae1845bc4bf": {
                "article_id": "84e02ae1845bc4bf",
                "week": WEEK,
                "path": loser.name,
                "added_on": "2026-09-01",
                "read_at": "2026-09-01T08:00:00+08:00",
                "disposition": "read",
                "comment": "拟人化批评有道理",
                "annotations": [],
                "updates": [],
                "update_pending": False,
                "review": {"revision": 0, "status": "idle", "result": "", "error": ""},
            }
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return canonical, loser


def test_reconciliation_merges_incident_pair(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    canonical, loser = _week_with_existing_duplicate(root)

    report = dedupe_vault(root)
    assert report.story_groups == 1
    assert not loser.exists()
    assert canonical.exists()
    trashed = list((root / WEEK).glob(".trash-dedup/story/*.md"))
    assert len(trashed) == 1 and trashed[0].name == loser.name

    # Reading state re-keyed onto the canonical id, comment preserved.
    state = json.loads((root / WEEK / "阅读状态 2026-09-W1.json").read_text(encoding="utf-8"))
    entries = state["articles"]
    assert "84e02ae1845bc4bf" not in entries
    migrated = entries["a1b2c3d4e5f6g7h8"]
    assert migrated["comment"] == "拟人化批评有道理"
    assert migrated["read_at"] == "2026-09-01T08:00:00+08:00"

    # The merge is auditable.
    manifest = (root / WEEK / "manifest.jsonl").read_text(encoding="utf-8")
    assert "story_merged" in manifest and _DIGEST_URL in manifest


def test_restore_orphans_does_not_resurrect_story_losers(tmp_path: Path) -> None:
    from knowledge_extractor_v3.outputs.dedupe import restore_orphans

    root = tmp_path / "vault"
    _canonical, loser = _week_with_existing_duplicate(root)
    dedupe_vault(root)
    assert not loser.exists()
    assert restore_orphans(root) == []  # single-level glob must not see story losers
    assert not loser.exists()


def test_restore_orphans_skips_stale_byid_siblings_of_story_losers(tmp_path: Path) -> None:
    """Real-vault hazard (2026-09-01): an EARLIER by-id merge left a trash
    entry for the loser's article_id. After the story merge removes the last
    live copy of that id, restore_orphans must not resurrect that stale
    sibling — it is the same duplicate the story pass just merged."""
    from knowledge_extractor_v3.outputs.dedupe import restore_orphans

    root = tmp_path / "vault"
    _canonical, loser = _week_with_existing_duplicate(root)
    # A stale by-id trash sibling carrying the loser's article_id.
    byid_trash = root / WEEK / ".trash-dedup"
    byid_trash.mkdir(parents=True, exist_ok=True)
    stale = byid_trash / "2026-08-29 旧标题副本 84e02ae1845bc4bf.md"
    stale.write_text(
        "---\n"
        'type: "knowledge-extract"\n'
        'article_id: "84e02ae1845bc4bf"\n'
        'title: "旧标题副本"\n'
        "---\n旧内容。\n",
        encoding="utf-8",
    )
    dedupe_vault(root)
    assert not loser.exists()
    assert restore_orphans(root) == []
    assert stale.exists()  # stays dead, not restored to the week


def test_reconciliation_respects_cross_week_and_dry_run(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    _write_article(root, "2026-08-W5", "a1b2c3d4e5f6g7h8", "拟人化叙事掩盖Agent安全真问题",
                   _ORIG_URL, ORIGINAL_CARD)
    loser = _write_article(root, "2026-09-W1", "84e02ae1845bc4bf", "拟人化叙事掩盖OpenAI真实安全漏洞",
                           _DIGEST_URL, DIGEST_CARD)

    dry = dedupe_vault(root, dry_run=True)
    assert dry.story_groups == 1
    assert loser.exists()  # dry run touches nothing

    applied = dedupe_vault(root)
    assert applied.story_groups == 1
    assert not loser.exists()
    assert (root / "2026-08-W5" / "2026-09-01 拟人化叙事掩盖Agent安全真问题 a1b2c3d4.md").exists()


def test_story_pass_disabled_leaves_vault_untouched(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    _canonical, loser = _week_with_existing_duplicate(root)
    report = dedupe_vault(root, story=False)
    assert report.story_groups == 0
    assert loser.exists()
