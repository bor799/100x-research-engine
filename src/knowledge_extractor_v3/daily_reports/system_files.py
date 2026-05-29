"""Default files for the US AI market daily report system."""

from __future__ import annotations

from pathlib import Path


REQUIRED_SYSTEM_FILES = (
    "DAILY_LOOP_PROMPT.md",
    "WATCHLIST.yml",
    "SIGNAL_DEFINITIONS.md",
    "DAILY_REPORT_TEMPLATE.md",
    "DATA_SOURCES.yaml",
    "RUNBOOK_DAILY_REPORT.md",
    "SIGNALS.md",
    "DAILY_SIGNAL_LOG.md",
    "DECISION_NOTES.md",
    "LOOP_PROMPT_US_AI_DAILY_DEV.md",
)

ACTION_LABELS = (
    "继续强势",
    "高位震荡",
    "短线过热",
    "回踩支撑",
    "破位风险",
    "等财报催化",
    "利好兑现",
    "低位修复",
    "需要观察",
    "叙事未验证",
    "已验证但太拥挤",
    "有基本面验证，可加入重点池",
    "只看不买",
    "等回调",
    "可小仓跟踪",
    "需要重新估值",
)


def _action_label_markdown() -> str:
    return "\n".join(f"- {label}" for label in ACTION_LABELS)


