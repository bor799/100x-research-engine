# V3 自动运行操作手册

V3 现在以 `scripts/control.sh` 作为唯一控制入口。它支持手动启动，也支持安装 macOS LaunchAgent，让系统在登录后自动恢复运行。

当前 7x24H 运行记录见：[V3 7x24H ST/SOP](V3_24X7_ST_SOP.md)。

产品调整方向见：[V3 产品方向](V3_PRODUCT_DIRECTION.md)。

当前发布交接见：[V3 发布交接](MIGRATION_COMPLETE.md)。

## 常用命令

```bash
scripts/control.sh doctor
scripts/control.sh status
scripts/control.sh start
scripts/control.sh stop
scripts/control.sh restart
scripts/control.sh recover
scripts/control.sh tmux-status
scripts/control.sh logs worker-loop
scripts/control.sh run-once 20 10
```

## 开机/登录后自动运行

```bash
scripts/control.sh install-autostart
```

安装后会创建四个 LaunchAgent：

- `com.100x.v3.scheduler-loop`
- `com.100x.v3.worker-loop`
- `com.100x.v3.telegram-bot-loop`
- `com.100x.v3.health-monitor`

停止当前运行：

```bash
scripts/control.sh stop
```

移除自动启动：

```bash
scripts/control.sh uninstall-autostart
```

## 验收命令

```bash
python -m compileall -q src tests scripts/v2_compare.py scripts/test_rss_fetch.py
bash -n scripts/*.sh
python -m pytest -q
scripts/control.sh doctor
python scripts/test_rss_fetch.py --all --timeout 8 --target-rate 95
```

2026-05-30 发布前验证结果：

- `python -m compileall -q src tests scripts/v2_compare.py scripts/test_rss_fetch.py`：通过。
- `bash -n scripts/*.sh`：通过。
- `python -m pytest -q`：`259 passed`。
- 私有文件/密钥扫描：未发现 `.env`、`config/config.local.yaml`、缓存、`.DS_Store`、bytecode 或常见 token 模式。
- RSS live fetch：因联网测试曾长时间无输出，按 operator 决策跳过；它仍是下一次 live-source-health 验收项。

## 日报命令

```bash
python -m knowledge_extractor_v3.daily_reports.runner --dry-run
100x-v3-us-ai-daily --date 2026-05-30
```

日报配置位于 `daily_reports.us_ai_market`。默认输出按交易周和 category 路由，系统文件写入配置的 `system_dir`。

## V2 对照验证

V2 只作为 shadow/reference，不作为 V3 生产依赖。

```bash
python scripts/v2_compare.py "https://example.com/article"
python scripts/v2_compare.py --url-file urls.txt --limit 10 --json
```

## 当前切换标准

- V3 nightly 连续 2 次成功。
- 手动 URL 入队、处理、输出成功。
- RSS 全量成功率大于等于 95%。
- `scripts/control.sh doctor` 无 error/critical。
- V2/Horizon cron 和进程只在上述条件满足后退役。

## GitHub 发布状态

2026-05-30 已从远端 `main` 派生 `codex/publish-v3-latest` 并创建草稿 PR：

```text
https://github.com/bor799/information-tracking-agent/pull/1
```

该 PR 保留远端 README 公共文案，发布范围只包含 V3 产品目录与安全配置模板，不包含本地私有运行目录或密钥。
