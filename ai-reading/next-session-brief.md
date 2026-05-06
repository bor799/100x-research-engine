# Next Session Brief

Use this as the opening instruction for the next independent development session.

```text
请继续开发 knowledge-extractor/v3。

先阅读 V3 内部材料：
1. ai-reading/README.md
2. ai-reading/phase2-background-and-backend-architecture.md
3. ai-reading/prompt-versioning-and-ab-testing.md
4. ai-reading/v2-operations-lessons-for-v3.md
5. ai-reading/phase2-design-review-and-core-development-plan.md
6. docs/architecture.md
7. docs/error-architecture.md
8. prompts/registry.json
9. src/knowledge_extractor_v3/runtime_guard.py
10. src/knowledge_extractor_v3/queue_store.py
11. src/knowledge_extractor_v3/prompt_registry.py

第二阶段目标：
实现完整后端 dry-run/staging 核心框架，但不要直接启动 live daemon。

必须实现：
1. models.py：FetchedContent、TypedError、StageResult、ScoreResult、ExtractionResult、ProcessResult、OutputResult。
2. prompt_parser.py：校验 scoring/extraction JSON schema，缺字段转 parse_error。
3. llm provider stub：支持 scoring/extraction/telegram 格式化，模拟 rate limit、timeout、parse error。
4. fetcher stub/fixture：不读取 V2 队列，用 V3 fixtures 跑内容。
5. pipeline.py：Fetch -> Validate -> Score -> Extract -> OutputResult -> QueueStore 状态更新。
6. prompt registry 接入 pipeline：可选择 active_bundle，也可对 parallel_test_bundles 并跑。
7. staging Obsidian writer 和 Telegram stub：先写测试目录和 stub，不接 live。
8. tests：高信号、低质量、parse_error、llm_rate_limit、fetch_failed、output_failed。

验收：
- python -m pytest 通过。
- python -m compileall src tests 通过。
- 使用临时 STATE_ROOT 和临时 queue.db 完成 dry-run smoke。
- 不创建真实 ~/.100x_v3/queue.db。
- 不读取 ~/.100x_v2/queue.db。
- 不复制 V2 密钥。
- 不启动 live daemon、live scheduler、live Telegram bot。

下一阶段 live 目标：
在 staging 通过后，再实现 run-v3.sh、startup_check、scheduler、Obsidian live write、Telegram live push，并启动夜间定时任务做效果观察。
```
