# FocusLedger

FocusLedger 是一个本地优先的个人生产力审计工具。当前版本完成了提案 Phase 0-9 的 MVP：本地采集、attention 估算、workday 模型、活动分类、deliverable 追踪、分心提醒、本地 LLM 日报、开机自启，以及一个实时的浏览器仪表台。schema v10 使用 raw/cooked 两层存储，在保留长期时间线的同时控制数据库增长。所有数据只存在本机 SQLite，不上传任何服务器。

## 文档入口

- CLI 操作手册：[docs/CLI_USAGE_ZH.md](docs/CLI_USAGE_ZH.md)
- 项目提案：[docs/PROJECT_PROPOSAL_ZH.md](docs/PROJECT_PROPOSAL_ZH.md)
- 开发阶段规划：[docs/DEVELOPMENT_PHASES_ZH.md](docs/DEVELOPMENT_PHASES_ZH.md)
- 开发日志：[devlog/](devlog/)

README 只保留项目级说明；具体命令、日常操作和排查步骤统一维护在 CLI 操作手册里。

## 当前数据链路

```text
启动 agent
-> 采集当前前台窗口和键盘/鼠标 idle 秒数
-> 写入 SQLite raw events 与 agent heartbeats
-> 最近日期从 raw data 推导 activity timeline / attention
-> 每个系统本地日期变化后，将超出保留期的完整日期归档为 cooked data
-> 历史日期直接读取 cooked activity / attention segments
-> 查询时继续推导分类 / deliverable 归因 / missing data
-> 分心达阈值时本地提醒并记录，等你标注原因
-> 下班后收集手写进展，本地小模型生成日报和明日计划
-> report 命令起本地服务，浏览器实时查看可交互仪表台
```

## Schema v10 与数据保留

数据库按用途分为三组：

- **raw data**：`raw_activity_events` 和 `agent_heartbeats`。默认保留最近 7 个系统本地日期，保留天数可配置。采样每次一行，应用和时区信息只在 `applications`、`time_contexts` 查找表里存一份；心跳只在状态变化时或每 30 秒写一次。
- **cooked data**：`activity_segments` 和 `attention_segments`。raw data 到期前按完整日期归档，长期保存。
- **用户事实与功能状态**：workday session/pause、classification rule/correction、deliverable/task/note、distraction、daily review 等 Phase 3-9 表。

以下内容仍在查询时计算，不额外保存副本：

```text
活动分类（category）      per-app / daily attention 汇总
missing data intervals   deliverable 时间归因
```

最近 raw data 可以按新的 attention 阈值重新解释；已经归档的 cooked data 保留归档当时生成的时间段。活动分类、per-app 汇总和 deliverable 归因仍会按当前规则从 raw 或 cooked data 计算。

每条新采样保存 UTC 时间，并关联一条时区上下文（系统时区 ID、时区显示名、UTC offset、DST 状态和本地午夜对应的 UTC），本地时间由 UTC 加 offset 还原。排序和持续时间使用 UTC，避免切换时区或夏令时回拨造成时间线逆序、截断。

数据库版本记录在 SQLite 的 `user_version` 里，升级时按编号依次执行迁移步骤，每步只执行一次；用旧版本代码打开新版本数据库会直接报错退出。从 v8 升级时，第一次运行任意命令会把 raw 表就地重建为新结构（一个事务完成，失败会整体回滚）。

常用维护命令：

```bash
python -m focusledger config retention
python -m focusledger config set raw-retention-days 14
python -m focusledger db prune
python -m focusledger db compact --yes
```

`db compact --yes` 会重建 SQLite 文件，应先停止 agent。

## 平台支持状态

| 平台 | 当前支持情况 |
|---|---|
| Windows | 已支持前台窗口采集、键盘/鼠标 idle 检测、attention 推导。 |
| macOS | 已支持前台应用采集和 idle 检测；窗口标题需要授予屏幕录制权限（Screen Recording）。 |
| Linux | 可以初始化配置和数据库，也可以运行 CLI 查询命令，但采集尚未实现。 |

