#!/usr/bin/env node
// 合成数据测试：不依赖任何真实刊物/路径/日期。用法：node tests/test-worker.mjs
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const WORKER = path.join(ROOT, 'node', 'worker.cjs');

const FIXTURE = {
  schema: 'knowledge-magazine/1',
  material_date: '2099-01-02',
  week: '2099-01-W1',
  build: { build_id: '2099-01-02-test0001', built_at: '2099-01-02 08:00', model: 'test' },
  today: {
    date: '2099-01-02', verdict: '合成总判断。', picks: [], groups: [],
    market_rows: [], new_notes: [],
    cross_checks: [{ claim: '合成判断', primary_source: '合成主证据', status: 'single_source',
                     verdict: '合成核验', counterpoint: '合成限制',
                     sources: [{ url: 'https://example.com/a', label: '示例', primary_key: 'fixture' }] }],
  },
  week_view: { week: '2099-01-W1', what_grew: '合成脉络。', topics: [], deep_reads: [], week_wires: [] },
  coverage: { wire: { date: '2099-01-02' }, weekly: { week: '2099-01-W1' }, notes: [] },
};

const dir = mkdtempSync(path.join(tmpdir(), 'mag-test-'));
mkdirSync(path.join(dir, 'data'), { recursive: true });
writeFileSync(path.join(dir, 'data', 'magazine-data.json'), JSON.stringify(FIXTURE));
writeFileSync(path.join(dir, '今日-2099-01-02.html'), '<html></html>');

function rpc(lines) {
  const r = spawnSync('node', [WORKER], { input: lines.map((l) => JSON.stringify(l)).join('\n') + '\n',
                                          encoding: 'utf8' });
  assert.equal(r.status, 0, 'worker 进程异常退出: ' + r.stderr);
  return r.stdout.trim().split('\n').map((l) => JSON.parse(l));
}

// 1 指定目录读数据
const [a] = rpc([{ jsonrpc: '2.0', id: 1, method: 'magazine.data', params: { dir } }]);
assert.equal(a.result.data.build.build_id, '2099-01-02-test0001');
assert.ok(a.result.mtime);

// 2 文件清单
const [b] = rpc([{ jsonrpc: '2.0', id: 2, method: 'magazine.files', params: { dir } }]);
assert.ok(b.result.files.some((f) => f.name === '今日-2099-01-02.html'));
assert.equal(b.result.hasData, true);

// 3 defaultDir 方法
const [c] = rpc([{ jsonrpc: '2.0', id: 3, method: 'magazine.defaultDir', params: {} }]);
assert.ok(c.result.defaultDir.endsWith(path.join('Desktop', '知识杂志', '杂志')));

// 4 未知方法
const [d] = rpc([{ jsonrpc: '2.0', id: 4, method: 'magazine.nope', params: {} }]);
assert.equal(d.error.code, -32601);

// 5 不存在的目录 → 结构化错误
const [e] = rpc([{ jsonrpc: '2.0', id: 5, method: 'magazine.data', params: { dir: '/nonexistent/xyz' } }]);
assert.ok(e.error && e.error.code === -32000);

console.log('PASS: worker 合成数据 5 项断言全部通过');
