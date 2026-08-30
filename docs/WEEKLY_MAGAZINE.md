# 周刊式阅读工作流

## 唯一事实源

所有通过吸收门（final_score >= 0.60，2026-08-30 起从 0.40 上调）且不是 Reject 的文章，只写一份 Markdown：

`<obsidian_root>/YYYY-MM-WN/YYYY-MM-DD 可读标题 <hash8>.md`

其中只有推送档（final_score >= 0.75）进入周刊与阅读状态；0.60-0.74 的中段内容是冷归档——留在周目录里可被 Obsidian 检索，但不出现在周刊 HTML、不进入阅读闭环。

周编号沿用 vault 约定 `W = ceil(day/7)`，日期按 Asia/Shanghai 的处理日。文件从上到下固定为：frontmatter、压缩萃取、用户反馈托管区、AI 定向复核托管区、`## 原文` 与抓取全文。`AI进展/` 只保留历史内容，不再写入。

## 一本周刊，每日追加

`python -m knowledge_extractor_v3.magazine build` 生成当前周唯一的 `信息源/YYYY-MM-WN/知识萃取周刊 YYYY-MM-WN.html`。HTML 是可重建的派生物，不保存唯一状态。每次新文章落档、保存反馈或完成 AI 复核后都会重建；17:20 日报任务也会在发送前重建。

页面按天组织时间线，并把此前各周未完成条目放在顶部阅读架。完成条件是：已读，且已评论或明确选择「无需评论」。其他文章继续出现在下一日、下一周的 HTML 中。

## 评论、划线与 AI

单 daemon 在 `http://127.0.0.1:8765/` 提供 localhost-only 服务。阅读状态落在同周的 `阅读状态 YYYY-MM-WN.json`，原子写入；评论和划线还会同步进入文章 Markdown 的托管区。

「交给 AI」是显式动作：系统冻结当前评论与划线快照，再异步调用已有 LLM 路由做定向复核，区分事实、作者推断与用户判断，并把结果写回 Markdown 的 AI 托管区。没有点击就不会产生额外调用。

双击 HTML 文件仍可阅读，但处于只读状态；编辑必须通过本机服务。服务只绑定 loopback、限制请求体大小、限制文章路径在配置的 Obsidian 根目录内。

## 日报发送与旧队列

新文章默认 `enqueue_individual_cards: false`，所以旧 pending 队列可以自然排空，不会边排边增长。旧 8:30/11:30 商业卡片任务与周日战略卡片任务只负责排空历史项，清零后暂停，不删除历史。

每天 17:20 的任务执行 `python -m knowledge_extractor_v3.magazine digest`，先发送当前周 HTML 文件，再发送一条包含总数、未读数、未完成数和结转数的摘要。周日同一份文件就是当周闭刊版。

## 更新与去重

同一篇文章只允许存在一份 Markdown。四层机制（配置段 `dedup:`，队列 schema 零变更）：

1. **管道早退**：抓取后先查全库索引（frontmatter `article_id` = content_hash）。同哈希已归档 → 任务直接标记完成并指向既有文件，0 次 LLM 调用。
2. **同 URL 增量**：调度器对 done 任务按 `refetch_cooldown_days`（默认 3 天）冷却重抓。重抓内容先过相似度门（归一化比率 >= `update_similarity_threshold` 且长度差 <= 150 字符 → 判重跳过，0 次 LLM）；真差异才调一次增量 LLM（`prompts/increment.md`），确认有更新就把「日期 + 增量要点 + 变化事实」追加进原文下方的 `<!-- 100x:updates:start/end -->` 托管区，永不动基础萃取、用户批注与原文。
3. **写入守卫**：并发竞态下同一哈希仍试图写第二个文件时，`LiveObsidianWriter` 抑制写入、任务指向 canonical 文件，manifest 记 `write_suppressed` 事件；同路径重复写走幂等重写，用户反馈/AI 复核/更新区原样保留。
4. **周期清理**：daemon 每 `dedup_interval_hours`（默认 24 小时，启动宽限 5 分钟，`--no-dedup` 关闭）跑一次 `dedupe_vault`。历史重复文件（同 article_id 多份）保最旧为 canonical，loser 的用户内容先合并进 canonical 再移入当周 `.trash-dedup/`（扫描面不可见、绝不移走某文章最后一份）；跨周重复会把阅读状态（已读/划线/评论）迁到 canonical 周；垃圾桶里失去幸存者的孤儿文件自动归位。手动执行：`python -m knowledge_extractor_v3.magazine dedupe [--dry-run|--restore]`。

有更新的已读文章重新进入当周周刊并标「有更新」角标，处置（评论/无需评论/划线）后清除。rejected 与 failed_terminal 任务永不因冷却重入队。
