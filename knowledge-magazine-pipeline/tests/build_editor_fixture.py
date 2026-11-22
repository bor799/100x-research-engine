#!/usr/bin/env python3
"""首期编辑层装配：正文判断为编辑产出；所有 URL/ID 从 collect 取，杜绝手误。"""
import json
import sys
from pathlib import Path

co = json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else "state/collect-2026-11-19.json").read_text())

W = {}  # wire href -> item
for s in co["wire"]["sections"]:
    for it in s["items"]:
        W[it["href"]] = it
OB = {n["article_id"]: n for n in co["obsidian_week"]["notes"]}          # 周目录笔记（含未进 ISSUE 的新笔记）
WK = {a["article_id"]: a for a in co["weekly"]["articles"]}              # 周刊 ISSUE

def hn(i):  # HN 链接
    return [h for h in W if h.endswith("id=" + i)][0]

def ob(aid, label):
    n = OB.get(aid) or WK.get(aid)
    return {"url": n["obsidian_url"] if "obsidian_url" in n else n.get("obsidian_url"), "label": label}

def srcobs(aid, label):
    n = OB.get(aid) or WK.get(aid)
    return {"url": n["url"], "label": label}

ed = {
  "schema": "knowledge-magazine-editor/1",
  "material_date": co["material_date"],
  "week": co["week"],
  "model": "glm-5.3",
  "today": {
    "verdict": "Anthropic 的游说账本被逐笔翻出：LDA 披露显示合计 $1.63M 付给两家特朗普关联游说公司，其中 Rubio 前幕僚长的 Continental Strategy 占 $740k（六个季度累计），『监管捕获』从论坛指控落到可查底账；同一天，一项 1,065 人随机对照实验（原始论文未直接核读，多家媒体转述口径一致）测得 AI 摘要让外部网站点击少约四成——模型公司的政治资本与分发挤压，同时加速。",
    "picks": [
      {
        "title": "LDA 披露：Anthropic 合计 $1.63M 付两家特朗普关联游说公司，其中 Continental Strategy（Rubio 前幕僚长）$740k、六个季度累计，申报游说对象含 NSC",
        "why": "主证据是 LDA 游说披露底账（我们未逐条直读原始 filings，依据多家独立媒体的相互一致转述）。口径要分清：$1.63M 是两家公司的合计，$740k 是其中 Continental 一家的累计，不是两笔追加。争议在 Rubio 现在分管 NSC，而其前幕僚长的公司正替 Anthropic 游说 NSC；『监管捕获』仍是论坛定性，filings 本身不下这个结论。",
        "kind": "fact",
        "status": "single_source",
        "sources": [
          {"url": hn("50536565"), "label": "HN·$740k", "primary_key": "lda-disclosures"},
          {"url": hn("50539834"), "label": "HN·$1.63M（合计口径）", "primary_key": "lda-disclosures"}
        ]
      },
      {
        "title": "随机对照实验测得：Google AI 摘要让外部网站点击少约 40%（原始论文未直接核读）",
        "why": "主证据是 CMU 与印度商学院的 1,065 人随机对照实验（隐藏 AI 摘要组作对照）；Search Engine Journal 与 PPC Land 两家媒体的转述相互一致，但这只能印证『该研究存在且数字口径如此』，不能替代对研究本身的独立复核。对内容与流量生意，这是迄今最硬的一档因果证据；口径仅美国桌面用户。",
        "kind": "fact",
        "status": "single_source",
        "sources": [
          srcobs("4ba3b2fc75486d6b", "萃取笔记·汇总（转述）"),
          {"url": OB["4ba3b2fc75486d6b"]["obsidian_url"], "label": "Obsidian 原文"}
        ]
      },
      {
        "title": "作者实验报告称：Claude 训练时大量见过蛋白质序列（GPT/Gemini 无此迹象）",
        "why": "作者用真实序列对打乱序列的准确率差做对照，报告 Claude 两代模型在无人写过的冷门蛋白上仍能预测下一个氨基酸。实验数据是其报告内事实，『训练语料含蛋白序列』是作者的推断性结论，尚无第三方复现——按作者报告的观察对待，不当作已证实的实证。",
        "kind": "fact",
        "status": "single_source",
        "sources": [
          srcobs("c0416b36c82aed57", "原作者实验"),
          {"url": OB["c0416b36c82aed57"]["obsidian_url"] if "c0416b36c82aed57" in OB else WK["c0416b36c82aed57"]["obsidian_url"], "label": "Obsidian 原文"}
        ]
      }
    ],
    "groups": [
      {"title": "Anthropic 的游说账本", "thread": "『监管捕获』指控的后面，是一笔笔可查的公开披露", "hrefs": [
        hn("50530450"), hn("50536565"), hn("50539834"), hn("50529644")]},
      {"title": "安全与攻防", "thread": "漏洞披露时限收紧、网络安全访问公开信与社区疑云同日出现", "hrefs": [
        hn("50540024"), hn("50518205"), hn("50538449")]},
      {"title": "OpenAI 的诉讼在滚大", "thread": "加州起诉书点名 80 起『失控』事件，单日新增八条相关帖", "hrefs": [
        hn("50525999")]},
      {"title": "Agent 工程化", "thread": "瓶颈从写代码挪到审代码，工具开始伺候 agent 的高产", "hrefs": [
        hn("50536468"), hn("50538303"), hn("50537739"), hn("50531481")]},
      {"title": "芯片与算力格局", "thread": "黄仁勋风波余波未平，Trainium 开源推理引擎给出非 N 卡选项", "hrefs": [
        hn("50534745"), hn("50533270")]}
    ],
    "item_notes": {
      hn("50530450"): {"kind": "opinion", "note": "『监管捕获』是发帖人定性；METR 接受 Anthropic 资助是事实，因果是推断。"},
      hn("50536565"): {"kind": "fact", "note": "$740k 为 2025 Q2 起 six quarters 累计，非单笔；filings 披露职务写法合法合规。"},
      hn("50539834"): {"kind": "fact", "note": "口径更正：$1.63M 为两家公司合计（含 Continental 的 $740k），HN 标题的『第二笔』与合计口径并存，以 LDA 原始披露为准，待核。"},
      hn("50538303"): {"kind": "fact", "note": "哈佛研究：代码生成不再稀缺，code review 成新关口——与 StarSling 属同一信号。"}
    }
  },
  "week_view": {
    "what_grew": "本周新增判断集中在三条线：一是治理——Anthropic 游说披露被翻出（$1.63M 合计、Continental 占 $740k）、OpenAI 被加州点名 80 起『失控』事件，模型公司的政治面首次压过产品面；二是分发的钱——一项随机对照实验测得 AI 摘要吞约四成点击（原始论文未直接核读），流量生意需要重排；三是工程——瓶颈挪到 code review 的证据增多，agent 基建（验证层、外部沙箱、自我改进 CI）一周内三篇独立材料指向同一处。相对上周 149 篇，本周萃取新进 6 篇 A 级。",
    "topics": [
      {
        "slug": "gpu-saga-and-chips",
        "title": "GPU 风波与芯片格局",
        "thread": "从『循环融资撑价』到黄仁勋辞职，再到非 N 卡推理选项浮出",
        "developments": [
          {"date": "2026-11-15", "event": "Nvidia 被指用循环融资养买家、把 GPU 稀缺做成既成事实；同日 Claude Code 宣布并行 worker。", "sources": [srcobs("6b1c206d5ea56ca3", "TechSpot·循环融资"), {"url": OB["6b1c206d5ea56ca3"]["obsidian_url"], "label": "Obsidian 原文"}]},
          {"date": "2026-11-16", "event": "火从 Nvidia 烧到黄仁勋本人；Anthropic IPO 文件被通读，市场把 11 月估值增长押给 Anthropic（69%）。", "sources": []},
          {"date": "2026-11-17", "event": "英伟达宣布黄仁勋即刻卸任 CEO；同周 Hydrogen 发布。", "sources": [srcobs("042d3d4c94d8f4fa", "AI 周报·Hydrogen 与辞职")]},
          {"date": "2026-11-18", "event": "风波续帖：黄仁勋曾建开放生态、威胁到 Anthropic；TrnRT 给 AWS Trainium 出开源推理引擎，非 N 卡推理多了实选项。", "sources": [{"url": hn("50534745"), "label": "HN·开放生态"}, {"url": hn("50533270"), "label": "HN·TrnRT"}]}
        ],
        "counterpoints": [
          "『循环融资』与『开放生态威胁 Anthropic』均出自论坛帖与单一报道，公司未回应全链条指控；把两件事拼成一条叙事线是编辑推断。"
        ],
        "to_verify": [
          "黄仁勋辞职的官方文件与接任安排（公司公告原文尚未入信源）",
          "TrnRT 在真实负载下与 GPU 推理的成本对比（Show HN 自述数据待第三方测）"
        ],
        "deep_read_ids": []
      },
      {
        "slug": "anthropic-governance-pressure",
        "title": "Anthropic 的治理压力",
        "thread": "游说披露被逐笔翻出，『安全公司』人设与华盛顿账本同周对撞",
        "developments": [
          {"date": "2026-11-17", "event": "IPO 估值盘继续向低档搬运：不足 $1.25T 档升到 37%，市场情绪与舆论同步降温。", "sources": []},
          {"date": "2026-11-18", "event": "METR『监管捕获』帖冲 138 分；LDA 披露被翻出：合计 $1.63M 付两家特朗普关联公司（其中 Continental Strategy $740k、六个季度累计，申报游说对象含 NSC）；另有公开信要求开放网络安全访问、漏洞披露时限被缩短。", "sources": [{"url": hn("50530450"), "label": "HN·METR"}, {"url": hn("50536565"), "label": "HN·$740k"}, {"url": hn("50539834"), "label": "HN·$1.63M"}, {"url": hn("50540024"), "label": "HN·公开信"}]}
        ],
        "counterpoints": [
          "LDA 披露里『Rubio 前参院幕僚长』是法定写法（申报曾任职位的固定格式），filings 本身不构成利益输送结论；『监管捕获』是 HN 帖定性。",
          "Cross-check 实抓：多家独立媒体报道口径一致（$740k 为累计六季度），但均未指出任何违规。"
        ],
        "to_verify": [
          "METR 资助金额与占比的原始披露（目前只有论坛帖与二手转述）"
        ],
        "deep_read_ids": []
      },
      {
        "slug": "ai-eats-distribution",
        "title": "AI 吞噬分发的证据线",
        "thread": "从相关性研究到随机实验：四成点击的因果坐实",
        "developments": [
          {"date": "2026-11-18", "event": "CMU/印度商学院 1,065 人随机对照实验测得 AI Overviews 使有机点击约 -40%、零点击 +34.5%（原始论文未直接核读，SEJ/PPC Land 两家转述一致）；Ahrefs 400 万被引 URL 研究把『76% 引用来自前 10』修正为 38%（另一项独立研究、口径不同）。", "sources": [srcobs("4ba3b2fc75486d6b", "revscope 汇总"), {"url": OB["4ba3b2fc75486d6b"]["obsidian_url"], "label": "Obsidian 原文"}]},
          {"date": "2026-11-18", "event": "另一端：AI 助手给网站导流仍只有 0.28%（Ahrefs 74,752 站点，3 月口径）——被吞掉的点击没有变成新入口。", "sources": [srcobs("4ba3b2fc75486d6b", "同文数据")]}
        ],
        "counterpoints": [
          "38% / 39.8% /『约40%』三个数字分属不同口径（结果页位置是否全计入、是否美国桌面样本）；引用任一数字时须带口径。"
        ],
        "to_verify": [
          "GEO（生成引擎优化）能否补回缺口：现只有厂商侧证据，缺独立复现"
        ],
        "deep_read_ids": ["c0416b36c82aed57"]
      },
      {
        "slug": "model-commodization",
        "title": "模型商品化与价格战",
        "thread": "GPT-6.2 降价两成仍不敌 Fable；推理侧拼每兆瓦吞吐",
        "developments": [
          {"date": "2026-11-18", "event": "Simon Willison 测价：GPT-6.2 Astra 输入输出降约 20%，性价比仍落后 Fable；同日 Cerebras 宣称分离推理把每兆瓦吞吐做到 5 倍（厂商自测）。", "sources": [srcobs("7e49e20b3ca6c077", "simonwillison.net"), {"url": OB["486020f41bc19e58"]["url"], "label": "Cerebras 官宣"}]},
          {"date": "2026-11-18", "event": "预测市场同步：Coding Arena 1580 分档月涨 33% 至 52%；11 月底中国最强模型盘小米档回落、阿里档回升。", "sources": [{"url": [r["href"] for r in co["wire"]["market_rows"] if "coding-" in r["href"]][0], "label": "Polymarket·Coding Arena"}]}
        ],
        "counterpoints": [
          "Cerebras ×5 为厂商自测口径，无第三方复现；『不敌 Fable』是个人基准 + 价格的合成判断，随定价变动会过期。"
        ],
        "to_verify": [
          "Fable 与 GPT-6.2 在长任务上的稳定性差距（现只有短基准对比）"
        ],
        "deep_read_ids": ["7e49e20b3ca6c077", "bd6d99290b860a81"]
      }
    ]
  },
  "cross_checks": [
    {
      "claim": "AI Overviews 让外部网站有机点击约 -40%",
      "status": "single_source",
      "primary_source": "CMU+印度商学院 1,065 人随机对照实验（原始论文未直接核读）",
      "verdict": "主证据是一项随机对照实验（对照组隐藏 AI 摘要，Chrome 扩展分流 1,065 名美国桌面用户）。Search Engine Journal 与 PPC Land 两家独立媒体的转述相互一致（39.8%/38%，差异在是否计入结果页全部位置）——转述只能印证『该研究存在且数字口径如此』，不能替代对研究本身的独立复核，故不计为两票。",
      "counterpoint": "样本限于自愿装扩展的美国桌面用户，移动端与美国以外未覆盖；38%/39.8%/『约40%』三个数字分属不同口径，引用须带口径；Ahrefs 的 38% 是另一项相关性研究的修正值，只能印证『AI 引用集中于 Google 靠前结果』这一层，不能印证点击降幅。",
      "sources": [
        {"url": OB["4ba3b2fc75486d6b"]["obsidian_url"], "label": "Obsidian·萃取笔记", "primary_key": "cmu-isb-experiment"},
        {"url": OB["4ba3b2fc75486d6b"]["url"], "label": "revscope 汇总（转述）", "primary_key": "cmu-isb-experiment"},
        {"url": "https://www.searchenginejournal.com/ai-overviews-cut-organic-clicks-38-field-study-finds/573145", "label": "Search Engine Journal（实抓转述）", "independent": True, "fetched": True, "primary_key": "cmu-isb-experiment"},
        {"url": "https://ppc.land/researchers-find-google-ai-overviews-cut-publisher-clicks-39-8", "label": "PPC Land（实抓转述，方法细节）", "independent": True, "fetched": True, "primary_key": "cmu-isb-experiment"}
      ]
    },
    {
      "claim": "Anthropic 合计 $1.63M 付两家特朗普关联游说公司；其中 Continental Strategy（Rubio 前参院幕僚长）$740k、六个季度累计，申报游说对象含 NSC",
      "status": "single_source",
      "primary_source": "美国参议院 LDA 游说披露底账（经多家媒体转述核对，未逐条直读原始 filings）",
      "verdict": "主证据是同一套 LDA 披露底账：IndiaTimes、magica、Crypto Briefing 等多家独立媒体转述相互一致——$1.63M 为付给 Continental Strategy 与 Ballard Partners 两家的合计，$740k 是 Continental 一家的六季度累计（含在合计内，不是另一笔追加）。多家转述同源只算一票，故不计 verified_multi。",
      "counterpoint": "『监管捕获』是 HN 帖定性；LDA 披露里『Rubio 前参院幕僚长』是法定申报格式，filings 本身不构成利益输送或违规结论；『同日曝光』指被媒体集中翻出，不是新发生的付款。合计与分项的准确拆分待直读 LDA 原始 filings 后再升格。",
      "sources": [
        {"url": hn("50536565"), "label": "HN·$740k", "primary_key": "lda-disclosures"},
        {"url": hn("50539834"), "label": "HN·$1.63M（合计口径）", "primary_key": "lda-disclosures"},
        {"url": "https://cryptobriefing.com/anthropic-lobbying-firm-linked-to-rubio", "label": "Crypto Briefing（实抓，合计/分项口径）", "independent": True, "fetched": True, "primary_key": "lda-disclosures"},
        {"url": "https://magica.com/news/anthropic-continental-strategy-rubio-aide-lobbying-nsc", "label": "magica（实抓，filings 逐条核对转述）", "independent": True, "fetched": True, "primary_key": "lda-disclosures"}
      ]
    },
    {
      "claim": "作者实验报告称 Claude 训练语料含大量蛋白质序列（GPT/Gemini 无此迹象）",
      "status": "single_source",
      "primary_source": "作者博客自建实验（唯一原始来源，无第三方复现）",
      "verdict": "668 个细菌/古菌蛋白 + 167 个 Pfam 未知家族（无结构解析、论文≤3 篇），真实序列对打乱序列的正确率差（作者报告 Fable 5.2：54.7% vs 8.8%）构成方法内对照；训练截止后首发的 56 个新蛋白仍可预测，排除『只背过论文』。以上均为作者报告内数据；『训练语料含蛋白序列』是作者推断，按观察对待。",
      "counterpoint": "训练截止日是假定的 2026-07-01；『无 50% 同源近亲』的筛查若漏掉远源泛化，新蛋白仍可预测就不必然意味着背过序列库；GPT/Gemini『无迹象』只说明该测法下测不出，不等于没训过。",
      "sources": [
        {"url": OB["c0416b36c82aed57"]["obsidian_url"] if "c0416b36c82aed57" in OB else WK["c0416b36c82aed57"]["obsidian_url"], "label": "Obsidian·萃取笔记", "primary_key": "author-experiment"},
        {"url": OB["c0416b36c82aed57"]["url"] if "c0416b36c82aed57" in OB else WK["c0416b36c82aed57"]["url"], "label": "原作者实验（唯一原始来源）", "primary_key": "author-experiment"}
      ]
    }
  ],
  "coverage_notes": [
    "本周萃取笔记中 4 篇（Google AI 摘要、Cerebras、英伟达循环融资、AI 周报）尚未进入周刊 ISSUE 数据，已直接从 Obsidian 周目录引用，待 17:20 日报重建后自动并入。"
  ]
}

out = Path("state/editor-2026-11-19.json")
out.write_text(json.dumps(ed, ensure_ascii=False, indent=1), encoding="utf-8")
print("written", out)
