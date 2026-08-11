import { spawnSync } from 'node:child_process';
const r = spawnSync('python3', ['scripts/wechat_outbox.py', 'has-pending', '--lane', 'strategic'], { stdio: ['ignore', 'pipe', 'pipe'] });
if (r.status === 0) { console.error('strategic outbox has pending items'); process.exit(0); }
if (r.status === 2) { console.error('strategic outbox empty'); process.exit(2); }
console.error('has-pending check failed:', r.status, r.stderr?.toString()); process.exit(1);
