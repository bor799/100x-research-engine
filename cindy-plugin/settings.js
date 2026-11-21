'use strict';
const $ = (s) => document.querySelector(s);
fetch('/kv').then((r) => r.json()).then((cfg) => { $('#dir').value = cfg.magazineDir ?? ''; });
$('#save').onclick = async () => {
  const v = $('#dir').value.trim();
  await fetch('/kv', { method: 'PUT', body: JSON.stringify({ magazineDir: v || null }) });
  new BroadcastChannel('knowledge-magazine-v1').postMessage({ type: 'settings-changed' });
  $('#msg').textContent = '已保存';
  setTimeout(() => $('#msg').textContent = '', 2000);
};
