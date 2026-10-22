# FocusLedger CLI 操作说明

本文档面向当前 terminal-first 版本，说明 `python -m focusledger ...` 的常用操作。

两点通用说明：

- macOS / Linux 上请把示例中的 `python` 替换为 `python3`（系统自带的 `python` 可能不存在或指向 Python 2）。
- macOS 推荐先 `pip install -e .` 安装（会带上 PyObjC，启用完整的窗口句柄与隐私追踪）；不安装直接从源码运行也可以，但会降级到 subprocess 采集路径，启动日志里会有对应 warning，详见 README“平台支持状态”。

## 1. 初始化

第一次使用先初始化配置和数据库：

```bash
python -m focusledger init
```

查看配置文件路径：

```bash
python -m focusledger config path
```

查看数据库路径：

```bash
python -m focusledger db path
```

## 2. 启动采集

推荐后台启动：

```bash
python -m focusledger start --background
```

后台启动后，当前 terminal 可以继续输入其他命令。关闭 terminal 后，agent 仍会继续运行。

前台调试启动：

```bash
python -m focusledger start
```

前台模式会占用当前 terminal，按 `Ctrl+C` 停止。

只采集一次：

```bash
python -m focusledger start --once
```

采集固定次数后退出：

```bash
python -m focusledger start --max-samples 10
```

## 3. 停止采集

请求正在运行的 agent 停止：

```bash
python -m focusledger stop
```

agent 会在下一次 poll 时退出。

## 4. 查看运行状态

```bash
python -m focusledger status
```

重点看：

```text
status
timestamp
age_seconds
stale
pid
device_id
```

如果显示：

```text
status: running
stale: false
```

说明 agent 最近仍在正常 heartbeat。

如果显示：

```text
status: running
stale: true
```

说明数据库里最后一次 heartbeat 已经过期，常见原因是电脑重启、agent 崩溃、或者之前的进程已经不存在。

## 5. 查看活动时间线

看今天：

```bash
python -m focusledger timeline --today
```

看指定日期：

```bash
python -m focusledger timeline --date 2026-07-09
```

输出示例：

```text
21:35:05 - 21:35:24         19s  (unknown)
21:35:25 - 21:42:10      6m 45s  chrome.exe  Example - Google Chrome
```

`(unknown)` 通常表示 Windows 没有返回普通前台进程名，可能是屏幕保护、锁屏、系统 UI 或受保护窗口。

## 6. 查看应用汇总

看今天每个进程的总时长：

```bash
python -m focusledger summary --today
```

看指定日期：

```bash
python -m focusledger summary --date 2026-07-09
```

## 7. 查看 Attention

看今天的 attention 汇总：

```bash
python -m focusledger attention --today
```

看今天，并展开详细 attention segments：

```bash
python -m focusledger attention --today --timeline
```

看昨天或任意日期：

```bash
python -m focusledger attention --date 2026-07-09 --timeline
```

Attention 状态含义：

```text
active          最近有键鼠输入
low_activity    低交互，可能仍在阅读/观看/思考
attention_gap   注意力可能已经断开
interrupted      长时间中断
hard_idle        确认长时间离开或无输入
unknown          无法读取 idle 信息
```

默认阈值：

```text
0-3 min       active
3-10 min      low_activity
10-20 min     attention_gap
20-30 min     interrupted
30+ min       hard_idle
```

注意：当前版本只把原始采样写入数据库。`timeline`、`summary`、`attention` 都是在查询时从 raw events 推导出来的。

## 8. 打开可视化报告（本地网页）

启动本地报告服务并自动打开浏览器：

```bash
python -m focusledger report --today
```

输出类似：

```text
FocusLedger report server running.
  url: http://127.0.0.1:53127/
  date: 2026-07-18 (live — follows today as it is collected)
  press Ctrl+C to stop
```

页面是**实时**的：agent 每采一批新数据，网页会自动刷新内容，**并且保留你当前的缩放位置**。不需要重新跑命令。`Ctrl+C` 停止服务。

常用参数：

