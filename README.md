# FocusLedger

FocusLedger 是一个本地优先的个人生产力审计工具。当前版本是 terminal-first 的早期 MVP，重点验证本地采集、原始事件存储、时间线推导和 attention 估算这条基础链路，并提供一个可交互的单文件 HTML 日报表用于可视化复盘。

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
-> 写入 SQLite 原始事件
-> 查询时推导 activity timeline / app summary / attention timeline
-> report 命令渲染当天数据为可交互 HTML 仪表台
-> 通过 heartbeat/status 检查运行状态
```

新版本只把原始采样作为长期事实写入数据库。以下内容都是查询时从原始采样推导出来的，不作为派生数据长期写入：

```text
activity segments
attention segments
attention_state
attention_weight
```

这样后续修改分类规则或 attention 阈值时，旧数据可以按新规则重新计算。

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

## 可视化报告

`focusledger report` 把某一天的原始采样渲染成一个自包含的 HTML 仪表台（内联数据/样式/脚本，无网络依赖，直接用浏览器打开）。主要内容：

- **Focus Skyline**：注意力时间线，柱高 = attention 权重，颜色 = 状态；长时间空闲直接标注在图上。
- **专注密度条**：每 15 分钟的有效专注比例，一眼看出全天节奏。
- **应用泳道**：每个应用一条泳道，展示前台切换模式。
- **深度工作 sessions**：自动检测 ≥25 分钟的持续投入段，点击可缩放跳转。
- **交互**：滚轮缩放（最小视窗 30 分钟）、拖拽平移、shift+框选、minimap 导航、十字准线 + 悬停详情；下方统计面板随可见范围实时聚合。

报告与其他查询命令一样在生成时从原始采样推导，不落库。具体用法见 CLI 操作手册。

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

## 当前尚未实现

- workday session
- 浏览器 URL / audio / private mode API 采集
- activity 分类
- 分心提醒
- LLM 日报
- Markdown journal
- Linux active window / idle 采集

## 测试

开发验证命令：

```bash
python -m unittest discover -s tests
python -m compileall focusledger tests
```

注意：macOS / Linux 上系统的 `python` 可能不存在或指向 Python 2，请统一使用 `python3`（本文档与 CLI 手册中的 `python -m focusledger ...` 同理）。
