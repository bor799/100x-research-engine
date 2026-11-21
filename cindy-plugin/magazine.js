'use strict';
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = { tab: 'today', data: null, files: null, channel: null, pending: new Map() };

const KIND = { fact: '事实', opinion: '观点', inference: '推断' };
const VSTAT = { verified_multi: '≥2 条不同原始证据', single_source: '主证据单源（多家转述不计两票）', unverified: '未核验' };

function setStatus(msg) { $('#status').textContent = msg; }

function request(action) {
  return new Promise((resolve, reject) => {
    const shim = window.__MAG_SHIM__; // 本地 UI 冒烟钩子：页面注入后直返数据，不经 BroadcastChannel
    if (shim) { setTimeout(() => { try { resolve(shim(action)); } catch (e) { reject(e); } }, 10); return; }
    if (!state.channel) { reject(new Error('当前环境无法连接数据后台')); return; }
    const id = crypto.randomUUID();
    const timer = setTimeout(() => { state.pending.delete(id); reject(new Error('请求超时')); }, 25000);
    state.pending.set(id, { resolve, reject, timer });
    state.channel.postMessage({ type: 'mag-request', id, action });
  });
}

function srcLinks(sources = []) {
  return sources.map(s => {
    const tag = s.independent ? '（独立）' : (s.fetched ? '（实抓）' : '');
    const isObs = String(s.url || '').startsWith('obsidian://');
    const label = esc(s.label || '来源');
    if (isObs) {
      return `<a href="${esc(s.url)}">${label}</a>${tag}<button class="copy-uri" data-uri="${esc(s.uri || s.url)}" title="点击复制 Obsidian 链接">复制链接</button>`;
    }
    return `<a href="${esc(s.url)}" target="_blank">${label}</a>${tag}`;
  }).join(' ');
}