```bash
python -m focusledger report --today --port 8765     # 固定端口
python -m focusledger report --today --no-open       # 只打印 URL，不自动开浏览器
python -m focusledger report --date 2026-07-16       # 看历史某天（非实时）
```

服务只监听 `127.0.0.1`（本机回环），局域网内其他设备无法访问，个人注意力数据不会离开这台机器。

### 导出静态快照

需要归档或发给别人时，导出一个自包含的单文件 HTML（内联全部数据、样式和脚本，离线可开）：

```bash
python -m focusledger report --date 2026-07-16 --export ~/Desktop/focus-0716.html
```

快照跑的是和实时页面同一套前端代码，只是数据固定、不再自动刷新。

页面内容：

```text
顶部统计卡    有效专注 / 原始时长 / 专注率 / 深度工作 / 应用切换次数 / Top app
Focus Skyline 注意力时间线：柱高 = 权重，颜色 = 状态；长空闲直接标注在图上
专注密度条    每 15 分钟的有效专注比例（类似股票图的成交量行）
应用泳道      每个应用一条泳道，展示前台切换模式
minimap       全天缩略图 + 当前视窗框
Deep Work     自动检测的 ≥25 分钟持续投入段，点击可缩放跳转
状态占比/应用排行  随当前可见范围实时聚合
表格视图      可展开的 attention segments 明细
```

交互操作：

```text
滚轮          以光标为锚缩放（可一直放大到 2 分钟视窗）
15m/1h/3h/Day 一键跳到该时间跨度（实时模式下以“现在”为中心）
拖拽          平移
shift + 拖拽  框选一段区间并放大
minimap       拖动视窗框平移；在空白处框选放大
双击 / Esc / 0  回到全天
← →           平移      + −   缩放
悬停          十字准线 + 浮动详情（状态、权重、该段时长、应用、该段分钟数、窗口标题）
图例点击      隔离某个 attention 状态
应用排行悬停  高亮该应用在泳道中的所有段
```

实时模式下还有两个行为：时间轴上有一条青色虚线标记“现在”；如果你的视窗停在最右侧，新数据到达时会自动跟随推进（类似行情图的跟盘），一旦你手动往回拖就不再自动跟随。


## 9. Workday（工作日边界）

日报按你真实的工作周期切分，而不是按自然日。跨午夜的工作仍归属开始那天。

```bash
python -m focusledger workday start        # 上班
python -m focusledger workday pause --reason lunch
python -m focusledger workday resume
python -m focusledger workday overtime     # 过了下班时间但继续做
python -m focusledger workday offwork      # 下班
python -m focusledger workday status
python -m focusledger workday list
```

假期不会催你写日报：

```bash
python -m focusledger workday mark-holiday --date 2026-10-01
python -m focusledger workday mark-vacation --date 2026-10-02
```

`status` 会显示 **missing data**——agent 没在跑的时间段。这是从 heartbeat 空档推导出来的，
日报里也会显示，避免你误以为统计是完整的。

## 10. 活动分类

把时间归入 11 个类别（核心生产力 / 学习 / 泛知识 / 行政 / 娱乐 / 游戏 / 社交 / 沟通 等）。

```bash
python -m focusledger classify summary --today     # 每类用了多久
python -m focusledger classify timeline --today    # 逐段看判定结果和依据
python -m focusledger classify unknown --today     # 哪些没分出来，按时长排序
python -m focusledger classify categories          # 列出所有类别
```

分类不满意就手动修正，**修正会重新解释历史数据**（分类是查询时推导的）：

```bash
python -m focusledger classify correct process "obsidian" productive_core
python -m focusledger classify correct site "youtube.com" learning_general
python -m focusledger classify correct title_keyword "标书" admin
python -m focusledger classify corrections     # 看已有修正
python -m focusledger classify uncorrect process "obsidian"
```

判定优先级：手动修正 → deliverable 关联 → 站点规则 → 应用规则 → 标题关键词 → unknown。
所以 YouTube 上的讲座会算成学习，游戏实况会算成娱乐。

## 11. Deliverable 与 Task

