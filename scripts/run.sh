#!/bin/bash
# 100X V3 启动脚本 (Legacy wrapper - delegates to run-v3.sh)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Set PYTHONPATH
export PYTHONPATH="${PYTHONPATH:-}:${PROJECT_ROOT}/src"

ENV_FILE="$PROJECT_ROOT/.env"
LOG_FILE="$HOME/100x-v3-daemon.log"
STATE_ROOT="${STATE_ROOT:-${HOME}/.100x_v3}"
export STATE_ROOT

QUEUE_DB_PATH="${QUEUE_DB_PATH:-${STATE_ROOT}/queue.db}"
export QUEUE_DB_PATH

load_env() {
  if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
}

resolve_python() {
  # 优先使用项目内的虚拟环境
  if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    echo "$PROJECT_ROOT/.venv/bin/python"
    return
  fi

  # 其次使用全局虚拟环境
  if [ -x "$HOME/venv/bin/python" ]; then
    echo "$HOME/venv/bin/python"
    return
  fi

  # 最后使用系统 python3
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi

  command -v python
}

init_state() {
  # 确保状态目录存在
  mkdir -p "$STATE_ROOT"

  # 确保输出目录存在
  OBSIDIAN_ROOT="$(grep "obsidian_root:" "$PROJECT_ROOT/config/config.local.yaml" 2>/dev/null | awk '{print $2}' | tr -d '"')"
  if [ -n "$OBSIDIAN_ROOT" ]; then
    mkdir -p "$OBSIDIAN_ROOT"
  fi
}

show_help() {
    cat << EOF
100X V3 知识萃取系统

用法:
  ./scripts/run.sh [命令] [选项]

单次命令:
  rss                 运行一次所有 RSS 源
  worker              启动 Worker 处理队列
  url <URL>           处理单个 URL

守护进程:
  start               启动后台守护进程
  stop                停止守护进程
  restart             重启守护进程
  status              查看运行状态
  logs                查看实时日志

夜间任务:
  nightly             执行夜间完整任务（RSS + Worker）

配置:
  doctor              运行系统诊断
  test                测试配置和依赖

EOF
}

load_env
PYTHON="$(resolve_python)"

case "${1:-help}" in
    rss)
        shift
        echo "📡 获取 RSS 源 (通过 scheduler)..."
        exec "$SCRIPT_DIR/run-v3.sh" scheduler-once "$@"
        ;;
    worker)
        shift
        echo "🔧 启动 Worker 处理队列..."
        exec "$SCRIPT_DIR/run-v3.sh" worker-once "$@"
        ;;
    url)
        shift
        [ -z "${1:-}" ] && echo "错误: 请提供 URL" && exit 1
        echo "🔗 处理 URL: $1"
        exec "$SCRIPT_DIR/run-v3.sh" enqueue-url "$1"
        ;;
    start)
        echo "🚀 启动 V3 守护进程..."
        init_state

        # 停止现有进程
        pkill -f "knowledge_extractor_v3.worker" 2>/dev/null || true
        sleep 2

        # 启动 Worker 守护进程
        PYTHONUNBUFFERED=1 nohup "$PYTHON" -m knowledge_extractor_v3.worker --loop >> "$LOG_FILE" 2>&1 &
        sleep 2

        echo "✅ V3 守护进程已启动"
        echo "📋 日志: tail -f $LOG_FILE"
        ;;
    stop)
        echo "🛑 停止 V3 守护进程..."
        pkill -f "knowledge_extractor_v3.worker" 2>/dev/null || true
        sleep 2
        echo "✅ 已停止"
        ;;
    restart)
        "$0" stop
        sleep 2
        "$0" start
        ;;
    status)
        exec "$SCRIPT_DIR/run-v3.sh status"
        ;;
    logs)
        tail -f "$LOG_FILE"
        ;;
    nightly)
        echo "🌙 执行夜间任务..."
        init_state

        # 记录开始时间
        START_TIME=$(date +%s)

        # 1. 获取 RSS 源 (通过 scheduler)
        echo "📡 [1/3] 获取 RSS 源..."
        "$PYTHON" -m knowledge_extractor_v3.scheduler --once --limit 100 2>&1 | tee -a "$LOG_FILE"

        # 2. 处理队列
        echo "🔧 [2/3] 处理队列..."
        "$PYTHON" -m knowledge_extractor_v3.worker --once --limit 50 --mode staging 2>&1 | tee -a "$LOG_FILE"

        # 3. 统计结果
        END_TIME=$(date +%s)
        DURATION=$((END_TIME - START_TIME))
        echo "✅ [3/3] 夜间任务完成，耗时: ${DURATION}s"

        # 发送 Telegram 通知（如果配置了）
        if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_ADMIN_CHAT_ID:-}" ]; then
            MSG="🌙 V3 夜间任务完成
耗时: ${DURATION}s
时间: $(date '+%Y-%m-%d %H:%M:%S')"
            curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
                -d "chat_id=${TELEGRAM_ADMIN_CHAT_ID}" \
                -d "text=${MSG}" >/dev/null 2>&1 || true
        fi
        ;;
    doctor)
        exec "$SCRIPT_DIR/run-v3.sh preflight"
        ;;
    test)
        echo "🧪 测试配置..."
        "$PYTHON" -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT/src')

# 测试配置加载
from knowledge_extractor_v3.config_loader import ConfigLoader

loader = ConfigLoader(project_root='$PROJECT_ROOT')
config = loader.load()

print('✅ 配置加载成功')
print(f'  - LLM Provider: {config.llm.provider}')
print(f'  - Scoring Model: {config.llm.scoring_model}')
print(f'  - Extraction Model: {config.llm.extraction_model}')
print(f'  - Sources: {len(config.sources)} 个')
print(f'  - Live Mode: {config.live.enabled}')
print(f'  - Scheduler: {config.scheduler.enabled}')
" 2>&1
        ;;
    *)
        show_help
        ;;
esac