function brief(b, n = 380) {
  b = String(b || '').trim().replace(/^#{1,4} /gm, '');
  return b.length > n ? b.slice(0, n) + '…' : b;
}

function badge(kind) { return kind ? `<span class="badge ${kind}">${KIND[kind] || kind}</span>` : ''; }
function verifyBadge(p) { return p === 'unverified' || p === 'single_source' ? '<span class="badge verify">待核</span>' : ''; }

function renderToday() {
  const d = state.data, t = d.today || {};
  const picks = (t.picks || []).map(p => `
    <div class="pick"><div class="t">${esc(p.title)}</div>
    <div class="why">${esc(p.why)}</div>
    <div class="meta-row">${badge(p.kind)}${verifyBadge(p.status)}${srcLinks(p.sources)}</div></div>`).join('');
  const groups = (t.groups || []).map(g => {
    const items = g.items.map(it => `
      <div class="item"><div class="t"><a href="${esc(it.href)}" target="_blank">${esc(it.title)}</a>
      ${it.original ? `<span class="en">${esc(it.original)}</span>` : ''}</div>
      ${it.why ? `<div class="m">${esc(it.why)}</div>` : ''}
      <div class="meta-row">${badge(it.kind)}${(it.meta || []).map(esc).join(' · ')}</div>
      ${it.note ? `<div class="note"><b>编辑注</b> ${esc(it.note)}</div>` : ''}</div>`).join('');
    return `<h2 class="sec">${esc(g.title)}</h2>${g.thread ? `<p class="thread">${esc(g.thread)}</p>` : ''}${items}`;
  }).join('');
  const odds = (t.market_rows || []).length ? `
    <h2 class="sec">市场变化<span class="sub">预测市场</span></h2>
    <table class="odds"><thead><tr><th>问题</th><th>概率</th><th>趋势</th><th>成交量</th></tr></thead><tbody>
    ${(t.market_rows || []).map(r => `<tr><td><a href="${esc(r.href)}" target="_blank" title="${esc(r.question)}">${esc(r.title)}</a></td><td>${esc(r.prob)}</td><td>${esc(r.trend)}</td><td>${esc(r.vol)}</td></tr>`).join('')}
    </tbody></table>` : '';
  const deeps = (t.new_notes || []).length ? `
    <h2 class="sec">值得深读<span class="sub">今日新增萃取 · ${t.new_notes.length} 篇</span></h2>
    ${(t.new_notes || []).map(a => `
      <div class="deep"><div class="t">${esc(a.title)}</div><div class="brief">${esc(brief(a.brief))}</div>
      <div class="meta-row">${esc(a.signal_tier || '')} · ${esc(a.final_score ?? '')}
      ${a.url ? ` · <a href="${esc(a.url)}" target="_blank">原始出处</a>` : ''}
      ${a.obsidian_url ? ` · <a href="${esc(a.obsidian_url)}">Obsidian 原文</a><button class="copy-uri" data-uri="${esc(a.obsidian_url)}">复制链接</button>` : ''}</div></div>`).join('')}` : '';
  const checks = (t.cross_checks || []).length ? `
    <h2 class="sec">交叉核验<span class="sub">重要判断的证据与反例</span></h2>
    ${(t.cross_checks || []).map(c => `
      <div class="check"><div class="claim">${esc(c.claim)}</div>
      ${c.primary_source ? `<div class="row"><b>主证据</b> ${esc(c.primary_source)}</div>` : ''}
      <div class="row"><b>证据</b> ${srcLinks(c.sources)}</div>
      <div class="row"><b>核验</b> ${esc(c.verdict || '')}（${VSTAT[c.status] || c.status}）</div>
      <div class="row"><b>分歧/限制</b> ${esc(c.counterpoint || '')}</div></div>`).join('')}` : '';
  return `<div class="verdict">${esc(t.verdict)}</div>
    <h2 class="sec">今日重点<span class="sub">${(t.picks || []).length} 条</span></h2>${picks}
    <h2 class="sec">变了什么<span class="sub">按主题分组 · 全量短讯</span></h2>${groups}${odds}${deeps}${checks}${coverageHtml(d)}`;
}

function renderWeek() {
  const d = state.data, w = d.week_view || {};
  const topics = (w.topics || []).map(t => {
    const arts = {};
    ((w.deep_reads || []).forEach(a => arts[a.article_id] = a));
    const devs = (t.developments || []).map(x => `
      <div class="dev"><span class="d">${esc((x.date || '').slice(5))}</span>
      <div><div>${esc(x.event)}</div><div class="s">${srcLinks(x.sources)}</div></div></div>`).join('');
    const counters = (t.counterpoints || []).map(c => `<li>${esc(c)}</li>`).join('');
    const verifs = (t.to_verify || []).map(c => `<li>${esc(c)}</li>`).join('');
    const deeps = (t.deep_read_ids || []).filter(i => arts[i]).map(i => `
      <div class="deep"><div class="t">${esc(arts[i].title)}</div><div class="brief">${esc(brief(arts[i].brief, 260))}</div>
      <div class="meta-row">${arts[i].obsidian_url ? `<a href="${esc(arts[i].obsidian_url)}">Obsidian 原文</a><button class="copy-uri" data-uri="${esc(arts[i].obsidian_url)}">复制链接</button>` : ''}
      ${arts[i].url ? ` · <a href="${esc(arts[i].url)}" target="_blank">原始出处</a>` : ''}</div></div>`).join('');
    return `<div class="topic" id="topic-${esc(t.slug)}"><h3>${esc(t.title)}</h3>
      <p class="thread">${esc(t.thread)}</p>${devs}
      ${counters ? `<details open><summary>证据与分歧</summary><ul>${counters}</ul></details>` : ''}
      ${verifs ? `<details><summary>仍待验证</summary><ul>${verifs}</ul></details>` : ''}
      ${deeps ? `<details><summary>值得展开的深读（${(t.deep_read_ids || []).length}）</summary>${deeps}</details>` : ''}
      </div>`;
  }).join('');
  const wires = ((w.week_wires || []).map(x => `<li>${esc((x.date || '').slice(5))}：${esc(x.oneline_take)}</li>`).join(''));
  return `<div class="verdict">${esc(w.what_grew || '')}</div>
    <h2 class="sec">主题<span class="sub">跨日对照 · 发展 / 分歧 / 待核</span></h2>${topics}
    ${wires ? `<details><summary>本周各日短讯一句话</summary><ul>${wires}</ul></details>` : ''}`;
}

function renderTopics() {
  const w = state.data.week_view || {};
  const rows = (w.topics || []).map(t => `
    <div class="topic"><h3><a href="#" data-goto="week:#topic-${esc(t.slug)}">${esc(t.title)}</a></h3>
    <p class="thread">${esc(t.thread)}</p>
    <div class="meta-row">${(t.developments || []).length} 条发展 · ${(t.counterpoints || []).length} 条分歧 · ${(t.to_verify || []).length} 条待核</div></div>`).join('');
  return `<h2 class="sec">本期主题<span class="sub">${esc(state.data.week)}</span></h2>${rows}
    <p class="thread">点击主题进入本周脉络中的对应区块。</p>`;
}

function renderArchive() {
  const files = state.files?.files || [];
  const rows = files.map(f => `<li><a href="#" data-open-file="${esc(f.name)}">${esc(f.name)}</a>
    <span class="meta-row">${(f.mtime || '').slice(0, 16).replace('T', ' ')}</span></li>`).join('');
  const lanes = (state.files?.lanes || []).map(l => {
    if (l.missing) return `<li>${esc(l.lane)}<span class="meta-row">目录缺失</span></li>`;
    const latest = l.latest ? `${(l.latest.mtime || '').slice(0, 10)} · ${esc(l.latest.name)}` : '暂无期刊';
    return `<li><a href="#" data-lane="${esc(l.lane)}">${esc(l.lane)}</a>
      <span class="meta-row">${l.files} 期 · 最新 ${latest}</span></li>`;
  }).join('');
  return `<h2 class="sec">往期<span class="sub">桌面存档</span></h2>
    <p class="thread">全部期刊存于 <code>~/Desktop/知识杂志/杂志/</code>，点击可在 Finder 中查看对应文件。</p>
    <ul class="arch">${rows || '<li>暂无存档</li>'}</ul>
    <h2 class="sec">原始栏目<span class="sub">信息源收口 · 实时扫描</span></h2>
    <p class="thread">四条上游栏目都在 <code>~/Desktop/知识杂志/</code> 下，与本页同一目录树；此处清单即时扫描，不依赖出刊批次。</p>
    <ul class="arch">${lanes || '<li>暂无栏目</li>'}</ul>
    <p class="thread">桌面总入口：<code>~/Desktop/知识杂志/index.html</code>（与本页同一批次）。</p>`;
}

function coverageHtml(d) {
  const c = d.coverage || {};
  const items = [
    c.wire ? `每日短讯 ${esc(c.wire.date)}（${c.wire.items} 条）` : '',
    c.weekly ? `知识萃取周刊 ${esc(c.weekly.week)}（${c.weekly.articles} 篇）` : '',
    c.obsidian_week ? `Obsidian 周信源 ${c.obsidian_week.notes} 篇笔记` : '',
    ...(c.lanes || []).map(x => x.missing
      ? `栏目 ${esc(x.lane)}：目录缺失`
      : `栏目 ${esc(x.lane)}：${x.files} 期，最新 ${esc((x.latest && x.latest.mtime || '').slice(0, 10))}`),
    ...(c.missing || []).map(x => `缺源：${esc(x)}`),
    ...(c.notes || []).map(x => esc(x)),
  ].filter(Boolean);
  return `<div class="coverage"><h4>覆盖与缺口</h4><ul>${items.map(i => `<li>${i}</li>`).join('')}</ul></div>`;
}

function paint() {
  const map = { today: renderToday, week: renderWeek, topics: renderTopics, archive: renderArchive };
  try {
    $('#workspace').innerHTML = map[state.tab]();
    document.querySelectorAll('.tabs button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === state.tab)));
    setStatus(`视角：${{today: '今日', week: '本周', topics: '主题', archive: '往期'}[state.tab]} · 批次 ${state.data?.build?.build_id || '—'} · 数据时点 ${state.dataMtime || '—'}`);
  } catch (e) {
    $('#workspace').innerHTML = `<div class="empty">渲染失败：${esc(e.message)}</div>`;
  }
}

async function load(action) {
  try {
    if (action !== 'files') {
      const r = await request(action === 'refresh' ? 'refresh' : 'data');
      if (!r.ok) throw new Error(r.error);
      state.data = r.result.data;
      state.dataMtime = (r.result.mtime || '').slice(0, 16).replace('T', ' ');
      const b = state.data.build || {};
      $('#build-info').textContent = `材料日 ${state.data.material_date} · 批次 ${b.build_id || '—'} · ${b.model || ''}`;
      $('#data-time').textContent = `数据时点 ${state.dataMtime}`;
    }
    const f = await request('files');
    if (f.ok) state.files = f.result;
    paint();
  } catch (e) {
    $('#workspace').innerHTML = `<div class="empty">${esc(e.message)}<br><br>请确认桌面 <code>~/Desktop/知识杂志/杂志/data/magazine-data.json</code> 存在（先出刊一期），或点击「重读最新一期」重试。</div>`;
    setStatus('读取失败');
  }
}

document.addEventListener('click', (ev) => {
  const copy = ev.target.closest('.copy-uri');
  if (copy) {
    ev.preventDefault();
    const uri = copy.dataset.uri;
    navigator.clipboard?.writeText(uri).then(
      () => { copy.textContent = '已复制'; setTimeout(() => copy.textContent = '复制链接', 1500); },
      () => { copy.textContent = '复制失败'; });
    return;
  }
  const goto = ev.target.closest('[data-goto]');
  if (goto) {
    ev.preventDefault();
    const [tab, hash] = goto.dataset.goto.split(':');
    state.tab = tab;
    paint();
    if (hash) { const el = document.getElementById(hash.slice(1)); el?.scrollIntoView({ behavior: 'smooth' }); el?.animate?.([{ outline: '2px solid #a63228' }, { outline: 'transparent' }], { duration: 1600 }); }
    return;
  }
  const openFile = ev.target.closest('[data-open-file]');
  if (openFile) {
    ev.preventDefault();
    const name = openFile.dataset.openFile;
    setStatus(`文件位于 ~/Desktop/知识杂志/杂志/${name}（插件内不直接打开本地 HTML，请到桌面查看）`);
    return;
  }
  const lane = ev.target.closest('[data-lane]');
  if (lane) {
    ev.preventDefault();
    const name = lane.dataset.lane;
    setStatus(`栏目目录 ~/Desktop/知识杂志/${name}/（插件内不直接打开本地 HTML，请到桌面查看）`);
  }
});

document.addEventListener('click', (ev) => {
  const tab = ev.target.closest('.tabs button');
  if (!tab) return;
  state.tab = tab.dataset.tab;
  paint();
});

$('#refresh').addEventListener('click', () => { setStatus('正在重读…'); load('refresh'); });

try {
  state.channel = new BroadcastChannel('knowledge-magazine-v1');
  state.channel.onmessage = ({ data: m }) => {
    if (m?.type !== 'mag-response') return;
    const p = state.pending.get(m.id);
    if (!p) return;
    clearTimeout(p.timer);
    state.pending.delete(m.id);
    m.ok ? p.resolve(m) : p.reject(new Error(m.error || '后台失败'));
  };
} catch (_) { state.channel = null; }
load('data');