FocusLedger 关心的不是"忙了多久"，而是"真正重要的事推进了没有"。

```bash
python -m focusledger deliverable add "FocusLedger MVP" --priority 1 --keywords focusledger,pycharm
python -m focusledger deliverable list --today      # 带今天在每项上花的时间
python -m focusledger deliverable update <id> --due 2026-08-01
python -m focusledger deliverable done <id>
```

`--keywords` 是自动归因的依据：关键词出现在应用名或窗口标题里，这段时间就算到该 deliverable 上。

```bash
python -m focusledger task add "Wire Phase 8 review" --deliverable FocusLedger --estimate 90
python -m focusledger task list --open
python -m focusledger task done <id>
python -m focusledger task block <id> "等对方给数据"
```

## 12. 分心提醒

agent 在 workday 进行中会监测持续的娱乐/游戏时段，超过阈值就本地通知一次（有 cooldown，不会连环打断），
并记录成事件等你说明原因——**没有原因的"分心"只是羞辱，不是信息**。

```bash
python -m focusledger distraction list --today
python -m focusledger distraction reasons
python -m focusledger distraction label <id> rabbit_hole
```

阈值和 cooldown 在配置文件里：`gaming_alert_minutes`、`entertainment_alert_minutes`、
`notification_cooldown_seconds`、`audio_enabled`。

## 13. 本地 LLM

日报的文字部分由本机小模型生成，走 Ollama 接口，数据不出机器、零费用、零 Python 依赖。

```bash
python -m focusledger llm status      # 检查是否可用，并给出安装指引
python -m focusledger llm test        # 发一句话测试
```

