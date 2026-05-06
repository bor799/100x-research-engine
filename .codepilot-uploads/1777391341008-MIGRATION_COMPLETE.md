# V2 → V3 迁移完成报告

## 迁移日期
2026-04-28

## 迁移状态
✅ **已完成**

---

## 一、核心配置迁移

### 1.1 环境变量 (.env)
| 项目 | V2 值 | V3 配置 | 状态 |
|------|-------|---------|------|
| ZHIPU_API_KEY | `a0506...` | `ZHIPU_API_KEY` | ✅ |
| TELEGRAM_BOT_TOKEN | `82167...` | `TELEGRAM_BOT_TOKEN` | ✅ |
| TELEGRAM_CHAT_ID | `7934670950` | `TELEGRAM_ADMIN_CHAT_ID` | ✅ |

### 1.2 LLM 配置
| V2 路由 | V3 配置 | 值 | 状态 |
|---------|---------|-----|------|
| `llm.router.quality_filter.model` | `llm.scoring_model` | `GLM-4.5` | ✅ |
| `llm.router.deep_analysis.model` | `llm.extraction_model` | `GLM-4.7` | ✅ |
| `llm.router.telegram_format.model` | `llm.telegram_brief_model` | `GLM-4.5-Air` | ✅ |
| `llm.api_base` | `llm.api_base` | `https://open.bigmodel.cn/api/coding/paas/v4` | ✅ |

### 1.3 输出配置
| V2 | V3 | 状态 |
|----|----|------|
| `output.obsidian_root: ~/Documents/Obsidian Vault/信息源` | `outputs.obsidian_root` | ✅ |
| `output.obsidian_folder: AI进展` | `outputs.obsidian_subdir` | ✅ |

---

## 二、信息源迁移

### 2.1 RSS 信息源
- V2: 70+ 信息源
- V3: 39 个高优先级信息源（已嵌入 config.local.yaml）
- 状态: ✅

### 2.2 信息源列表
- **AI 进展**: The Batch, Gary Marcus, Dwarkesh Patel, Minimaxir, Gwern, Experimental History
- **AI 工程**: Simon Willison
- **技术资讯**: HackerNews, Jeff Geerling, Daring Fireball, Mitchelh
- **安全/逆向**: Krebs on Security, lcamtuf
- **创业/商业**: Steve Blank, Pluralistic
- **编程/语言**: Antirez, Miguel Grinberg
- **系统/底层**: Old New Thing
- **前端/设计**: Overreacted
- **综合博客**: Paul Graham, Matklad, John D Cook, Hillel Wayne, Geoffrey Litt, 等 20+ 高质量博客

---

## 三、Agent Reach 多渠道抓取

### 3.1 支持的平台
| 平台 | V2 Channel | V3 状态 |
|------|-----------|---------|
| YouTube | YouTubeChannelAdapter | ✅ |
| Twitter/X | TwitterChannelAdapter | ✅ |
| 微信公众号 | WechatChannelAdapter | ✅ |
| 小宇宙播客 | XiaoyuzhouChannelAdapter | ✅ |
| 通用网页 | WebChannelAdapter (Jina) | ✅ |

### 3.2 实现方式
- V3 新增 `fetchers/multi_channel.py`
- 复用 V2 的 `ar_channels` 实现
- 输出符合 V3 的 `FetchedContent` 格式

---

## 四、定时任务配置

### 4.1 Cron 任务
```bash
# 每天 05:30 运行夜间任务
30 5 * * * /path/to/v3/scripts/nightly_job.sh
```

### 4.2 夜间任务流程
1. **获取 RSS 源** - 从 39 个信息源获取最新文章
2. **处理队列** - Worker 处理队列中的文章
3. **输出结果** - Obsidian Markdown + Telegram 通知

### 4.3 安装命令
```bash
cd /path/to/v3
./scripts/install_crontab.sh
```

---

## 五、使用方式

### 5.1 单次命令
```bash
# 运行 RSS 获取
./scripts/run.sh rss

# 处理单个 URL
./scripts/run.sh url <URL>

# 启动 Worker
./scripts/run.sh worker
```

### 5.2 守护进程
```bash
# 启动后台守护进程
./scripts/run.sh start

# 停止
./scripts/run.sh stop

# 状态
./scripts/run.sh status
```

### 5.3 夜间任务
```bash
# 手动执行夜间任务
./scripts/run.sh nightly

# 或直接调用
./scripts/nightly_job.sh
```

### 5.4 系统诊断
```bash
# 运行系统诊断
./scripts/run.sh doctor

# 测试配置
./scripts/run.sh test
```

---

## 六、验证结果

### 6.1 配置加载
```
✅ 配置加载成功
  - LLM Provider: zhipu
  - Scoring Model: GLM-4.5
  - Extraction Model: GLM-4.7
  - Sources: 39 个
  - Live Mode: True
  - Scheduler: True
  - Interval: 43200s (12h)
```

### 6.2 文件结构
```
v3/
├── .env                          # ✅ 环境变量
├── config/
│   ├── config.example.yaml       # 默认配置
│   ├── config.local.yaml         # ✅ 本地配置
│   └── sources.yaml              # ✅ 信息源备份
├── scripts/
│   ├── run.sh                    # ✅ 主运行脚本
│   ├── nightly_job.sh            # ✅ 夜间任务
│   └── install_crontab.sh        # ✅ Cron 安装
└── src/knowledge_extractor_v3/
    ├── fetchers/
    │   ├── base.py
    │   ├── web.py
    │   ├── multi_channel.py      # ✅ Agent Reach
    │   └── __init__.py
    └── ...
```

---

## 七、下一步

1. **安装 Cron 任务**
   ```bash
   cd /path/to/v3
   ./scripts/install_crontab.sh
   ```

2. **验证夜间任务**
   ```bash
   ./scripts/nightly_job.sh
   ```

3. **停止 V2 运行**（切换后）
   ```bash
   cd /path/to/v2
   ./run.sh stop
   ```

4. **归档 V2**（稳定运行后）
   ```bash
   mv v2 v2.deprecated
   ```

---

## 八、注意事项

1. **Token 唯一性**: 同一 token 不能同时被 V2 和 V3 使用
2. **State 隔离**: V2 和 V3 使用不同的 state 目录
3. **环境变量优先**: 敏感信息优先用环境变量管理
4. **代理配置**: 代理配置留空，使用 TUN 或自动探测
5. **依赖包**: 需要安装 `pyyaml`、`httpx`、`feedparser`

---

## 九、回滚方案

如果 V3 出现问题：

1. **停止 V3**: `./scripts/run.sh stop`
2. **启动 V2**: `cd ../v2 && ./run.sh start`
3. **配置回滚**: 使用备份的 V2 `config.yaml`
