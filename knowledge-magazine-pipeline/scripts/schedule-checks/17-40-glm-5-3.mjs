#!/usr/bin/env node
// 知识杂志出刊前置检查 v3（瘦壳）：材料选择/指纹/闭刊窗口全部由唯一收集规划器
// collect.py --plan 决定（与采集同一份确定性逻辑，不再有第二份期号推导）。
// 放行：publish / closure_fresh。跳过：skip（写状态后 exit 2）。阻断：异常 exit 1。
import { execFileSync } from 'node:child_process';

const MOD = process.env.KM_MOD || require('node:path').join(require('node:os').homedir(), 'Desktop', '知识杂志', '模块-知识杂志');

function run(args) {
  return execFileSync('python3', [MOD + '/collect.py', ...args], {
    encoding: 'utf8', timeout: 120000,
    env: { ...process.env },
  }).trim();
}

try {
  const raw = run(['--plan']);
  const plan = JSON.parse(raw);
  if (!plan.ok) { console.error('plan 未通过: ' + raw); process.exit(1); }
  if (plan.decision === 'skip') {
    try { run(['--record-skip', JSON.stringify(plan)]); } catch (e) { /* 状态写失败不改变跳过决策 */ }
    console.log('CINDY_PRECHECK_OK');
    console.log(`跳过本轮：${plan.reason}（短讯 ${plan.wire_date} / ${plan.week} 窗口 ${plan.week_window.first}~${plan.week_window.last} / 周刊 degraded=${plan.weekly.degraded}）`);
    process.exit(2);
  }
  console.log('CINDY_PRECHECK_OK');
  console.log(`放行（${plan.decision}）：${plan.reason}（短讯 ${plan.wire_date} / ${plan.week} 窗口 ${plan.week_window.first}~${plan.week_window.last}${plan.decision === 'closure_fresh' ? '，出刊后加 --fresh' : ''}）`);
  process.exit(0);
} catch (e) {
  console.error('预检异常，阻断本轮（次日自愈）: ' + String(e.message).slice(0, 200));
  process.exit(1);
}
