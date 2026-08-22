#!/usr/bin/env node
// 商业档（早/午）空跑门：先 best-effort 回收卡死的 processing 条目（不烧 attempt），再看 business lane
// 有待推送 -> exit 0 放行；无 -> exit 2 跳过；检查失败/超时 -> exit 1 fail-closed
import { spawnSync } from 'node:child_process';
import { appendFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const PROJECT = '/Users/murphy/Documents/Obsidian Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v3';
const LOG = join(homedir(), '.100x_v3', 'gate_business_lane.log');
const TIMEOUT_MS = 30_000;

function log(msg) {
  try { appendFileSync(LOG, `${new Date().toISOString()} [business-gate] ${msg}\n`); } catch {}
}

const rec = spawnSync('python3', ['scripts/wechat_outbox.py', 'recover', '--stale-seconds', '14400'], {
  cwd: PROJECT,
  timeout: TIMEOUT_MS,
  stdio: ['ignore', 'pipe', 'pipe'],
});
if (rec.error || rec.status !== 0) {
  log(`stale recovery 失败（忽略）：${rec.error ? rec.error.message : `exit ${rec.status}`} ${rec.stderr ? rec.stderr.toString().trim() : ''}`);
} else {
  log(`stale recovery 完成：recovered=${rec.stdout ? rec.stdout.toString().trim() : '0'}`);
}

const r = spawnSync('python3', ['scripts/wechat_outbox.py', 'has-pending', '--lane', 'business'], {
  cwd: PROJECT,
  timeout: TIMEOUT_MS,
  stdio: ['ignore', 'pipe', 'pipe'],
});
if (r.status === 0) {
  log('business lane 有待推送条目，放行本轮');
  console.error('business outbox has pending items');
  process.exit(0);
}
if (r.status === 2) {
  log('business lane 无待推送条目，跳过本轮');
  console.error('business outbox empty');
  process.exit(2);
}
// 超时/崩溃时 r.status 为 null，r.error 带 ETIMEDOUT 等原因
log(`检查失败，fail-closed：${r.error ? r.error.message : `exit ${r.status}`} ${r.stderr ? r.stderr.toString().trim() : ''}`);
console.error('has-pending check failed:', r.status, r.error?.message, r.stderr?.toString());
process.exit(1);