DEFAULT_WATCHLIST_ITEMS = (
    ("INV-US-NVDA", "NVDA", "Nvidia", "核心候选", "GPU/HBM", "P1", "数据中心收入、Blackwell/Rubin 供给、毛利率、hyperscaler capex"),
    ("INV-US-MSFT", "MSFT", "Microsoft", "核心候选", "平台", "P1", "Copilot seat、Agent 365、Azure AI 增速、治理层定价"),
    ("INV-US-GOOGL", "GOOGL", "Google", "核心候选", "平台", "P1", "AI Mode 广告、Gemini 用户、Cloud AI 增速、反垄断进展"),
    ("INV-US-META", "META", "Meta", "观察", "平台", "P1", "AI 广告收入、CapEx、FCF、Llama 生态"),
    ("INV-US-AMZN", "AMZN", "Amazon", "观察", "平台", "P1", "AWS AI 收入、Trainium/Inferentia、CapEx 回报"),
    ("INV-US-ORCL", "ORCL", "Oracle", "观察", "平台", "P1", "OCI AI 基础设施收入、RPO 转收入、CapEx 压力"),
    ("INV-US-AMD", "AMD", "AMD", "瓶颈观察", "GPU/HBM", "P1", "MI 系列收入、GPU 份额、毛利率和供给"),
    ("INV-US-AVGO", "AVGO", "Broadcom", "瓶颈观察", "网络", "P1", "AI 网络芯片、定制 ASIC、客户集中度"),
    ("INV-US-MRVL", "MRVL", "Marvell Technology", "瓶颈观察", "网络", "P1", "AI 网络订单、定制 ASIC 设计胜出、数据中心收入"),
    ("INV-US-ARM", "ARM", "Arm", "观察", "端侧 AI", "P2", "端侧 AI 授权、云端 Arm CPU 采用、估值消化"),
    ("INV-US-TSM", "TSM", "TSMC", "瓶颈观察", "GPU/HBM", "P1", "先进制程、CoWoS 产能、AI 客户需求"),
    ("INV-US-ASML", "ASML", "ASML", "瓶颈观察", "GPU/HBM", "P2", "EUV 订单、先进制程资本开支"),
    ("INV-US-INTC", "INTC", "Intel", "证据不足", "GPU/HBM", "P2", "Foundry 客户、AI 加速器、现金流压力"),
    ("INV-US-QCOM", "QCOM", "Qualcomm", "观察", "端侧 AI", "P1", "端侧推理、手机/PC AI 芯片、汽车收入"),
    ("INV-US-MU", "MU", "Micron", "观察", "GPU/HBM", "P1", "HBM 供需、毛利率、客户满足率、周期反转迹象"),
    ("INV-US-WDC", "WDC", "Western Digital", "期权", "GPU/HBM", "P2", "存储周期、AI 数据增长、毛利率"),
    ("INV-US-STX", "STX", "Seagate", "期权", "GPU/HBM", "P2", "近线存储需求、AI 数据中心订单"),
    ("INV-US-ANET", "ANET", "Arista Networks", "瓶颈观察", "网络", "P1", "AI 网络交换机订单、云客户 capex"),
    ("INV-US-CRDO", "CRDO", "Credo Technology", "期权", "网络", "P2", "AEC 订单、AI 集群互连需求"),
    ("INV-US-ALAB", "ALAB", "Astera Labs", "期权", "网络", "P2", "PCIe/CXL 订单、客户集中度、估值"),
    ("INV-US-CIEN", "CIEN", "Ciena", "观察", "网络", "P2", "光网络需求、云客户订单"),
    ("INV-US-NOK", "NOK", "Nokia", "暂不研究", "网络", "P3", "数据中心网络业务是否形成实质收入"),
    ("INV-US-LITE", "LITE", "Lumentum", "瓶颈观察", "光通信", "P1", "InP 产能、OCS 订单、毛利率"),
    ("INV-US-COHR", "COHR", "Coherent", "瓶颈观察", "光通信", "P1", "光模块订单、毛利率、AI 客户需求"),
    ("INV-US-AAOI", "AAOI", "Applied Optoelectronics", "期权", "光通信", "P2", "800G/1.6T 光模块订单和盈利"),
    ("INV-US-TSEM", "TSEM", "Tower Semiconductor", "期权", "光通信", "P3", "硅光/模拟代工需求"),
    ("INV-US-SIVE", "SIVE", "Sivers Semiconductors", "证据不足", "光通信", "P3", "硅光订单和上市流动性"),
    ("INV-US-SMCI", "SMCI", "Super Micro Computer", "期权", "数据中心", "P2", "AI 服务器收入、审计/治理风险、毛利率"),
    ("INV-US-DELL", "DELL", "Dell Technologies", "观察", "数据中心", "P1", "AI 服务器 backlog、利润率、现金流"),
    ("INV-US-HPE", "HPE", "Hewlett Packard Enterprise", "观察", "数据中心", "P2", "AI 服务器订单、Juniper 整合"),
    ("INV-US-VRT", "VRT", "Vertiv", "瓶颈观察", "数据中心", "P1", "液冷/电力订单、backlog、毛利率"),
    ("INV-US-ETN", "ETN", "Eaton", "瓶颈观察", "电力", "P1", "电气设备订单、数据中心收入"),
    ("INV-US-PWR", "PWR", "Quanta Services", "观察", "电力", "P2", "电网工程 backlog、数据中心需求"),
    ("INV-US-GEV", "GEV", "GE Vernova", "观察", "电力", "P2", "电网/燃机订单、利润率"),
    ("INV-US-VST", "VST", "Vistra", "期权", "电力", "P1", "PPA、ERCOT 电价、AI 数据中心电力合同"),
    ("INV-US-CEG", "CEG", "Constellation Energy", "观察", "电力", "P1", "核电 PPA、数据中心合同、监管"),
    ("INV-US-NRG", "NRG", "NRG Energy", "期权", "电力", "P2", "电力合同、负荷增长、利润率"),
    ("INV-US-OKLO", "OKLO", "Oklo", "期权", "电力", "P2", "核电审批、商业化时间表、现金消耗"),
    ("INV-US-SMR", "SMR", "NuScale Power", "期权", "电力", "P2", "SMR 项目进展、现金流、客户合同"),
    ("INV-US-NEE", "NEE", "NextEra Energy", "观察", "电力", "P2", "可再生能源和数据中心电力合同"),
    ("INV-US-SO", "SO", "Southern Company", "观察", "电力", "P3", "电力需求、核电/燃气资产"),
    ("INV-US-DUK", "DUK", "Duke Energy", "观察", "电力", "P3", "数据中心负荷、电网投资"),
    ("INV-US-FLNC", "FLNC", "Fluence Energy", "期权", "电力", "P3", "储能订单、毛利率、现金流"),
    ("INV-US-DDOG", "DDOG", "Datadog", "观察", "软件", "P1", "AI 可观测性收入、RPO、客户扩展"),
    ("INV-US-NET", "NET", "Cloudflare", "观察", "软件", "P1", "Workers AI 收入、Dynamic Workers、盈利路径"),
    ("INV-US-MDB", "MDB", "MongoDB", "观察", "软件", "P1", "Atlas 增速、AI workload、毛利率"),
    ("INV-US-SNOW", "SNOW", "Snowflake", "观察", "软件", "P1", "Cortex AI 收入、consumption 趋势"),
    ("INV-US-PLTR", "PLTR", "Palantir", "观察", "软件", "P1", "AIP 收入、商业客户增速、估值"),
    ("INV-US-NOW", "NOW", "ServiceNow", "观察", "软件", "P1", "AI ACV、workflow agent 采用、seat 风险"),
    ("INV-US-CRM", "CRM", "Salesforce", "观察", "软件", "P1", "Agentforce、AWU、消耗计费转型"),
    ("INV-US-PANW", "PANW", "Palo Alto Networks", "观察", "软件", "P2", "AI 安全收入、平台化整合"),
    ("INV-US-CRWD", "CRWD", "CrowdStrike", "观察", "软件", "P2", "agent identity/security 产品和收入"),
    ("INV-US-TEAM", "TEAM", "Atlassian", "观察", "软件", "P1", "Rovo 货币化、Teamwork Graph、FCF"),
    ("INV-US-WDAY", "WDAY", "Workday", "观察", "软件", "P2", "HR/finance agent 采用、增长重加速"),
    ("INV-US-INTU", "INTU", "Intuit", "观察", "软件", "P2", "AI 财税工作流、ARPU、利润率"),
    ("INV-US-SHOP", "SHOP", "Shopify", "观察", "软件", "P2", "商家 AI 工具、GMV、take rate"),
    ("INV-US-AAPL", "AAPL", "Apple", "观察", "端侧 AI", "P1", "端侧 AI 功能、换机周期、服务收入"),
    ("INV-US-TSLA", "TSLA", "Tesla", "期权", "端侧 AI", "P2", "FSD/Robotaxi、Optimus、汽车利润率"),
    ("INV-US-APLD", "APLD", "Applied Digital", "期权", "投机基础设施", "P3", "AI 数据中心合同、融资和稀释"),
    ("INV-US-IREN", "IREN", "IREN", "期权", "投机基础设施", "P3", "HPC 转型收入、矿业现金流"),
    ("INV-US-CORZ", "CORZ", "Core Scientific", "期权", "投机基础设施", "P3", "HPC 托管合同、债务和稀释"),
    ("INV-US-BE", "BE", "Bloom Energy", "期权", "投机基础设施", "P3", "数据中心电力订单、毛利率、现金流"),
)


