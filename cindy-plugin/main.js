'use strict';
// 知识杂志 main.js：工具调用（get_magazine / get_archive）+ 页面数据服务（BroadcastChannel）
// 杂志目录默认 ~/Desktop/知识杂志/杂志，可经插件设置（/kv magazineDir）覆盖，只读。
const channel = new BroadcastChannel('knowledge-magazine-v1');
const cache = { data: null, at: 0 };
const replies = new Map();
let magazineDir = null; // null = worker 默认目录

async function readKv() {
  try {
    const cfg = await (await fetch('/kv')).json();
    magazineDir = cfg.magazineDir || null;
  } catch (_) { magazineDir = null; }
}

async function nodeRequest(method, params = {}, callId) {
  const r = await cindy.node.request({
    method, params: { ...params, ...(magazineDir ? { dir: magazineDir } : {}) },
    timeoutMs: 20000, ...(callId ? { callId } : {}),
  });
  if (!r.ok) throw new Error(r.message || 'Node 请求失败');
  return r.result;
}

async function loadFresh(force) {
  if (!force && cache.data && Date.now() - cache.at < 60_000) return cache.data;
  const { data, mtime } = await nodeRequest('magazine.data');
  cache.data = { data, mtime };
  cache.at = Date.now();
  return cache.data;
}

function compact(d) {
  const t = d.today || {}, w = d.week_view || {};
  return {
    build: d.build, material_date: d.material_date, week: d.week,
    verdict: t.verdict,
    picks: (t.picks || []).map(p => ({ title: p.title, why: p.why, why_md: p.why_md, kind: p.kind,
                                       status: p.status, sources: p.sources })),
    topics: (w.topics || []).map(x => ({ slug: x.slug, title: x.title, thread: x.thread })),
    what_grew: w.what_grew,
    coverage: d.coverage,
    cross_checks: (t.cross_checks || []).map(c => ({ claim: c.claim, status: c.status,
                                                     primary_source: c.primary_source })),
  };
}

channel.onmessage = async ({ data: m }) => {
  if (m?.type === 'settings-changed') { cache.data = null; return; }
  if (m?.type !== 'mag-request' || typeof m.id !== 'string' || !['data', 'files', 'refresh'].includes(m.action)) return;
  if (replies.has(m.id)) { const c = replies.get(m.id); if (c) channel.postMessage(c); return; }
  replies.set(m.id, null);
  let answer;
  try {
    if (m.action === 'files') {
      answer = { type: 'mag-response', id: m.id, ok: true, result: await nodeRequest('magazine.files') };
    } else {
      answer = { type: 'mag-response', id: m.id, ok: true, result: await loadFresh(m.action === 'refresh') };
    }
  } catch (e) {
    answer = { type: 'mag-response', id: m.id, ok: false, error: '读取杂志失败：' + e.message };
  }
  replies.set(m.id, answer);
  if (replies.size > 60) replies.delete(replies.keys().next().value);
  channel.postMessage(answer);
};

cindy.onHostMessage(async (msg) => {
  if (msg.type !== 'tool-call') return;
  try {
    await readKv();
    if (msg.tool === 'get_magazine') {
      const { data } = await loadFresh(false);
      await cindy.send({ type: 'tool-result', callId: msg.callId, ok: true, result: compact(data) });
    } else if (msg.tool === 'get_archive') {
      const files = await nodeRequest('magazine.files', {}, msg.callId);
      await cindy.send({ type: 'tool-result', callId: msg.callId, ok: true, result: files });
    } else {
      throw new Error('未知工具');
    }
  } catch (e) {
    await cindy.send({ type: 'tool-result', callId: msg.callId, ok: false,
                       message: '知识杂志读取失败：' + e.message + '。可先出刊一期或在插件设置里核对目录。' });
  }
});

readKv();