启用方式：装 [Ollama](https://ollama.com/download) → `ollama pull qwen2.5:1.5b` → 完成。
换模型改配置 `llm_model` 即可（`qwen2.5:3b`、`qwen3:1.7b`、`llama3.2:1b`、`gemma2:2b` 都行）。

**没装也能用**：日报自动降级成确定性的规则版本，客观统计完全一致。

### 让模型分析你的数据：analyst

`analyst` 让本地模型先通过一组只读工具查数据，再回答问题。所有数字都由 Python 算好，模型只负责决定查什么、怎么说。
答案里如果出现工具结果里没有的数字，会被打回去重写；重写后仍有，就在结尾标出警告。

```bash
python -m focusledger analyst ask "上周我的时间主要花在哪里？"
python -m focusledger analyst ask "8 月 7 日哪段时间最专注？" --trace        # 显示每次工具调用
python -m focusledger analyst ask "..." --model deepseek-r1:8b              # 临时换模型
python -m focusledger analyst eval --models qwen3.5:9b,qwen3:8b --out eval.json   # 多个模型跑同一套问题
python -m focusledger analyst tools                                          # 列出模型能用的工具
python -m focusledger analyst tool day_summary --args '{"date": "2026-08-07"}'     # 不经模型，直接跑一个工具
```

- 默认模型是配置里的 `llm_analyst_model`（默认 `qwen3.5:9b`）。分析要多轮调用工具，日报用的 1.5B 小模型不够用，建议 8B 以上。
- 工具调用有两种方式：`native` 走 Ollama 原生的工具调用；`text` 让模型在回复里写一段 JSON。默认 `auto` 会先试探一次：模型不会原生调用工具时（比如 `deepseek-r1:8b`），自动改用 `text`。
- `--think` / `--no-think` 开关模型的思考模式，`--num-ctx` 调上下文长度（默认 16384），`--tool-mode` 强制指定工具调用方式。
- 可用工具：`list_days`、`day_summary`、`range_summary`、`compare_ranges`、`app_usage`、`timeline`、`hourly_profile`、`deliverables`，全部只读。

本机实测（RTX 5070 12 GB，Ollama 0.40，`analyst eval` 内置的 5 道题，2026-10-22，答案逐条对照工具结果人工核对）：

| 模型 | 工具调用方式 | 完全正确 | 中位耗时 | 问题 |
|---|---|---:|---:|---|
| `qwen3.5:9b` | native | 5/5 | 22 秒 | 没有一次需要打回 |
| `qwen3:8b` | native | 2/5 | 32 秒 | 自己算占比；区间对比选错日期、方向说反；把锁屏时间说错 |
| `deepseek-r1:8b` | text | 2/5 | 55 秒 | 不会原生调用工具；把"游戏"当应用名去搜；读错每小时数据 |

DeepSeek 更新的 V4 系列在 Ollama 上只有云端版本（`deepseek-v4.1-flash:cloud` 等），会把数据发到 Ollama 服务器，
不符合本项目"数据不出机器"的原则，所以没有接入；本地能跑的最新 DeepSeek 权重是 `deepseek-r1:8b`。

## 14. 日报与明日计划

```bash
python -m focusledger review write        # 先记录你自己的进展（系统看不出真实产出）
python -m focusledger review generate     # 生成日报
python -m focusledger review show
python -m focusledger review list
python -m focusledger review pending      # 哪几天还欠日报
python -m focusledger review generate --no-llm   # 强制用规则版
```

日报保存为 markdown，同时写到 `<data_dir>/journal/<日期>.md`。结构包含：workday 范围、客观统计、
deliverable 进展、注意力与分心、复盘、明日计划、missing data、待修正的分类。

## 15. 开机自启

```bash
python -m focusledger autostart install     # macOS launchd / Windows 计划任务 / Linux systemd
python -m focusledger autostart status
python -m focusledger autostart uninstall
```

## 16. 修改采样间隔

查看当前采样间隔可以通过 `status` 或配置文件判断。设置为 1 秒：

```bash
python -m focusledger config set poll-interval 1
```

修改后需要重启 agent 才会生效：

```bash
python -m focusledger stop
python -m focusledger start --background
```

## 17. 查看和修改 Attention 配置

查看当前阈值和权重：

```bash
python -m focusledger config attention
```

按分钟设置四个阈值：

```bash
python -m focusledger config set attention-thresholds 3 10 20 30
```

按顺序设置五个权重：

```bash
python -m focusledger config set attention-weights 1.0 0.5 0.0 0.0 0.0
```

权重顺序是：

```text
active low_activity attention_gap interrupted hard_idle
```

## 18. 隐私维护

对已经写入数据库的无痕/隐私浏览器标题重新脱敏：

```bash
python -m focusledger privacy redact-private-browser-titles
```

当前规则：

```text
普通浏览器标题会保留
无痕/隐私窗口标题会显示为 [private browsing]
```

macOS 数据的特殊情况：如果这些记录来自没有 PyObjC 时的 subprocess 采集路径（`window_handle` 为空），该命令只能脱敏那些标题本身仍带 Incognito/隐私标记的行；同一无痕窗口导航后产生的、标题已不带标记的历史记录无法靠这个命令找回并脱敏。这不是命令的 bug，而是数据本身缺少可用于关联同一窗口的句柄信息。

## 19. 测试阶段清空数据库

查看将要清空的数据库路径，但不删除：

```bash
python -m focusledger dev reset-db
```

确认清空并重建数据库：

```bash
python -m focusledger dev reset-db --yes
```

这个命令会删除本地 SQLite 数据库，适合测试阶段重新跑数据。

## 20. 常见工作流

一次性设置（只做一次）：

```bash
python -m focusledger init
python -m focusledger autostart install
python -m focusledger deliverable add "你的主要目标" --keywords 关键词
```

每天：

```bash
python -m focusledger workday start
```

查看今天：

```bash
python -m focusledger timeline --today
python -m focusledger summary --today
python -m focusledger attention --today --timeline
```

随时看实时仪表台：

```bash
python -m focusledger report --today
```

下班：

```bash
python -m focusledger workday offwork
python -m focusledger review write
python -m focusledger review generate
```

电脑重启后（agent 不会开机自启，需要手动再启动）：

```bash
python -m focusledger status
python -m focusledger start --background
```

测试阶段重新开始：

```bash
python -m focusledger stop
python -m focusledger dev reset-db --yes
python -m focusledger start --background
```
