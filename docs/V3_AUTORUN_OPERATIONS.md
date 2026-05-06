# V3 自动运行操作手册

V3 现在以 `scripts/control.sh` 作为唯一控制入口。它支持手动启动，也支持安装 macOS LaunchAgent，让系统在登录后自动恢复运行。

当前 7x24H 运行记录见：[V3 7x24H ST/SOP](V3_24X7_ST_SOP.md)。

产品调整方向见：[V3 产品方向](V3_PRODUCT_DIRECTION.md)。

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
python -m pytest -q
python -m compileall src tests scripts/v2_compare.py scripts/test_rss_fetch.py
bash -n scripts/*.sh
scripts/control.sh doctor
python scripts/test_rss_fetch.py --all --timeout 8 --target-rate 95
```

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
