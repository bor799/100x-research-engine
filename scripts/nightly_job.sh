#!/bin/bash
# 100X V3 夜间任务 - Cron Job 入口
# 用于 crontab 调用，每天凌晨运行

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# 加载环境变量
ENV_FILE="$PROJECT_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

# 日志文件
LOG_DIR="$HOME/.100x_v3/logs"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/nightly_$TIMESTAMP.log"

echo "======================================" | tee -a "$LOG_FILE"
echo "100X V3 夜间任务启动" | tee -a "$LOG_FILE"
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG_FILE"
echo "======================================" | tee -a "$LOG_FILE"

# 1. 获取 RSS 源
echo "" | tee -a "$LOG_FILE"
echo "[1/3] 📡 获取 RSS 源..." | tee -a "$LOG_FILE"
cd "$PROJECT_ROOT"
if [ -x "scripts/run.sh" ]; then
    ./scripts/run.sh rss 2>&1 | tee -a "$LOG_FILE"
else
    echo "错误: run.sh 不可执行" | tee -a "$LOG_FILE"
    exit 1
fi

# 2. 处理队列
echo "" | tee -a "$LOG_FILE"
echo "[2/3] 🔧 处理队列..." | tee -a "$LOG_FILE"
./scripts/run.sh worker 2>&1 | tee -a "$LOG_FILE"

# 3. 总结
END_TIME=$(date +%s)
echo "" | tee -a "$LOG_FILE"
echo "======================================" | tee -a "$LOG_FILE"
echo "[3/3] ✅ 夜间任务完成" | tee -a "$LOG_FILE"
echo "结束时间: $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG_FILE"
echo "======================================" | tee -a "$LOG_FILE"

# 发送 Telegram 通知（如果配置了）
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_ADMIN_CHAT_ID:-}" ]; then
    # 获取处理的文章数量（从日志中提取）
    PROCESSED=$(grep -c "✓ 成功输出" "$LOG_FILE" 2>/dev/null || echo "0")
    FAILED=$(grep -c "✗ 失败" "$LOG_FILE" 2>/dev/null || echo "0")

    MSG="🌙 100X V3 夜间任务完成

📅 $(date '+%Y-%m-%d %H:%M')
✅ 成功: $PROCESSED 篇
❌ 失败: $FAILED 篇
📄 日志: $LOG_FILE"

    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_ADMIN_CHAT_ID}" \
        -d "text=${MSG}" >/dev/null 2>&1 || true
fi

echo "📋 日志已保存到: $LOG_FILE"
