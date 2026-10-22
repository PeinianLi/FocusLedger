# FocusLedger CLI 操作说明

[English](CLI_USAGE.md)

本文档说明 `python -m focusledger ...` 的日常用法，章节编号和英文版一一对应。

两点通用说明：

- macOS / Linux 上请把示例中的 `python` 替换为 `python3`（系统自带的 `python` 可能不存在或指向 Python 2）。
- macOS 推荐先 `pip install -e .` 安装（会带上 PyObjC，启用完整的窗口句柄与隐私追踪）；不安装直接从源码运行也可以，但会降级到 subprocess 采集路径，启动日志里会有对应 warning，详见 README“平台支持状态”。

## 升级到当前版本后先看这里（本地测试步骤）

当前数据库版本是 **v11**。从旧版本升级时，**第一次运行任意 `focusledger` 命令就会自动迁移**：从 v9 或 v10 升级几乎是瞬间完成（v11 只是新增两张表，用来存你批准的修改，见第 13 节）；从 v8 升级会就地重建原始数据表，几十万行大约几秒。
升级前建议先备份（做法见第 18 节）。用旧版本的代码打开 v11 数据库会直接报错退出，不会写坏数据。

建议按这个顺序试一遍：

1. 把超过保留期的原始数据归档，数据库会小很多（例如 235 MB → 17 MB）：

   ```bash
   python -m focusledger db prune
   ```

2. 打开报告页面，先显示最后一个有记录的日子（日期换成你自己的）：

   ```bash
   python -m focusledger report --date 2026-08-11
   ```

   在页面里试试：顶部日期栏切换日期；点 **Database** 看数据库大小和明细；在 **Ask the Analyst** 里点一个示例问题。详见第 8 节。

3. 在终端里向本地模型提问（第 13 节）：

   ```bash
   python -m focusledger analyst ask "我有记录的最近 7 天，时间主要花在哪里？" --trace
   ```

4. 让模型帮你改一天的数据，比如睡觉时屏幕一直没关的那一晚。在页面里问"8月11号的unknown全是在睡觉，帮我在数据库里面改了"，看一眼修改卡片，点 **Approve**，报告马上更新；点 **Undo** 就恢复原样。详见第 13 节"修改数据"。

5. 用完在终端按 `Ctrl+C` 停掉报告服务。

两个模型配置要分清：

| 配置项 | 用在哪里 | 默认值 |
|---|---|---|
| `llm_analyst_model` | `analyst` 提问、报告页面的 Ask the Analyst | `qwen3.5:9b` |
| `llm_model` | `review generate` 日报文字、`classify auto` 自动分类 | `qwen2.5:1.5b` |

`ollama list` 里没有的模型用不了：要么 `ollama pull <模型名>`，要么在配置文件（路径用 `python -m focusledger config path` 查）里把对应的值改成已经下载的模型。日报在模型不可用时会自动改用规则版，`classify auto` 则会直接报错提示。

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

说明 agent 最近仍在正常 heartbeat。agent 在状态变化时（启动、出错、恢复）立即写一次 heartbeat，平时最多每 30 秒写一次。

如果显示：

```text
status: running
stale: true
```

说明最近 60 秒内没有 heartbeat（采样间隔很长时是 3 个采样间隔），常见原因是电脑重启、agent 崩溃、或者之前的进程已经不存在。

如果有超过保留期、还没归档的原始数据，`status` 会先多打印一行提示，例如：

```text
7 day(s) of raw samples are past retention and not archived yet (2026-08-05 to 2026-08-11); the agent archives them when it starts, or run `focusledger db prune` now.
```

归档平时由 agent 在每天日期变化时自动完成；agent 停着的时候就会积压，按提示运行 `db prune` 即可（见第 18 节）。

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

数据来源：保留期内（默认最近 7 天）的日子，`timeline`、`summary`、`attention` 都是查询时从原始采样现算的，所以改了阈值会立刻重新解释；更早的日子读取归档时存下的时间段（见第 18 节）。

