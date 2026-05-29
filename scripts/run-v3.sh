#!/bin/bash
# V3 Operator Commands Wrapper
#
# Usage:
#   ./scripts/run-v3.sh preflight
#   ./scripts/run-v3.sh status
#   ./scripts/run-v3.sh enqueue-url "https://example.com/article"
#   ./scripts/run-v3.sh worker-once --limit 5
#   ./scripts/run-v3.sh scheduler-once --limit 10
#   ./scripts/run-v3.sh live-worker
#   ./scripts/run-v3.sh live-scheduler
#   ./scripts/run-v3.sh live-bot
#   ./scripts/run-v3.sh stop-role worker
#   ./scripts/run-v3.sh logs worker

set -euo pipefail

# Project root
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# Python path setup
export PYTHONPATH="${PYTHONPATH:-}:${PROJECT_ROOT}/src"

# State root (only export if user explicitly set it; otherwise let config/defaults resolve)
if [ -n "${STATE_ROOT+x}" ]; then
    export STATE_ROOT
fi

# Queue DB path
if [ -n "${QUEUE_DB_PATH+x}" ]; then
    export QUEUE_DB_PATH
fi

ENV_FILE="${PROJECT_ROOT}/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$ENV_FILE"
    set +a
fi

# Source config if exists
if [ -f "config/config.local.yaml" ]; then
    CONFIG_FILE="config/config.local.yaml"
else
    CONFIG_FILE="config/config.example.yaml"
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_python() {
    if ! command -v python &> /dev/null; then
        log_error "Python not found in PATH"
        exit 1
    fi
}

check_live_enabled() {
    # Check if live mode is enabled in config
    if python -c "
import sys
sys.path.insert(0, 'src')
from pathlib import Path
from knowledge_extractor_v3.config_loader import ConfigLoader
loader = ConfigLoader()
config = loader.load()
live_enabled = config.live.enabled if hasattr(config, 'live') else False
print('1' if live_enabled else '0')
" 2>/dev/null | grep -q "1"; then
        return 0
    else
        return 1
    fi
}

require_live_enabled() {
    if ! check_live_enabled; then
        log_error "Live mode is not enabled. Set live.enabled: true in config/config.local.yaml"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_preflight() {
    log_info "Running preflight checks..."

    # Check Python
    check_python

    # Compile check
    log_info "Checking Python syntax..."
    python -m compileall src tests || {
        log_error "Python syntax check failed"
        return 1
    }

    # Runtime guard check - now fails hard
    log_info "Checking runtime guard..."
    if ! python -m knowledge_extractor_v3.runtime_guard --check; then
        log_error "Runtime guard check failed"
        return 1
    fi

    # Path consistency check - ensure enqueue and worker use same queue
    log_info "Checking path consistency..."
    python -c "
import sys
import os
sys.path.insert(0, 'src')
from pathlib import Path
from knowledge_extractor_v3.config_loader import ConfigLoader
from knowledge_extractor_v3.runtime_guard import resolve_runtime_paths

loader = ConfigLoader()
config = loader.load()
project_root = Path.cwd()
paths = resolve_runtime_paths(project_root, config, loader, env=os.environ)

# Verify queue is under state root
if not str(paths.queue_db_path.resolve()).startswith(str(paths.state_root.resolve())):
    print(f'ERROR: Queue DB not under STATE_ROOT', file=sys.stderr)
    print(f'  STATE_ROOT: {paths.state_root}', file=sys.stderr)
    print(f'  QUEUE_DB_PATH: {paths.queue_db_path}', file=sys.stderr)
    sys.exit(1)

print('Path consistency OK')
print(f'  STATE_ROOT: {paths.state_root}')
print(f'  QUEUE_DB_PATH: {paths.queue_db_path}')
print(f'  state_root_is_explicit: {paths.state_root_is_explicit}')
print(f'  queue_db_is_explicit: {paths.queue_db_is_explicit}')
" || {
        log_error "Path consistency check failed"
        return 1
    }

    # Config validation
    log_info "Validating configuration..."
    python -c "
import sys
sys.path.insert(0, 'src')
from pathlib import Path
from knowledge_extractor_v3.config_loader import ConfigLoader
from knowledge_extractor_v3.models import sha256_text
from knowledge_extractor_v3.prompt_registry import PromptRegistry
loader = ConfigLoader()
config = loader.load()
prompts = PromptRegistry.from_config(Path.cwd(), config.prompts)
active_bundle = prompts.active_bundle_name
prompt_hash = sha256_text(
    prompts.load_prompt(active_bundle, 'scoring')
    + prompts.load_prompt(active_bundle, 'extraction')
    + prompts.load_prompt(active_bundle, 'telegram_brief'),
    length=16,
)
print('Config loaded successfully')
print('  State root:', config.runtime.state_root)
print('  Queue DB:', config.runtime.queue_db_path)
print('  Live enabled:', config.live.enabled)
print('  Active prompt bundle:', active_bundle)
print('  Active prompt hash:', prompt_hash)
" || {
        log_error "Config validation failed"
        return 1
    }

    # Queue schema check
    log_info "Checking queue schema..."
    python -c "
