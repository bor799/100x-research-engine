'use strict';
// 知识杂志 node worker：只读桌面 ~/Desktop/知识杂志/杂志/（无网络、无写入）
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const readline = require('node:readline');

const DEFAULT_MAG = path.join(os.homedir(), 'Desktop', '知识杂志', '杂志');
let MAG = DEFAULT_MAG;
const legacyDataPath = () => path.join(MAG, 'data', 'magazine-data.json');
const POINTER_PREFIX = 'window.MAGAZINE_CURRENT_BATCH = ';

// 唯一读取边界：杂志/batch-pointer.js（与桌面路由页同一指针文件）。
// 指针在 → 读 batches/<build>/data/magazine-data.json；指针缺/坏 → 旧固定路径只读兼容。
function readPointer() {
  try {
    const raw = fs.readFileSync(path.join(MAG, 'batch-pointer.js'), 'utf8').trim();
    return JSON.parse(raw.slice(POINTER_PREFIX.length).replace(/;\s*$/, '').trim());
  } catch (_) { return null; }
}
function resolveBatch() {
  const p = readPointer();
  if (p && p.dir) {
    const d = path.join(MAG, p.dir);
    if (fs.existsSync(d)) return { pointer: p, dataPath: path.join(d, 'magazine-data.json'), legacy: false };
  }
  return { pointer: p, dataPath: legacyDataPath(), legacy: true };
}
const dataPath = () => resolveBatch().dataPath;
// 信息源收口：杂志目录旁的四条原始栏目 lane（只读列清单，不打开不写入）
const LANES = ['每日短讯', '知识萃取', '深读', '研究简报'];

function applyDir(dir) {
  if (dir && typeof dir === 'string' && path.isAbsolute(dir) && fs.existsSync(dir)) MAG = dir;
  else if (dir) throw new Error('配置的杂志目录不存在：' + dir);
}

function readData() {
  const rb = resolveBatch();
  const raw = fs.readFileSync(rb.dataPath, 'utf8');
  const m = JSON.parse(raw);
  const st = fs.statSync(rb.dataPath);
  return { data: m, mtime: st.mtime.toISOString(), bytes: st.size,
           batch: { build_id: rb.pointer ? rb.pointer.build_id : null, legacy: rb.legacy } };
}

function listFiles() {
  const out = [];
  if (fs.existsSync(MAG)) {
    for (const name of fs.readdirSync(MAG)) {
      if (!name.endsWith('.html')) continue;
      const st = fs.statSync(path.join(MAG, name));
      out.push({ name, bytes: st.size, mtime: st.mtime.toISOString() });
    }
  }
  out.sort((a, b) => (a.mtime < b.mtime.toISOString ? 0 : a.mtime < b.mtime ? 1 : -1));
  // 新结构：批次目录内的期刊（含 build 前缀名，点击提示完整路径）
  const batchesDir = path.join(MAG, 'batches');
  if (fs.existsSync(batchesDir)) {
    for (const d of fs.readdirSync(batchesDir)) {
      const dir = path.join(batchesDir, d);
      for (const name of fs.readdirSync(dir)) {
        if (!name.endsWith('.html') || name === 'index.html') continue;
        const st = fs.statSync(path.join(dir, name));
        out.push({ name: `batches/${d}/${name}`, bytes: st.size, mtime: st.mtime.toISOString() });
      }
    }
  }
  out.sort((a, b) => (a.mtime === b.mtime ? 0 : a.mtime < b.mtime ? 1 : -1));
  return out;
}

function listLanes() {
  const hub = path.dirname(MAG);
  return LANES.map((lane) => {
    const dir = path.join(hub, lane);
    if (!fs.existsSync(dir)) return { lane, missing: true };
    const issues = [];
    for (const name of fs.readdirSync(dir)) {
      if (!name.endsWith('.html') || name === 'index.html') continue;
      const st = fs.statSync(path.join(dir, name));
      issues.push({ name, mtime: st.mtime.toISOString() });
    }
    issues.sort((a, b) => (a.mtime === b.mtime ? 0 : a.mtime < b.mtime ? 1 : -1));
    return { lane, dir, files: issues.length,
             latest: issues[0] ? { name: issues[0].name, mtime: issues[0].mtime } : null };
  });
}

function buildStatus(data, batchInfo) {
  // 状态派生（只读）：指针一致性 + 出刊状态文件 + 新材料待出刊（与 data 同一次返回，两个入口同一状态源）
  const st = { marker: null, publication: null, pending: {} };
  const p = batchInfo && batchInfo.pointer;
  if (p) {
    st.marker = { build_id: p.build_id, material_date: p.material_date,
                  match: p.build_id === (data.build && data.build.build_id) };
  }
  try {
    st.publication = JSON.parse(fs.readFileSync(path.join(MAG, 'data', 'publication-status.json'), 'utf8'));
  } catch (_) {}
  const lanes = listLanes();
  const wire = lanes.find((l) => l.lane === '每日短讯');
  const wm = wire && wire.latest && /^每日短讯-(\d{4}-\d{2}-\d{2})\.html$/.exec(wire.latest.name);
  st.pending.wire_new = !!(wm && wm[1] > (data.material_date || ''));
  const wk = lanes.find((l) => l.lane === '知识萃取');
  const km = wk && wk.latest && /^知识萃取周刊 (\d{4}-\d{2}-W\d)\.html$/.exec(wk.latest.name);
  st.pending.weekly_new = !!(km && km[1] > (data.week || ''));
  const builtMs = Date.parse(((data.build && data.build.built_at) || '') + '+08:00');
  st.pending.lanes_updated = Number.isFinite(builtMs)
    ? lanes.filter((l) => l.latest && Date.parse(l.latest.mtime) > builtMs).map((l) => l.lane)
    : [];
  return st;
}

function reply(message) { process.stdout.write(JSON.stringify(message) + '\n'); }

readline.createInterface({ input: process.stdin }).on('line', (line) => {
  let req;
  try { req = JSON.parse(line); } catch (_) { return; }
  if (req.id == null) return;
  try {
    if (req.params && typeof req.params === 'object' && 'dir' in req.params) applyDir(req.params.dir);
    switch (req.method) {
      case 'magazine.data': {
        const { data, mtime, bytes, batch } = readData();
        reply({ jsonrpc: '2.0', id: req.id, result: { data, mtime, bytes, batch,
                                                      status: buildStatus(data, batch ? { pointer: batch.pointer || readPointer() } : { pointer: readPointer() }) } });
        break;
      }
      case 'magazine.files': {
        const files = listFiles();
        const hasData = fs.existsSync(dataPath());
        reply({ jsonrpc: '2.0', id: req.id, result: { files, hasData, dir: MAG, lanes: listLanes() } });
        break;
      }
      case 'magazine.defaultDir': {
        reply({ jsonrpc: '2.0', id: req.id, result: { defaultDir: DEFAULT_MAG, currentDir: MAG } });
        break;
      }
      default:
        reply({ jsonrpc: '2.0', id: req.id, error: { code: -32601, message: '未知方法，只支持 magazine.data / magazine.files / magazine.defaultDir' } });
    }
  } catch (e) {
    reply({ jsonrpc: '2.0', id: req.id, error: { code: -32000, message: e.code === 'ENOENT' ? ('尚未找到杂志数据（' + MAG + ' 下缺 data/magazine-data.json），请先在杂志管道出刊一期，或在插件设置里改目录') : e.message } });
  }
});