跨午夜的长时间无输入只按本地日期算一次：例如 16:30 离开、第二天 08:00 回来，0 点之前的部分算在前一天，0 点之后的部分算在后一天，不会把同一段时间算进两天。

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
python -m focusledger report --date 2026-07-16       # 打开时先显示历史某天（非实时）
```

页面顶部的日期栏可以在所有有记录的日子之间切换（‹ › 逐天跳，下拉框直接选，`Today` 回到今天）。只有今天会实时刷新，历史日期只加载一次。
今天还没有数据时，页面会给出"打开最后一个有记录的日子"的按钮。

日期栏旁边的 **Database** 显示数据库文件大小，点一下展开存储面板，能看到：
- 文件大小、真正存着数据的部分、已释放但还留在文件里的空闲空间；
- 原始数据有多少行、覆盖哪几天，已归档了多少天，心跳记录有多少条；
- 有超过保留期的原始数据时，提示运行 `focusledger db prune` 归档；空闲空间超过 5% 时，提示运行 `focusledger db compact --yes` 把空间还给磁盘（要先停掉 agent）。

服务只监听 `127.0.0.1`（本机回环），局域网内其他设备无法访问，个人注意力数据不会离开这台机器。
接口还会核对请求的 Host，别的网站即使把自己的域名解析到 127.0.0.1（DNS rebinding），也读不到这些数据。

### 在页面里问本地模型（Ask the Analyst）

页面上方的 **Ask the Analyst** 面板就是第 13 节的 `analyst ask`：输入问题（或点一个示例问题），`Ctrl+Enter` 发送。

- 回答过程中会实时列出模型调用了哪些工具、各花了多久；点开每一条能看到模型拿到的原始数据。
- **思维链**：会先思考再回答的模型（`qwen3.5:9b`、`qwen3:8b`、`deepseek-r1:8b`），思考过程会实时显示在 **Thinking** 块里，答案出来后自动收起，点一下可以再展开。不支持思考的模型不显示这一块。
- **追问**：模型能看到你最近 4 个已回答的问题，所以先问某一天，再说"把那天的 unknown 标成睡觉"也能听懂。点 **New conversation** 清空上下文。
- **折叠**：标题旁的 `-` / `+` 按钮收起或展开整个面板，页面会记住你的选择。新回答出来时，旧回答折叠成一行问题，点问题可以重新展开。
- **修改提案**以卡片形式出现在回答下面，写明要改什么、有效时间会怎么变。**Approve** 写入并刷新报告，**Dismiss** 放弃，已写入的卡片上点 **Undo** 撤销。
- 面板底部的 **Changes made for you** 列出所有批准过的修改（包括在终端里批准的），每条都有自己的 **Undo**，刷新页面也还在。
- 回答里出现的有记录日期会变成链接，点一下上面的报告就切到那一天。
- 回答里如果有工具结果中找不到的数字，会用黄色提示标出来；被打回重写过的回答会标 `sent back once`。
- 右上角可以换模型，列表来自本机 Ollama 已下载的模型，默认是配置里的 `llm_analyst_model`。
- 同一时间只回答一个问题（本地模型共用一块 GPU）。导出的静态快照里没有这个面板。

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

`status` 会显示 **missing data**——agent 没在跑的时间段。这是从 heartbeat 空档推导出来的（超过 60 秒没有 heartbeat 才算缺失），
日报里也会显示，避免你误以为统计是完整的。

同一个日期只会有一个 workday：笔记、日报都按日期各存一份，哪怕以后多台设备共用一个数据库也一样。

## 10. 活动分类

把时间归入 11 个类别（核心生产力 / 学习 / 泛知识 / 行政 / 娱乐 / 游戏 / 社交 / 沟通 等）。

```bash
python -m focusledger classify summary --today     # 每类用了多久
python -m focusledger classify timeline --today    # 逐段看判定结果和依据
python -m focusledger classify timeline --today --category gaming   # 只看某一类
python -m focusledger classify unknown --today     # 哪些没分出来，按时长排序
python -m focusledger classify categories          # 列出所有类别
```

**游戏按安装目录识别**：游戏的程序名五花八门（`Dangan3Win.exe`、`swhaop.exe`），所以除了按程序名，还会看程序装在哪里。
装在 Steam（`steamapps\common`）、Epic、GOG、Xbox、Riot、Ubisoft、EA 游戏库目录下的程序都算 gaming。
Steam 上也有非游戏软件（比如 Blender），被误判时用下面的手动修正改回来，手动修正的优先级更高。

让本地模型给还没分类的应用和网站打标签（离线运行，结果存成本机规则，用的是配置里的 `llm_model`）：

```bash
python -m focusledger classify auto --today --dry-run   # 先看模型给的标签，不保存
python -m focusledger classify auto --today             # 保存
python -m focusledger classify rules                    # 看本机学到的规则
```

分类不满意就手动修正，**修正会重新解释历史数据**（分类是查询时推导的）：

```bash
python -m focusledger classify correct process "obsidian" productive_core
python -m focusledger classify correct site "youtube.com" learning_general
python -m focusledger classify correct title_keyword "标书" admin
python -m focusledger classify corrections     # 看已有修正
python -m focusledger classify uncorrect process "obsidian"
```

判定优先级：手动修正 → deliverable 关联 → 本机学到的规则（`classify auto`）→ 站点规则 → 应用规则 → 游戏库安装目录 → 标题关键词 → unknown。
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

游戏提醒依赖第 10 节的分类：装在游戏商店目录下的游戏现在都能被识别成 gaming，所以也会触发游戏提醒。

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

`analyst` 让本地模型先通过一组工具查数据，再回答问题。所有数字都由 Python 算好，模型只负责决定查什么、怎么说。
答案里如果出现工具结果里没有的数字，会被打回去重写；重写后仍有，就在结尾标出警告。

```bash
python -m focusledger analyst ask "上周我的时间主要花在哪里？"
python -m focusledger analyst ask "8 月 7 日哪段时间最专注？" --trace        # 显示每次工具调用和思考过程
python -m focusledger analyst ask "..." --model deepseek-r1:8b              # 临时换模型
python -m focusledger analyst eval --models qwen3.5:9b,qwen3:8b --out eval.json   # 多个模型跑同一套问题
python -m focusledger analyst tools                                          # 列出模型能用的工具
python -m focusledger analyst tool day_summary --args '{"date": "2026-08-07"}'     # 不经模型，直接跑一个工具
```

- 默认模型是配置里的 `llm_analyst_model`（默认 `qwen3.5:9b`）。分析要多轮调用工具，日报用的 1.5B 小模型不够用，建议 8B 以上。
  这一项没有 `config set` 命令，直接改配置文件：`"llm_analyst_model": "deepseek-r1:8b"`。只想临时换一次就用 `--model`。
- 工具调用有两种方式：`native` 走 Ollama 原生的工具调用；`text` 让模型在回复里写一段 JSON。默认 `auto` 会先试探一次：模型不会原生调用工具时（比如 `deepseek-r1:8b`），自动改用 `text`。
- `--think` / `--no-think` 开关模型的思考模式，`--num-ctx` 调上下文长度（默认 16384），`--tool-mode` 强制指定工具调用方式。
- 查询工具：`list_days`、`day_summary`、`range_summary`、`compare_ranges`、`app_usage`、`timeline`、`hourly_profile`、`deliverables`。修改工具：`mark_absent`、`correct_category`（见下文）。

#### 修改数据：模型提议，你来批准

模型可以提两类修改。它自己写不了任何东西：每个修改都先变成带预览的提案，只有你同意才会写入。

| 工具 | 作用 | 例子 |
|---|---|---|
| `mark_absent` | 把某一天的一段时间标成不在电脑前，标签为 `sleep`、`away`、`offline`、`meal`、`break` 之一。可以按应用标（某个应用在前台的所有时段，比如锁屏时的 `(unknown)`），也可以按钟点标（比如 23:30–07:10） | "8月11号的unknown全是在睡觉，帮我在数据库里面改了" |
| `correct_category` | 把某个应用、网站或标题关键词固定归到某个类别，效果和 `classify correct`（第 10 节）一样 | "把 explorer.exe 归到 admin" |

```bash
python -m focusledger analyst ask "8月11号的unknown全是在睡觉，帮我在数据库里面改了"   # 显示提案，问 y/N
python -m focusledger analyst ask "..." --yes        # 不问，直接应用提案
python -m focusledger analyst changes                # 列出所有代你做的修改
python -m focusledger analyst undo 248e5a05d057      # 按 id 撤销一条
```

提案长这样：

```text
Proposed change: Mark every stretch when (unknown) was in front on 2026-08-11 as sleep (8.6 h)
  stretches: 4
  from: "04:31"
  to: "16:35"
  effective_hours_before: 16.6
  effective_hours_after: 8.0
