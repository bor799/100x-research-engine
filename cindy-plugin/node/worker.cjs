'use strict';
// 知识杂志 node worker：只读桌面 ~/Desktop/知识杂志/杂志/（无网络、无写入）
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const readline = require('node:readline');

const DEFAULT_MAG = path.join(os.homedir(), 'Desktop', '知识杂志', '杂志');
let MAG = DEFAULT_MAG;
const dataPath = () => path.join(MAG, 'data', 'magazine-data.json');
// 信息源收口：杂志目录旁的四条原始栏目 lane（只读列清单，不打开不写入）
const LANES = ['每日短讯', '知识萃取', '深读', '研究简报'];

function applyDir(dir) {
  if (dir && typeof dir === 'string' && path.isAbsolute(dir) && fs.existsSync(dir)) MAG = dir;
  else if (dir) throw new Error('配置的杂志目录不存在：' + dir);
}

function readData() {
  const DATA = dataPath();
  const raw = fs.readFileSync(DATA, 'utf8');
  const m = JSON.parse(raw);
  const st = fs.statSync(DATA);
  return { data: m, mtime: st.mtime.toISOString(), bytes: st.size };
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

function reply(message) { process.stdout.write(JSON.stringify(message) + '\n'); }

readline.createInterface({ input: process.stdin }).on('line', (line) => {
  let req;
  try { req = JSON.parse(line); } catch (_) { return; }
  if (req.id == null) return;
  try {
    if (req.params && typeof req.params === 'object' && 'dir' in req.params) applyDir(req.params.dir);
    switch (req.method) {
      case 'magazine.data': {
        const { data, mtime, bytes } = readData();
        reply({ jsonrpc: '2.0', id: req.id, result: { data, mtime, bytes } });
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
