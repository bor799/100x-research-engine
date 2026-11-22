# 知识杂志出刊管道（源版本）

> 本目录是桌面生产目录 `~/Desktop/知识杂志/模块-知识杂志/` 的**入库源版本**（source of truth for review）。
> 生产目录不是 git 仓库、每日 17:40 出刊直接在其中运行——两处本周允许暂时分叉。
>
> **同步方式**：改动先落生产目录并经真实出刊验证，再以「复制 + 路径参数化」入库；
> 反向（仓库→生产）仅在显式同步时进行。入库版与生产版的唯一刻意差异是路径注入：
> 入库版用环境变量 `KM_HUB`（桌面 hub 根）、`KM_VAULT_ROOT`（Obsidian 根）、`KM_VAULT`（信息源周目录根）、
> `KM_STATE`（管道 state 目录）与 `KM_MOD`（前置检查脚本用模块目录）替代本机绝对路径，
> 缺省值基于当前用户主目录的同名结构。`state/`（批次与运行时产物）与 `logs/` 一律不入库。
>
> 快速验证：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_magazine_module`（28 例，合成数据，不依赖任何真实刊物）。

# 模块-知识杂志（唯一杂志任务 · GLM 5.3）

> 定位：把 ①每日短讯 ②知识萃取周刊 ③Obsidian 日/周信源 三类真实材料融合成
> 「知识杂志」——今日看变化、本周看脉络、判断可回溯到证据。桌面与 Cindy 插件同批。
> 版本：v1.0（2026-11-19）

## 一、系统位置

```
每日短讯看板(08:30,上游) ─┐
100X 知识萃取日报(17:20,上游) ─┼→ collect.py → 编辑层(GLM 5.3) → render.py → 杂志/ + 桌面 index.html
Obsidian 信息源/<周>/笔记 ─┘                                            └→ Cindy 插件读取同批 data
```

- 上游两条调度有独立消费者（GitHub 发布管道 publish.py 同步三条 lane），保持不动；
  本模块只读它们的产物，绝不写上游生产文件。
- 杂志的出刊入口**只有一条** Cindy 计划（glm-5.3，17:40），一次运行同时重建今日与本周，
  周窗口最后一天闭刊（与 100X 周刊 artifact 口径一致，本期 2026-11-W3 为 11-21 周六）。
  手动补跑 = 计划手动触发（GUI 或 schedule_run_now），共用本模块脚本。

## 二、管道五步（与 prompt.md 一致，v2 2026-11-21 起）

1. **采集**：`python3 collect.py` → `state/collect-<今日>.json`
   确定性解析：短讯条目/预测市场行、周刊内嵌 ISSUE JSON（154 篇含 obsidian 回链）、
   周目录笔记 frontmatter、上周周刊（判断增量）、巡视/复盘新旧状态。结构变化解析为空 → FAIL。
2. **编辑**：按 `prompt.md` 写 `state/editor-<今日>.json`（GLM 5.3 的判断层：
   总判断、精选、主题分组、跨日脉络、交叉核验）。URL 只能来自采集集或实抓（fetched）。
3. **渲染预演（门禁）**：`python3 render.py --data … --collect … --dry-run`
   机械校验（分组全量覆盖、来源 containment 分今日/本周两档、独立证据数、kind 枚举）
   → 内容层规范化 → 确定性可读性门禁（`readability_gate.py`：标记泄漏/悬空截断/raw HTML/
   危险协议/结构缺失为硬错误；超长无断句等为警报）→ HTML 层泄漏复查。dry-run 不发布。
4. **语义可读性审核（同一 GLM 5.3 的独立审稿步骤）**：按契约逐条核对「谁做了什么/
   为什么值得看/适用范围与证据限制」，结论经 `readability_review.py --record` 写入
   `state/readability-latest.json`（绑定候选 sha256 + 覆盖字段 + 规则版本；major → fail）。
5. **发布**：`python3 render.py --data … --collect …`
   两道门禁全过才切换批次：staged 全量校验 → 备份上一成功集 → 有序提交
   （data.json 倒数第二、`杂志/data/current-batch.json` 标记最后落盘=提交点）→
   备份刷新为本批。任一门 FAIL / 中断 → 保留上一完整批次并在
   `杂志/data/publication-status.json` 写真实状态；下次运行 `verify_or_heal()` 按
   标记清单自愈恢复上一完整批次。幂等：同 build_id 先核标记完整性再跳过。

**周证据（v2）**：week_view.developments 与 cross_checks 可引用本周各期短讯原始链接
（collect.week_urls，含出处日期/期号，渲染端标注「（MM-DD 短讯）」）；今日条目仍限今日
采集集，跨周/编造一律拦截。week_wires 在周刊 artifact 期号最终确定后按最终窗口收集。
**内容层（v2）**：`content.py` 把富文本字段安全解析为标题/列表/强调/链接/代码的块表示，
先解析再整块截断；两出口（桌面 HTML、Cindy 插件）消费同一块结构，不再各自删标记截断。

## 三、边界与护栏

- **不外发**：不发微信/GitHub/飞书；`模块-发布/publish.py` 只同步三条固定 lane
  （每日短讯/知识萃取/深读），`杂志/` 目录与 hub 根 `index.html` 不在其扫描范围，
  新增文件不会被公开发布扫入。
- **不越权写**：不改上游 HTML/状态；**render.py 无任何 vault 写入分支**，回链
  一律落桌面 `杂志/回链/`（对抗验证：伪造合法 user-confirmed 配置也无法触发
  vault 写入）。Obsidian 仅作可溯源读取；vault 内既有回链文件保留原样不再触碰；
  用户如需 vault 写回须另行明确任务。出刊任务不创建/不修改任何程序配置文件。
- **缺源如实**：每日巡视/每周复盘长期只有旧样本，按缺口展示；今日短讯未出刊时
  基于最新一期并在覆盖区注明。本周周刊缺失时采集降级（coverage 注明，不阻断今日合刊）。
- **必要/可缺分档**：出刊触发只看必要材料（当日短讯 + 周目录笔记变化）；周刊重建属
  重组，不单独触发出刊。`collect.py --plan` 是唯一的材料选择/指纹/闭刊判定来源
  （月末自然截断，如 2026-11-W5 窗口为 11-29~11-30），preRunHook 只是它的瘦壳。
- **唯一状态源**：`杂志/data/current-batch.json`（当前批次标记）+
  `杂志/data/publication-status.json`（最近成功/尝试/跳过）。Cindy 插件与桌面页面
  同批读取；插件据此显示「新材料待出刊 / 上次尝试失败保留旧批 / 批次标记不一致」。
- 单元测试：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_magazine_module`（10 例，
  不读写生产文件）。

