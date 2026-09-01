# Story-Identity Dedup（跨传输去重）

2026-09-01 上线。解决的问题：同一篇编辑部内容经由不同传输通道到达 —— 原发
RSS（英文原站）+ 聚合器中文导读（aihot.virxact.com）+ 带 tracking 参数的镜像
URL —— 所有既有去重层都以"传输身份"为键（URL 字符串、抓取字节），对这类重复
结构性失明，于是在同一周文件夹里落了两篇文章。

## 第一性原理

一篇文章有两组身份：

| 身份 | 定义 | 稳定性 |
|---|---|---|
| 传输身份 | URL、内容哈希 | 同一故事换通道即变 |
| 编辑身份 | 讲的是哪件事 | 跨通道不变 |

去重要匹配的是编辑身份。系统里已经存在编辑身份的规范表示：**吸收卡**
（`obsidian_brief_markdown`）—— 无论原文什么语言，卡片一律中文、保留实体与
数字。因此故事比对在卡片上进行，而不是原文。

## 判定规则（证据分层）

对两张卡的词元集（拉丁词 + CJK 二元组，停用词表剔除功能词、卡片模板词
`## 关键事实/信号`、领域口水词 `模型/发布/开源`）计算：共享稀有词元数
（df ≤ 2 的词元）、其中"强词元"数（含数字且长度 ≥ 3，或长度 ≥ 5 的拉丁词）、
包含度（交集/较小词集，适配摘要-原文长度不对称）、Jaccard、标题 Jaccard：

```
重复 iff  jaccard ≥ 0.60                                # 几乎逐字相同
      or  共享稀有 ≥ 3 且
          ( (包含度 ≥ 0.10 且
             ( 强词元 ≥ 2                                # 两个以上实体/数字
               或 (强词元 ≥ 1 且 标题Jaccard ≥ 0.20)      # 实体+标题佐证
               或 标题Jaccard ≥ 0.60))                   # 标题几乎相同
           或 (包含度 ≥ 0.15 且 共享稀有 ≥ 12) )          # 词汇规模证据
```

设计取向是**查准优先**：误并（把两篇不同文章合成一篇）是静默丢失，漏并
（重复留下）可见且可恢复（`.trash-dedup/story/`）。词法证据分不清的（同一
主题的两篇不同文章 vs 同一故事的两次独立报道），一律保留两篇。

### 校准记录（2026-08/09 真实 vault，98 篇）

修复过程中用真实库校准出三类误报，各有一条回归测试：

| 误报形态 | 例子 | 原误判证据 | 修复 |
|---|---|---|---|
| 同刊文风 | 两篇 charonhub 帖子 | chosen/doesn/shift + 理速/到端 | 停用词表 + 强词元分层 |
| 单实体相邻主题 | Debian 投票 vs 封杀趋势分析 | debian 1 个实体 | 强词元 ≥ 2 或标题佐证 |
| 短数字碰撞 | DeepSeek vs GLM 两篇发布文 | cybergym + "53" | 数字须 ≥ 3 字符 |

校准后全库判出 7 组真实跨通道重复（含本次事故对 0f2b3f3f/84e02ae1），
全部核对为真；已一次性合并归档。

## 三层防线

1. **队列层**：`normalize_url` 剔除 utm_* 等 tracking 参数，队列
   `UNIQUE(url)` 收敛镜像变体（同一 URL 只有一行）。
2. **写入层**（`pipeline.py`，吸收后、落盘前）：新卡片对最近
   `story_window_weeks`（默认 2）周的索引做匹配；命中则不写第二个文件，
   任务直接 DONE 于规范路径，`dedup_outcome="duplicate_story"`，
   manifest 记 `story_duplicate_suppressed`。代价为零额外 LLM 调用
   （吸收本来就要做）。
3. **自愈层**（`dedupe_vault` 的 story 扫描，daemon 每 24h 一次）：
   全库重扫同规则，历史漏网重复自动合并；败者移入
   `.trash-dedup/story/`（比 by-id 回收站深一层，restore 看不见），
   阅读状态按 article_id 改键到规范文章上，manifest 记 `story_merged`。

### 复活漏洞（已修）

`restore_orphans` 只看"该 article_id 是否还有活副本"。story 合并移走某 id
的最后一个活副本后，**更早的 by-id 回收站里同 id 的陈旧条目**会被误判为孤儿
而复活，悄悄撤销刚做的合并。修复：restore 前先收集 story 回收站里的全部 id，
命中即跳过。

## 配置（`config.yaml` → `dedup:`）

| 键 | 默认 | 含义 |
|---|---|---|
| `story_dedup` | true | 总开关（写入门 + 自愈扫描） |
| `story_rare_tokens` | 3 | 共享稀有词元前置门 |
| `story_mass_tokens` | 12 | 词汇规模路径阈值 |
| `story_strong_tokens` | 2 | 强词元路径阈值 |
| `story_overlap_min` | 0.15 | 规模路径包含度下限 |
| `story_strong_jaccard` | 0.60 | 逐字相同阈值 |
| `story_title_min` | 0.20 | 标题佐证阈值（需 ≥1 强词元） |
| `story_window_weeks` | 2 | 写入门只比对最近 N 周 |
| `story_max_df` | 2 | 词元在窗口内出现 ≤ N 篇才算稀有 |

## 运维

- 全库自愈：`python -c "from knowledge_extractor_v3.outputs.dedupe import dedupe_vault; dedupe_vault(root)"`（先 `dry_run=True` 看分组再应用）
- 误并恢复：从 `YYYY-MM-WN/.trash-dedup/story/` 把文件移回周目录即可
  （阅读状态需手工对回；manifest 里有完整审计）
- 测试：`tests/test_story_identity.py`（21 条，含事故对回归与三类误报回归）
