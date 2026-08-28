# 周刊式阅读工作流

## 唯一事实源

所有通过吸收门、且不是 Reject 的文章，只写一份 Markdown：

`<obsidian_root>/YYYY-MM-WN/YYYY-MM-DD 可读标题 <hash8>.md`

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