Apply this change? [y/N]
```

- 没有终端可以回答时（脚本、管道），不会写入任何东西；需要的话加 `--yes` 重跑。
- 原始采样永远不会被改写。标记的时段作为一条备注存在旁边，每次读取某一天时叠加上去：被标记的时间算作 **hard idle**（权重 0），类别里显示为 **Idle**，报告的悬停提示会写 "marked sleep"。撤销就是删掉这条备注，那一天恢复原样。已归档的日子同样适用。
- 每次写入的修改都连同撤销所需的信息一起存进数据库，不管来自页面还是终端；页面上在 **Changes made for you** 里能看到。

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

## 18. 数据库维护：保留、归档、压缩、备份

数据库里的数据分三层：

```text
原始采样   每次采样一行（默认 1 秒一次），只保留最近 7 个本地日期
归档       超过保留期的整天，压成几百个应用时间段和注意力时间段，长期保存
你的记录   workday、deliverable、笔记、日报、分类修正等，永远不会被自动清理
```

查看和修改保留天数：

```bash
python -m focusledger config retention
python -m focusledger config set raw-retention-days 14
```

立刻归档超过保留期的原始数据。agent 每天日期变化时也会自动做；agent 停着的时候就会积压。归档时旧的心跳记录也会一起清掉：

```bash
python -m focusledger db prune
```

归档后空出来的空间会自动还给磁盘。较早版本建的数据库没有这个能力，空间只会留在文件里给以后复用；想马上缩小文件，先停掉 agent 和报告服务，再运行：

```bash
python -m focusledger db compact --yes
```

数据库现在多大、各层各有多少，在报告页面顶部点 **Database** 就能看到（第 8 节）。

**备份**：先停掉 agent 和报告服务，再直接复制数据库文件。数据库默认在：

```text
Windows        %LOCALAPPDATA%\FocusLedger\focusledger.sqlite3
macOS / Linux  ~/.focusledger/focusledger.sqlite3
```

PowerShell 示例：

```powershell
Copy-Item "$env:LOCALAPPDATA\FocusLedger\focusledger.sqlite3" "$env:LOCALAPPDATA\FocusLedger\backups\focusledger-backup.sqlite3"
```

升级前做备份时，**备份之前不要运行任何 `focusledger` 命令**（包括 `db path`），因为第一次运行就会自动升级数据库。

**恢复**：停掉 agent 和报告服务，把备份文件复制回原位置、改名为 `focusledger.sqlite3`，再删掉旁边的 `focusledger.sqlite3-wal` 和 `focusledger.sqlite3-shm`。

数据库版本记录在 SQLite 的 `user_version` 里，升级按编号逐步执行，每一步只执行一次。

## 19. 隐私维护

对已经写入数据库的无痕/隐私浏览器标题重新脱敏：

```bash
python -m focusledger privacy redact-private-browser-titles
```

当前规则：

```text
普通浏览器标题会保留
无痕/隐私窗口标题会显示为 [private browsing]
```

已经归档的日子没有窗口句柄，所以只能按"浏览器进程 + 标题本身带无痕标记"脱敏；
同一个无痕窗口后来换了标题的那部分，在归档数据里补不回来。主要用途是以后扩充了无痕标记词（例如新增一种语言）时，回头补救旧数据。

macOS 数据的特殊情况：如果这些记录来自没有 PyObjC 时的 subprocess 采集路径（`window_handle` 为空），该命令只能脱敏那些标题本身仍带 Incognito/隐私标记的行；同一无痕窗口导航后产生的、标题已不带标记的历史记录无法靠这个命令找回并脱敏。这不是命令的 bug，而是数据本身缺少可用于关联同一窗口的句柄信息。

## 20. 测试阶段清空数据库

查看将要清空的数据库路径，但不删除：

```bash
python -m focusledger dev reset-db
```

确认清空并重建数据库：

```bash
python -m focusledger dev reset-db --yes
```

这个命令会删除本地 SQLite 数据库，适合测试阶段重新跑数据。

## 21. 常见工作流

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

随时看实时仪表台（也能在页面里切换到以前的日子、向本地模型提问、看数据库大小）：

```bash
python -m focusledger report --today
```

问本地模型：

```bash
python -m focusledger analyst ask "这周和上周比，我的有效时间有什么变化？"
```

agent 停了一阵子再回来时，先把积压的原始数据归档：

```bash
python -m focusledger status
python -m focusledger db prune
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