## 环境要求

- Python 3.11 或更新版本
- Windows 采集不需要安装第三方 Python 包。
- macOS 默认（`pip install` 后）使用 PyObjC/Quartz 路径；`pyproject.toml` 已将其列为 darwin 平台的必装依赖。
- Linux 采集暂未实现。

### macOS：不通过 pip 安装、直接跑 `python -m focusledger` 时的降级行为

如果没有安装 PyObjC（例如直接从源码用 `python -m focusledger` 运行，没有 `pip install -e .`），采集会自动降级到基于 `lsappinfo` / `osascript` / `ioreg` 的 subprocess 后备路径。这条路径能正常采集，但有一个已知限制：**它拿不到稳定的窗口 ID**，因此无痕/隐私浏览窗口的“粘性”识别只在窗口标题本身还带着 Incognito/隐私标记时有效——一旦该窗口导航到具体网页，新标题会按普通标题保存。这个限制在启动时会打一条 warning 日志。

如果需要在 macOS 上保证无痕窗口的隐私保护，请执行 `pip install -e .`（或确保 `pyobjc-framework-Cocoa` / `pyobjc-framework-Quartz` 已安装）以启用 pyobjc 路径，它和 Windows 一样能提供稳定的窗口句柄。

## Attention 模型

当前版本实现 Proposal Part 10 的 Unified Attention Detector。它不读取键盘内容、鼠标位置、摄像头或眼动信息，只读取系统提供的“距离上次键盘/鼠标输入过去了多久”。

默认规则：

| 无输入时长 | 状态 | 权重 |
|---:|---|---:|
| 0-3 分钟 | `active` | 1.0 |
| 3-10 分钟 | `low_activity` | 0.5 |
| 10-20 分钟 | `attention_gap` | 0.0 |
| 20-30 分钟 | `interrupted` | 0.0 |
| 30 分钟以上 | `hard_idle` | 0.0 |

`low_activity` 表示低交互但仍可能在阅读、观看或思考；`attention_gap` 表示已经缺少足够证据证明用户仍在持续关注。

当下一次真实输入确认前一段长时间无输入时，查询层会把整段长空白解释为 `hard_idle`。例如：

```text
13:00 最后一次输入
14:00 下一次输入
=> 13:00 - 14:00 hard_idle
```

Part 11 中“播放课程音视频时修正 attention gap”的规则尚未接入；在浏览器音频和内容分类完成前，长时间无输入仍按 Part 10 的保守规则计算。

## 本地 LLM

日报的自然语言部分由**本机运行的小模型**生成，走 Ollama 的 HTTP 接口（默认 `127.0.0.1:11434`），
数据不出机器，也不产生任何 API 费用。项目本身**不增加任何 Python 依赖**，只用标准库发请求。

```bash
# 安装 Ollama 后拉一个小模型即可（1-3B 就够用）
ollama pull qwen2.5:1.5b
python -m focusledger llm status
```

没有装模型也完全不影响使用：日报会自动降级为确定性的规则版本，客观统计一字不少。

设计上有一条硬边界：**模型只负责写评述文字，绝不参与任何计算**。所有数字都在 Python 里算好后
才交给模型，因此即使用 0.5B 的模型也不会出现算错时间的情况，最多是措辞弱一些。

`analyst` 命令把同一条边界用在自由提问上：本地模型通过 8 个只读工具（单日详情、区间汇总、区间对比、
应用用量、时间线、每小时节奏等）查数据，再回答"上周时间花在哪""哪段时间最专注"这类问题。
答案里出现工具结果里没有的数字时会被打回重写，仍然有就标出警告。用法见 CLI 操作手册第 13 节。

## 可视化报告

`focusledger report` 会在本机启动一个只监听 `127.0.0.1` 的报告服务并打开浏览器（`http://127.0.0.1:<port>/`）。页面是实时的：最近日期从 raw data 推导，历史日期读取 cooked data；agent 采到新数据后网页自动更新，且保留当前缩放位置。