def _watchlist_yaml() -> str:
    lines = [
        "schema_version: 1",
        "source_context:",
        "  stock_context_root: /Users/murphy/Documents/Obsidian Vault/兴趣领域/股票投资/AI周期探索/0_总览",
        "  canonical_source: watchlist_master.md",
        "watchlist:",
    ]
    for canonical_id, ticker, company, status, category, priority, signal in DEFAULT_WATCHLIST_ITEMS:
        lines.extend(
            [
                f"  - canonical_id: {canonical_id}",
                f"    ticker: {ticker}",
                f"    company: {company}",
                "    market: US",
                f"    investment_status: {status}",
                f"    ai_bottleneck_category: {category}",
                f"    priority: {priority}",
                f"    next_validation_signal: {signal}",
            ]
        )
    return "\n".join(lines) + "\n"


DEFAULT_FILE_CONTENTS = {
    "DAILY_LOOP_PROMPT.md": """# 美股 AI 投资日报 Loop Prompt

你是 V3 信息源服务下的 `US_AI_MARKET_DAILY`。你的任务不是新闻摘要，而是每天用证据验证 AI 投资主线。

必须读取：
- `WATCHLIST.yml`
- `SIGNAL_DEFINITIONS.md`
- `DAILY_REPORT_TEMPLATE.md`
- `/Users/murphy/Documents/Obsidian Vault/兴趣领域/股票投资/AI周期探索/0_总览/BOTTLENECK_3X_FRAMEWORK.md`
- `/Users/murphy/Documents/Obsidian Vault/兴趣领域/股票投资/AI周期探索/0_总览/MINDSPACE_SOURCE_MCP_SOP.md`
- `/Users/murphy/Documents/Obsidian Vault/兴趣领域/股票投资/AI周期探索/0_总览/watchlist_master.md`
- `/Users/murphy/Documents/Obsidian Vault/兴趣领域/股票投资/AI周期探索/0_总览/company_score_table.md`

输出到 `信息源/YYYY-MM-WN/日报/YYYY-MM-DD_美股AI投资日报.md`。

输出原则：
- 参考 100X 知识萃取模板，保持简单清晰。
- 只回答四件事：有哪些信号、有哪些共识、有哪些踩坑点、明天看什么。
- 禁止直接输出买入/卖出。
- 每个关键判断必须标注已验证信号、待验证信号、噪音信号或需要后续追踪。
- Mindspace MCP 不可用时允许降级，但必须写明降级原因和置信度影响。
""",
    "WATCHLIST.yml": _watchlist_yaml(),
    "SIGNAL_DEFINITIONS.md": f"""# Signal Definitions

## 信号分类

- 已验证信号：公司 IR、SEC、交易所、财报、订单、指引、capex、毛利率、FCF、backlog 或公司一手披露支撑。
- 待验证信号：媒体、分析师、产业链传闻、产品发布或管理层暗示，有逻辑链但缺硬数据。
- 噪音信号：单条社媒、单日股价大涨、AI 概念标签、朋友推荐、无订单和利润验证的 TAM 故事。
- 需要后续追踪：方向重要，但证据不足以改变分类或行动标签。

## 六项测试

Need / Constraint / Control / Pricing / Capture / Duration。

## 行动标签

{_action_label_markdown()}
""",
    "DAILY_REPORT_TEMPLATE.md": """# 美股 AI 投资日报｜YYYY-MM-DD

## 0. 信源状态

- live source 是否可用：
- 降级原因：
- 置信度影响：

## 1. 今日信号

### 已验证信号

### 待验证信号

### 噪音信号

### 需要后续追踪

## 2. 市场共识

- 今天可以形成的共识：
- 仍需验证的共识：

## 3. 踩坑点

- 信源坑：
- 证据坑：
- 行动坑：
- 叙事坑：

## 4. 明日观察

- 最该验证的 3-5 个触发器：

## 附录：输入上下文
""",
    "DATA_SOURCES.yaml": """schema_version: 1
priority_order:
  - Mindspace Source MCP
  - V3 extracted notes
  - official_and_regulatory_sources
  - financial_media_and_aggregators
  - agent_reach_jina_rss_fallback
sources:
  - name: AIHot RSS
    type: rss
    url: https://aihot.virxact.com/feed.xml
    role: ai_industry_facts
    conclusion_source: false
  - name: Company IR
    type: web
    url: company_ir_pages
    role: first_party_company_disclosure
    conclusion_source: true
  - name: SEC EDGAR
    type: web
    url: https://www.sec.gov/edgar
    role: regulatory_filings
    conclusion_source: true
  - name: Exchange Filings
    type: web
    url: exchange_listing_and_disclosure_pages
    role: exchange_disclosures
    conclusion_source: true
  - name: FRED
    type: web
    url: https://fred.stlouisfed.org/
    role: macro_data
    conclusion_source: true
  - name: CME FedWatch
    type: web
    url: https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html
    role: fed_probabilities
    conclusion_source: true
  - name: U.S. Treasury
    type: web
    url: https://home.treasury.gov/
    role: treasury_rates_and_policy
    conclusion_source: true
  - name: EIA
    type: web
    url: https://www.eia.gov/
    role: energy_and_power_market_data
    conclusion_source: true
""",
    "RUNBOOK_DAILY_REPORT.md": """# Daily Report Runbook

Daily schedule: Beijing time 08:03.

Expected output: `信息源/YYYY-MM-WN/日报/YYYY-MM-DD_美股AI投资日报.md`.

1. Load V3 config and system files from this directory.
2. Resolve report date and weekly output path.
3. Read V3 recent extracted notes and stock context files.
4. Collect or summarize signals using source priority.
5. Render the report.
6. Write atomically to `信息源/YYYY-MM-WN/日报/`.
7. Append `SIGNALS.md`, `DAILY_SIGNAL_LOG.md`, and `DECISION_NOTES.md` only when facts changed.

Boundaries:
- Keep this as a V3 information-source service.
- Do not migrate historical files.
- Do not rewrite the V3 fetch, scoring, or extraction core flow.
- Treat the stock investment project as read-only context.
- Do not produce direct buy/sell advice.

Failure policy:
- If live market data is incomplete, generate a downgraded-confidence report.
- If the day is a US market holiday, generate a review/preview report.
""",
    "SIGNALS.md": "# Signals\n\n| date | ticker | signal_type | evidence | action_label | follow_up |\n|---|---|---|---|---|---|\n",
    "DAILY_SIGNAL_LOG.md": "# Daily Signal Log\n\n| date | ai_mainline_status | strongest_area | weakest_area | verified | pending | biggest_risk | next_watch |\n|---|---|---|---|---:|---:|---|---|\n",
    "DECISION_NOTES.md": "# Decision Notes\n\nOnly record changes that alter the investment framework.\n",
    "LOOP_PROMPT_US_AI_DAILY_DEV.md": """# US AI Daily Report Dev Loop

You are `US_AI_DAILY_REPORT_DEV_LOOP`.

Use TDD/RFF:
1. Red test: write one failing test for one behavior.
2. Fix minimal implementation: implement the smallest change.
3. Fortify with regression/docs: run targeted pytest and update docs/logs when needed.

Boundaries:
- 不得迁移历史文件。
- 不得改写 V3 主抓取流程。
- 股票投资项目只读。
- 输出必须落在 `信息源/YYYY-MM-WN/日报/`。
- 每轮只处理一个可验证用例，通过后再进入下一轮。

Automation target:
- Daily run time: Beijing time 08:03.
- Runner: `python -m knowledge_extractor_v3.daily_reports.runner`.

Completion promise: `<promise>US_AI_DAILY_REPORT_DEV_COMPLETE</promise>`.
""",
}


def write_default_system_files(system_dir: Path | str, *, overwrite: bool = False) -> None:
    root = Path(system_dir)
    root.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_SYSTEM_FILES:
        destination = root / name
        if destination.exists() and not overwrite:
            continue
        destination.write_text(DEFAULT_FILE_CONTENTS[name], encoding="utf-8")
