# 知识杂志 · Cindy 插件（knowledge-magazine）

在 Cindy 内阅读「知识杂志」的插件：今日变化、本周脉络、主题与往期四个视角，
判断与出处可回溯，与桌面杂志同批次。零网络权限；自带 Node worker 只读杂志数据目录。
主视图为固定乳白纸底（#FBFAF8，与桌面阅读页一致），不跟随宿主壁纸/主题透明化。

## 功能

- **mainView 四视图**：今日（总判断→重点→按主题全量→市场→深读→交叉核验→覆盖缺口）、
  本周（主题×发展/分歧/待核/深读，跨日对照）、主题（跳转本周对应区块）、往期（桌面存档清单 + 原始栏目 lane 清单）。
- **原始栏目收口**：往期视图与覆盖区列出杂志目录旁的四条上游栏目
  （每日短讯/知识萃取/深读/研究简报）的期数与最新期，worker 实时只读扫描，
  不依赖出刊批次；全部信息源在同一入口内可查。
- **Agent 工具**：`get_magazine`（最新一期摘要：批次、总判断、精选与来源、主题、
  交叉核验、覆盖缺口）、`get_archive`（各期文件清单 + 四条栏目 lane 清单）。
- **来源打开**：https 原文新窗口打开；`obsidian://` 链接可直接点击，也提供「复制链接」
  按钮作为兜底（不同宿主对外部协议的处理可能不同）。
- **设置页**：可配置杂志数据目录（默认 `~/Desktop/知识杂志/杂志`），保存后立即生效，
  插件对目录只读。

## 数据读取契约

worker 只读一个目录（默认 `~/Desktop/知识杂志/杂志`，可在插件设置覆盖）：

```
<目录>/
├── data/magazine-data.json   # 唯一数据面（结构见下）
├── 今日-YYYY-MM-DD.html      # 各期页面（往期视图仅列文件名）
└── 本周-YYYY-MM-Wn.html
```

`magazine-data.json` 关键字段（`schema: knowledge-magazine/1`）：

| 字段 | 说明 |
|---|---|
| `build` | `{build_id, built_at, model}` 批次溯源，页面与数据同批 |
| `material_date` / `week` | 材料日与周标签（W=ceil(day/7) 口径） |
| `today.verdict` / `picks` / `groups` / `market_rows` / `new_notes` / `cross_checks` | 今日视图全部内容 |
| `week_view.topics[]` | `{slug,title,thread,developments[{date,event,sources}],counterpoints,to_verify,deep_read_ids}` |
| `coverage` | 三源覆盖与缺口（缺源如实展示）；v1.3 起含 `lanes[]` 栏目清单（采集时快照，插件侧另有实时扫描） |
| `today.cross_checks[]` | `{claim,primary_source,status,sources[{url,label,independent,primary_key}],verdict,counterpoint}`；独立性按底层原始证据计票，同一研究/底账的多家转述只算一票 |

生成该数据面的管道不属于本插件（上游为杂志构建任务）；只要目录内有合法
`magazine-data.json`，插件即可独立工作。

## 安装（Cindy Forge）

```
# 在插件源码目录
ghost_forge_pack   → knowledge-magazine-<version>.cindy
ghost_forge_install  # 或在 Cindy 设置→插件 里导入 .cindy
```

权限面：`node`（随包 Node 工作进程，只读上述目录）；无网络、无 fs 能力声明。

## 测试

```
node tests/test-worker.mjs   # 合成数据 fixture，不依赖任何真实刊物
```

## 本地 UI 冒烟

`magazine.js` 支持 `window.__MAG_SHIM__` 注入钩子：页面提供
`(action) => ({ok:true, result:{data, mtime}} | {ok:true, result:{files}})` 即可
脱离宿主渲染四个视图，用于开发期冒烟；宿主内实际走 BroadcastChannel ↔ main.js ↔
Node worker 链路。