前端是 React 18，通过 vendored UMD 构建 + htm 模板直接运行，**不需要 Node、npm 或任何构建步骤**。`--export` 可以把同一套界面连同数据内联成一个离线单文件 HTML 快照。主要内容：

- **Focus Skyline**：注意力时间线，柱高 = attention 权重，颜色 = 状态；长时间空闲直接标注在图上。
- **专注密度条**：每 15 分钟的有效专注比例，一眼看出全天节奏。
- **应用泳道**：每个应用一条泳道，展示前台切换模式。
- **深度工作 sessions**：自动检测 ≥25 分钟的持续投入段，点击可缩放跳转。
- **交互**：滚轮缩放（可深入到 2 分钟视窗）、快捷档位、拖拽平移、shift+框选、minimap 导航、十字准线 + 悬停详情；下方统计面板随可见范围实时聚合。

报告与其他查询命令一样只读取现有 raw/cooked data，不为报告本身额外写入派生结果。具体用法见 CLI 操作手册。

## 隐私行为

FocusLedger 当前只采集前台窗口元数据和 idle 秒数，不采集键盘输入内容、鼠标坐标或网页正文。

浏览器隐私规则：

- 普通浏览器窗口标题会被保存。
- 无痕/隐私浏览器窗口标题会保存为 `[private browsing]`。
- 一旦某个浏览器窗口句柄被识别为无痕/隐私窗口，该窗口后续标题变化也会合并到 `[private browsing]`（Windows、macOS pyobjc 路径）。

重要限制：如果 agent 启动时，无痕/隐私窗口已经打开在某个具体网页上，并且窗口标题里没有明显 private/incognito 标记，当前阶段可能无法可靠识别它是隐私窗口。这个问题计划在后续浏览器扩展阶段解决。

macOS 上没有 PyObjC 时会退化到 subprocess 采集路径，这条路径拿不到窗口句柄，因此上面第三条“粘性”规则不适用——无痕窗口只在标题本身还带 Incognito/隐私标记的那一刻被脱敏，之后导航到的网页标题会按普通标题保存。详见上方“环境要求”一节。

## `(unknown)` Timeline

`timeline` 中出现 `(unknown)` 表示系统当时没有提供普通前台进程名。

常见原因包括：

- 屏幕保护程序
- 锁屏界面
- 系统 UI
- 权限受保护的窗口
- Windows 短暂无法返回 foreground window

连续 `(unknown)` 样本会在查询时合并成连续段。若同时存在长时间无键鼠输入，attention 查询会按 idle 规则把这段解释为 `hard_idle`。

## 功能总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0-2 | 采集骨架、Windows/macOS 前台窗口、idle 与 attention 推导 | 已完成 |
| Phase 3 | Workday 模型（start/pause/overtime/跨午夜/missing data） | 已完成 |
| Phase 4 | 浏览器上下文（窗口标题解析出站点与页面） | 已完成（标题解析路线） |
| Phase 5 | 活动分类（11 类，规则优先级链 + 手动修正） | 已完成 |
| Phase 6 | Deliverable / Task 管理与时间归因 | 已完成 |
| Phase 7 | 分心提醒、cooldown、原因标注 | 已完成 |
| Phase 8 | 本地 LLM 日报与明日计划（无模型时规则降级） | 已完成 |
| Phase 9 | 开机自启（launchd / 计划任务 / systemd） | 已完成 |

## 当前尚未实现

- 浏览器扩展（完整 URL、音频播放状态、无痕 API）——当前只做窗口标题解析
- Linux active window / idle 采集
- system tray 常驻图标
- 多设备同步、OCR、手机端

## 测试

开发验证命令：

```bash
python -m unittest discover -s tests
python -m compileall focusledger tests
```

注意：macOS / Linux 上系统的 `python` 可能不存在或指向 Python 2，请统一使用 `python3`（本文档与 CLI 手册中的 `python -m focusledger ...` 同理）。
