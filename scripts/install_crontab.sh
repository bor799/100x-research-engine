#!/bin/bash
# 100X V3 Cron 任务安装脚本

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
NIGHTLY_JOB="$PROJECT_ROOT/scripts/nightly_job.sh"

# 确保 nightly_job.sh 可执行
chmod +x "$NIGHTLY_JOB"

# 定义 cron 任务
# 每天 05:30 运行夜间任务（在 V2 的 05:10 Horizon 之后）
CRON_LINE="30 5 * * * $NIGHTLY_JOB"

echo "📋 正在安装 V3 Cron 任务..."
echo ""
echo "Cron 任务内容:"
echo "  $CRON_LINE"
echo ""

# 检查是否已存在
if crontab -l 2>/dev/null | grep -q "100X V3"; then
    echo "⚠️  检测到已存在的 V3 Cron 任务"
    echo ""
    echo "当前 crontab 内容:"
    crontab -l 2>/dev/null | grep -v "^#" | grep "100X\|nightly_job"
    echo ""
    read -p "是否覆盖? (y/N): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "❌ 取消安装"
        exit 0
    fi
    # 删除旧的 V3 任务
    crontab -l 2>/dev/null | grep -v "100X V3" | grep -v "nightly_job.sh" | crontab -
fi

# 添加新的 cron 任务
(crontab -l 2>/dev/null | grep -v "100X V3"; echo "# 100X V3 夜间任务 (每天 05:30)"; echo "$CRON_LINE") | crontab -

echo ""
echo "✅ V3 Cron 任务已安装"
echo ""
echo "📅 下次运行时间: 每天 05:30"
echo ""
echo "查看当前 crontab:"
crontab -l 2>/dev/null | grep -E "100X|nightly_job"
echo ""
echo "如需手动运行夜间任务:"
echo "  $NIGHTLY_JOB"