import sys, os
sys.path.insert(0, 'src')
from pathlib import Path
from knowledge_extractor_v3.config_loader import ConfigLoader
from knowledge_extractor_v3.runtime_guard import resolve_runtime_paths
from knowledge_extractor_v3.queue_store import QueueStore
loader = ConfigLoader()
config = loader.load()
paths = resolve_runtime_paths(Path.cwd(), config, loader, env=os.environ)
queue = QueueStore(paths.queue_db_path)
queue.initialize()
queue.validate_schema()
print('Queue schema OK')
" || {
        log_error "Queue schema check failed"
        return 1
    }

    log_info "Preflight complete!"
}

cmd_status() {
    log_info "V3 System Status"
    echo ""

    # Queue status
    python -c "
import sys, os
sys.path.insert(0, 'src')
from pathlib import Path
from knowledge_extractor_v3.config_loader import ConfigLoader
from knowledge_extractor_v3.models import sha256_text
from knowledge_extractor_v3.prompt_registry import PromptRegistry
from knowledge_extractor_v3.runtime_guard import resolve_runtime_paths
from knowledge_extractor_v3.queue_store import QueueStore
loader = ConfigLoader()
config = loader.load()
paths = resolve_runtime_paths(Path.cwd(), config, loader, env=os.environ)
prompts = PromptRegistry.from_config(Path.cwd(), config.prompts)
active_bundle = prompts.active_bundle_name
prompt_hash = sha256_text(
    prompts.load_prompt(active_bundle, 'scoring')
    + prompts.load_prompt(active_bundle, 'extraction')
    + prompts.load_prompt(active_bundle, 'telegram_brief'),
    length=16,
)
queue = QueueStore(paths.queue_db_path)
queue.initialize()

counts = queue.count_by_status()
print('Queue Status:')
for status, count in sorted(counts.items()):
    print(f'  {status}: {count}')
print(f'  Queue path: {paths.queue_db_path}')
print('Prompt Status:')
print(f'  Active bundle: {active_bundle}')
print(f'  Active hash: {prompt_hash}')
" || echo "  (Queue not initialized)"

    echo ""

    # Role status
    log_info "Role Lock Files:"
    local effective_state_root
    effective_state_root="$(python -c "
import os, sys
sys.path.insert(0, 'src')
from pathlib import Path
from knowledge_extractor_v3.config_loader import ConfigLoader
from knowledge_extractor_v3.runtime_guard import resolve_runtime_paths
loader = ConfigLoader()
config = loader.load()
paths = resolve_runtime_paths(Path.cwd(), config, loader, env=os.environ)
print(paths.state_root)
")"
    if [ -d "${effective_state_root}/roles" ]; then
        # Use find instead of glob to avoid shell expansion issues
        find "${effective_state_root}/roles" -maxdepth 1 -name "*.json" -type f 2>/dev/null | while read -r role_file; do
            role=$(basename "$role_file" .json)
            echo "  $role: $(cat "$role_file" 2>/dev/null || echo 'unknown')"
        done
    else
        echo "  (No role directory)"
    fi
}

cmd_enqueue_url() {
    local url="$1"
    if [ -z "$url" ]; then
        log_error "Usage: run-v3.sh enqueue-url <URL>"
        exit 1
    fi

    log_info "Enqueueing URL: $url"

    python -c "
import sys, os
sys.path.insert(0, 'src')
from pathlib import Path
from knowledge_extractor_v3.config_loader import ConfigLoader
from knowledge_extractor_v3.runtime_guard import resolve_runtime_paths
from knowledge_extractor_v3.queue_store import QueueStore
loader = ConfigLoader()
config = loader.load()
paths = resolve_runtime_paths(Path.cwd(), config, loader, env=os.environ)
queue = QueueStore(paths.queue_db_path)
task = queue.enqueue('$url', source='manual', priority=50)
print(f'Enqueued task ID: {task.id}')
print(f'  URL: {task.url}')
print(f'  Status: {task.status.value}')
print(f'  Queue: {paths.queue_db_path}')
"
}