## 四、产出物

| 文件 | 说明 |
|---|---|
| `杂志/今日-YYYY-MM-DD.html` | 当日合刊：总判断→重点→按主题全量→市场→深读→交叉核验→覆盖 |
| `杂志/本周-YYYY-MM-Wn.html` | 周脉络：主题×发展/分歧/待核/深读，周内滚动重建 |
| `杂志/data/magazine-data.json` | 同批结构化数据（插件与页面共用，含 build_id/md5 溯源） |
| `~/Desktop/知识杂志/index.html` | 桌面总入口（合刊 + 三条原始栏目） |
| `信息源/<周>/知识杂志回链 <周>.md` | vault 回链索引（可逆、幂等） |
| `state/last-success/` | 最近成功版备份 |

## 五、Cindy 插件与调度

- 插件：`knowledge-magazine`（local，中性 id），源码 `~/projects/cindy-knowledge-magazine`，
  mainView 四视图（今日/本周/主题/往期），工具 `get_magazine` / `get_archive`，
  node worker 只读桌面 `杂志/` 目录，无网络权限。
- 调度：单条计划「知识杂志出刊」（17:40 Asia/Shanghai，claude-code + glm-5.3@glm，
  **persistentSession 持久会话**）；preRunHook v2：材料无变化且非闭刊日→跳过零 token，
  周窗口末日（闭刊日）即使材料无变化也放行并以 render --fresh 重建闭刊版。
- 交叉核验独立性按底层原始证据计票（primary_source/primary_key），同一研究/
  同一底账的多家转述只算一票；obsidian 回链统一 信息源/ 前缀 + .md 并经存在性校验。
