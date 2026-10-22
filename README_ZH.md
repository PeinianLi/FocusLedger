# FocusLedger

**一本本地优先的账本，记录你的注意力真正花在了哪里。**

[English README](README.md)

FocusLedger 记录电脑前台是哪个应用，以及距离上一次键盘或鼠标输入过去了多久。基于这两个信号，它给出一份如实的每日账本：时间花在了哪些应用和类别上、其中有多少是真正投入的、哪些目标有推进、时间在哪里漏掉了。本地语言模型可以回答关于这份账本的问题，但它说出的每个数字都由 FocusLedger 算好，不由模型计算。

所有数据都留在本机：一个 SQLite 文件、一个只监听 `127.0.0.1` 的报告服务，模型通过本机安装的 [Ollama](https://ollama.com) 运行。

FocusLedger 是 **Fole** 的基础。Fole 是规划中的跨设备个人助手，见[项目提案](docs/PROJECT_PROPOSAL_ZH.md)和[开发阶段](docs/DEVELOPMENT_PHASES_ZH.md)。

## 现在能做什么

| 方面 | 内容 |
|---|---|
| 采集 | 后台 agent 每秒采样一次前台窗口和键鼠空闲时长（Windows、macOS） |
| 注意力 | 把空闲时长换算成注意力状态（活跃、低活动、注意力空档、中断、长时间离开）和加权后的**有效时间** |
| Workday | 上班、暂停、加班、下班；跨午夜的工作仍归属开始那天，agent 没在运行的时段标为缺失数据 |
| 分类 | 11 个类别，依据内置规则、网站解析、游戏商店安装目录、本地模型学到的规则以及你自己的修正 |
| Deliverable | 目标和任务，按关键词把时间归到对应目标上 |
| 分心提醒 | workday 期间持续玩游戏或娱乐超过阈值时本地通知一次，并让你记下原因 |
| 日报 | 由本地小模型写复盘和明日计划；没有模型时改用确定性的规则版 |
| 分析（analyst） | 就你记录的时间提问；本地模型依据工具结果作答，每个数字都会和数据核对；它建议的修改要你批准才写入，并且可以撤销 |
| 实时报告 | 浏览器仪表台，可切换日期，内含分析面板和存储面板，全部由本机提供 |
| 开机自启 | 登录时自动启动 agent（Windows 计划任务、macOS launchd、Linux systemd） |

## 快速开始

需要 Python 3.11 或更新版本。Windows 上不需要安装其他东西；macOS 上 `pip install -e .` 会装上 PyObjC，启用完整的窗口追踪。

```bash
python -m focusledger init
python -m focusledger start --background
python -m focusledger report
```

`report` 会在浏览器里打开仪表台。想让本地模型分析你的数据，先装 [Ollama](https://ollama.com/download)，下载一个支持工具调用的模型，然后提问：

```bash
ollama pull qwen3.5:9b
python -m focusledger analyst ask "我有记录的最近 7 天，时间主要花在哪里？" --trace
```

完整的命令说明见 [docs/CLI_USAGE_ZH.md](docs/CLI_USAGE_ZH.md)（[English](docs/CLI_USAGE.md)）。

## 工作原理

```text
agent 每秒采样前台窗口和空闲秒数
→ 原始采样和心跳写入 SQLite
→ 最近几天在查询时由原始采样推导：
     应用时间段、注意力时间段、类别、deliverable 时间
→ 某天超出原始数据保留期后归档：
     几百个时间段替代几万条采样
→ CLI、实时报告和 analyst 读取的是同一套推导结果
```

能重新算出来的东西一律不存。修改分类规则或注意力阈值，会重新解释历史数据，而不是留下过时的结果。

## 注意力模型

FocusLedger 不读取你输入的内容、鼠标位置，也不使用摄像头，只读取"距离上一次键盘或鼠标输入过去了多久"。

| 距上次输入 | 状态 | 权重 |
|---:|---|---:|
| 0–3 分钟 | `active` | 1.0 |
| 3–10 分钟 | `low_activity` | 0.5 |
| 10–20 分钟 | `attention_gap` | 0.0 |
| 20–30 分钟 | `interrupted` | 0.0 |
| 30 分钟以上 | `hard_idle` | 0.0 |

长时间没有输入、之后又恢复输入时，整段空白都会被重新判为 `hard_idle`，所以人离开时开着的窗口永远不会被算成工作。跨午夜的空白在本地 0 点切开，同一段时间不会算进两天。阈值和权重都可以配置。

## 分析（analyst）

`focusledger analyst` 命令和页面上的 **Ask the Analyst** 面板，让本地模型回答"我一天里哪几个小时最专注""这周和上周比怎么样"这类问题。

- 模型调用 8 个查询工具：数据索引、单日汇总、区间汇总、区间对比、应用用量、时间线、每小时节奏、deliverable。所有计算都由工具在 Python 里完成，交给模型的是算好的数字。
- 另有两个工具让它**提议**修改：把一段时间标成不在电脑前（比如"8月11号的unknown全是在睡觉，帮我在数据库里面改了"会把那天所有锁屏时段标成睡觉），或者把某个应用、网站、关键词固定归到某个类别。只有你在页面上批准、或在终端里回答 `y`，才会写入。每次写入都有记录、可以撤销；原始采样永远不改，标记的时段是一条备注，在读取那一天时叠加上去。
- 接地检查会把答案里的每个数字和工具结果比对。没调用任何工具就作答，或者写了工具结果里没有的数字，会被打回重写；重写后仍找不到来源的数字会被标出来。
- 追问时模型能看到最近 4 个回答，所以问完某一天再说"把那天标一下"也能听懂。会思考的模型，思考过程会显示在答案旁边。
- 工具调用走 Ollama 原生的工具接口；对于自称支持工具、实际从不发起调用的模型，改用在回复里写 JSON 的方式。默认 `auto` 会对每个模型先试探一次再决定。
- `analyst eval` 让多个模型跑同一套问题，方便比较。

在 51 天真实数据上的实测（RTX 5070 12 GB，Ollama 0.40，5 道内置问题，答案逐条对照工具结果人工核对）：

| 模型 | 工具调用方式 | 完全正确 | 中位耗时 |
|---|---|---:|---:|
| `qwen3.5:9b`（默认） | native | 5/5 | 22 秒 |
| `qwen3:8b` | native | 2/5 | 32 秒 |
| `deepseek-r1:8b` | text | 2/5 | 55 秒 |

DeepSeek 最新的 V4 系列在 Ollama 上只有云端版本，会把你的活动数据发出本机，所以没有使用。

## 实时报告

`python -m focusledger report` 在 `127.0.0.1` 上启动仪表台并打开浏览器。前端是 React 18，直接运行内置的库文件，不需要构建；`--export` 会把同一套界面连同数据内联成一个离线 HTML 文件。

- **日期栏**：在所有有记录的日子之间切换，只有今天会实时刷新。
- **Focus Skyline**：注意力时间线，下面有每 15 分钟的专注密度、类别色带和每个应用一条泳道。滚轮缩放（最细到 2 分钟视窗），拖拽平移，按住 shift 拖拽可框选放大。
- **深度工作**：持续投入 25 分钟以上的时段，点击可跳转放大。
- **Ask the Analyst**：提问和追问，实时显示工具调用和思考过程；答案里的日期可以点击，跳回报告里对应的那一天；修改提案以卡片显示，可 Approve / Dismiss / Undo，底部列出所有代你做过的修改。不用时可以把整个面板折叠起来。
- **Database**：文件大小、在用和空闲空间、原始和归档两层数据；需要维护时提示该运行的命令。

## 数据与存储

数据库在 Windows 上位于 `%LOCALAPPDATA%\FocusLedger\focusledger.sqlite3`，其他系统位于 `~/.focusledger/focusledger.sqlite3`，分三层：

| 层 | 表 | 保留多久 |
|---|---|---|
| 原始数据 | `raw_activity_events`、`agent_heartbeats`，以及查找表 `applications`、`time_contexts` | 默认最近 7 个本地日期 |
| 归档数据 | `activity_segments`、`attention_segments` | 长期 |
| 你的记录 | workday、暂停、deliverable、任务、笔记、日报、分心事件、规则、修正、你标记的离开时段、已批准修改的记录 | 永不清理 |

每条采样存 UTC 时间，并关联一条时区信息，所以切换时区或夏令时变化都不会打乱时间线。数据库版本记录在 SQLite 的 `user_version` 里（当前为 11）；第一次运行时按编号逐步升级，旧版本代码打开新版本数据库会拒绝运行。

```bash
python -m focusledger config retention
python -m focusledger config set raw-retention-days 14
python -m focusledger db prune          # 立刻归档超过保留期的日子
python -m focusledger db compact --yes  # 重建数据库文件；先停掉 agent
```

在一份真实数据库上，归档 7 天每秒一次的采样后，文件从 235 MB 降到 17 MB。

## 隐私与安全

- 只采集前台窗口的元数据和空闲秒数，不采集按键、鼠标位置或网页内容。
- 无痕/隐私浏览器窗口的标题存为 `[private browsing]`；一个窗口一旦被识别为无痕窗口，之后标题再变也保持脱敏（Windows，以及装了 PyObjC 的 macOS）。
- 报告服务只绑定本机回环地址，会核对 `Host` 请求头，防止通过 DNS rebinding 读取数据；分析问题和批准操作只接受 JSON 请求。
- 模型不能自己改你的数据：修改工具只返回提案，只有你批准才会写入。
- 模型的回答只当作文本显示，不会作为 HTML 插入页面。
- 模型通过 Ollama 在本地运行，不向任何云服务发送数据。

## 平台支持

| 平台 | 状态 |
|---|---|
| Windows | 前台窗口、进程、标题采集，空闲检测，注意力推导 |
| macOS | 前台应用和空闲检测；窗口标题需要授予"屏幕录制"权限。没有 PyObjC 时改用 subprocess 方式采集，无法在标题变化后继续追踪无痕窗口 |
| Linux | 配置、数据库和所有查询命令可用；采集尚未实现 |

## 本地模型

| 配置项 | 用在哪里 | 默认值 |
|---|---|---|
| `llm_analyst_model` | `analyst` 命令和 Ask the Analyst 面板 | `qwen3.5:9b` |
| `llm_model` | `review generate` 日报文字、`classify auto` 自动分类 | `qwen2.5:1.5b` |

两者都通过 `127.0.0.1:11434` 上的 Ollama 运行，只用 Python 标准库。没有模型时，日报会改用确定性的规则版，统计数字完全一样。

## 文档

| 文档 | English | 中文 |
|---|---|---|
| 命令说明 | [CLI_USAGE.md](docs/CLI_USAGE.md) | [CLI_USAGE_ZH.md](docs/CLI_USAGE_ZH.md) |
| 项目提案（Fole） | [PROJECT_PROPOSAL.md](docs/PROJECT_PROPOSAL.md) | [PROJECT_PROPOSAL_ZH.md](docs/PROJECT_PROPOSAL_ZH.md) |
| 开发阶段（Fole） | [DEVELOPMENT_PHASES.md](docs/DEVELOPMENT_PHASES.md) | [DEVELOPMENT_PHASES_ZH.md](docs/DEVELOPMENT_PHASES_ZH.md) |
| 最初的生产力审计方案 | | [docs/legacy](docs/legacy/README.md) |
| 开发日志 | | [devlog/](devlog/) |

## 尚未实现

- 浏览器扩展（完整 URL、音频播放状态、可靠的无痕窗口识别）；目前网站信息来自窗口标题
- Linux 采集
- 系统托盘图标
- 多设备同步、OCR、手机端

## 开发

运行全部测试（`tests/` 下所有 `test_*.py`；测试只用临时建的数据库，不会碰你的真实数据）：

```bash
python -m unittest discover -s tests
```

检查所有源文件能否编译，这是一次不运行任何代码的快速语法检查：

```bash
python -m compileall focusledger tests
```

Windows 上请先 `pip install tzdata`，否则时区相关的测试加载不了 `America/New_York` 这类时区。macOS 和 Linux 上请把文档里的 `python` 换成 `python3`。