cmd_worker_once() {
    log_info "Running worker (once mode)..."

    python -m knowledge_extractor_v3.worker --once "$@"
}

cmd_scheduler_once() {
    log_info "Running scheduler (once mode)..."

    python -m knowledge_extractor_v3.scheduler --once "$@"
}

cmd_staging_schedule() {
    log_info "Running scheduler in staging mode..."

    python -m knowledge_extractor_v3.scheduler --once "$@"
}

cmd_live_worker() {
    require_live_enabled
    log_info "Starting live worker loop..."

    python -m knowledge_extractor_v3.worker --loop --mode live "$@"
}

cmd_live_scheduler() {
    require_live_enabled
    log_info "Starting live scheduler loop..."

    python -m knowledge_extractor_v3.scheduler --loop "$@"
}

cmd_live_bot() {
    require_live_enabled
    log_info "Starting live Telegram bot..."

    python -m knowledge_extractor_v3.telegram_bot "$@"
}

cmd_stop_role() {
    local role="$1"
    if [ -z "$role" ]; then
        log_error "Usage: run-v3.sh stop-role <worker|scheduler|telegram_bot>"
        exit 1
    fi

    log_info "Stopping role: $role"

    # Remove lock file
    local lock_file="${STATE_ROOT}/locks/${role}.lock"
    if [ -f "$lock_file" ]; then
        rm -f "$lock_file"
        log_info "Removed lock file: $lock_file"
    else
        log_warn "No lock file found for role: $role"
    fi

    # Mark role as stopped in fingerprint
    local role_file="${STATE_ROOT}/roles/${role}.json"
    if [ -f "$role_file" ]; then
        # Update status to stopped
        python -c "
import sys, json
sys.path.insert(0, 'src')
with open('${role_file}', 'r') as f:
    data = json.load(f)
data['status'] = 'stopped'
with open('${role_file}', 'w') as f:
    json.dump(data, f, indent=2)
"
        log_info "Marked role as stopped"
    fi
}

cmd_logs() {
    local role="$1"
    if [ -z "$role" ]; then
        log_error "Usage: run-v3.sh logs <worker|scheduler|telegram_bot>"
        exit 1
    fi

    local log_file="${STATE_ROOT}/logs/${role}.log"
    if [ -f "$log_file" ]; then
        tail -f "$log_file"
    else
        log_warn "Log file not found: $log_file"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    local command="${1:-}"
    shift || true

    case "$command" in
        preflight)
            cmd_preflight
            ;;
        status)
            cmd_status
            ;;
        enqueue-url)
            cmd_enqueue_url "$@"
            ;;
        worker-once)
            cmd_worker_once "$@"
            ;;
        scheduler-once)
            cmd_scheduler_once "$@"
            ;;
        staging-schedule)
            cmd_staging_schedule "$@"
            ;;
        live-worker)
            cmd_live_worker "$@"
            ;;
        live-scheduler)
            cmd_live_scheduler "$@"
            ;;
        live-bot)
            cmd_live_bot "$@"
            ;;
        stop-role)
            cmd_stop_role "$@"
            ;;
        logs)
            cmd_logs "$@"
            ;;
        *)
            echo "V3 Operator Commands"
            echo ""
            echo "Usage: $0 <command> [args...]"
            echo ""
            echo "Commands:"
            echo "  preflight              Run preflight checks"
            echo "  status                 Show system status"
            echo "  enqueue-url <url>      Enqueue a URL for processing"
            echo "  worker-once [args]     Run worker once (processes batch)"
            echo "  scheduler-once [args]  Run scheduler once (discovers items)"
            echo "  staging-schedule [args] Run scheduler in staging mode"
            echo "  live-worker [args]     Start live worker loop"
            echo "  live-scheduler [args]  Start live scheduler loop"
            echo "  live-bot [args]        Start live Telegram bot"
            echo "  stop-role <role>       Stop a live role"
            echo "  logs <role>            Tail logs for a role"
            echo ""
            echo "Live commands require live.enabled: true in config"
            exit 1
            ;;
    esac
}

main "$@"
