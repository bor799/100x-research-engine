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
writeFileSync(path.join(dir, 'data', 'magazine-data.json'), JSON.stringify({ ...FIXTURE,
  build: { ...FIXTURE.build, build_id: '2099-01-02-LEGACY00' } }));  // 旧固定路径批（不应被指针选中）
writeFileSync(path.join(dir, '今日-2099-01-02.html'), '<html></html>');
// 新结构：批次目录 + 指针
mkdirSync(path.join(dir, 'batches', '2099-01-02-test0001'), { recursive: true });
writeFileSync(path.join(dir, 'batches', '2099-01-02-test0001', 'magazine-data.json'), JSON.stringify(FIXTURE));
writeFileSync(path.join(dir, 'batches', '2099-01-02-test0001', '今日-2099-01-02.html'), '<html>b1</html>');
writeFileSync(path.join(dir, 'batch-pointer.js'),
  'window.MAGAZINE_CURRENT_BATCH = {"schema":"knowledge-magazine-batch/2","build_id":"2099-01-02-test0001","material_date":"2099-01-02","built_at":"2099-01-02 08:00","dir":"batches/2099-01-02-test0001","files":{}};\n');
// lane 目录：一条有期刊、一条空、一条带 index.html 路由页
mkdirSync(path.join(path.dirname(dir), '每日短讯'), { recursive: true });
writeFileSync(path.join(path.dirname(dir), '每日短讯', '每日短讯-2099-01-01.html'), '<html></html>');
mkdirSync(path.join(path.dirname(dir), '深读'), { recursive: true });
writeFileSync(path.join(path.dirname(dir), '深读', 'index.html'), '<html></html>');

function rpc(lines) {
  const r = spawnSync('node', [WORKER], { input: lines.map((l) => JSON.stringify(l)).join('\n') + '\n',
                                          encoding: 'utf8' });
  assert.equal(r.status, 0, 'worker 进程异常退出: ' + r.stderr);
  return r.stdout.trim().split('\n').map((l) => JSON.parse(l));
}

// 1 指定目录读数据 + 出刊状态
const [a] = rpc([{ jsonrpc: '2.0', id: 1, method: 'magazine.data', params: { dir } }]);
assert.equal(a.result.data.build.build_id, '2099-01-02-test0001');
assert.ok(a.result.mtime);
assert.ok(a.result.status && typeof a.result.status.pending === 'object', 'data 响应应附带出刊状态');
assert.equal(a.result.status.pending.wire_new, false, 'lane 最新短讯(01-01)不晚于材料日(01-02) → 无待出刊');
// 新短讯到达（01-03）→ 待出刊为真（状态可见性核心场景）
writeFileSync(path.join(path.dirname(dir), '每日短讯', '每日短讯-2099-01-03.html'), '<html></html>');
const [a2] = rpc([{ jsonrpc: '2.0', id: '1b', method: 'magazine.data', params: { dir } }]);
assert.equal(a2.result.status.pending.wire_new, true, '新短讯已到而材料日未跟进 → 新材料待出刊');


// 2 文件清单 + lane 扫描
const [b] = rpc([{ jsonrpc: '2.0', id: 2, method: 'magazine.files', params: { dir } }]);
assert.ok(b.result.files.some((f) => f.name === '今日-2099-01-02.html'));
assert.ok(b.result.files.some((f) => f.name === 'batches/2099-01-02-test0001/今日-2099-01-02.html'), '批次期刊入清单');
assert.equal(b.result.hasData, true);
const lanes = Object.fromEntries((b.result.lanes || []).map((l) => [l.lane, l]));
assert.ok(lanes['每日短讯'] && lanes['每日短讯'].files === 2
  && lanes['每日短讯'].latest.name === '每日短讯-2099-01-03.html', 'lane 应列出全部期刊与最新期');
assert.ok(lanes['深读'] && lanes['深读'].files === 0, 'index.html 不计入 lane 期数');
assert.ok(lanes['知识萃取'] && lanes['知识萃取'].missing === true, '不存在的 lane 目录标 missing');

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
