import { spawnSync } from 'node:child_process';
const r = spawnSync('python3', ['scripts/wechat_outbox.py', 'has-pending', '--lane', 'requested'], { stdio: ['ignore', 'pipe', 'pipe'] });
process.exit(r.status === 0 ? 0 : 2);
