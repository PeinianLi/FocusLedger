# FocusLedger

FocusLedger 是一个本地优先的个人生产力审计工具。当前版本是 terminal-first 的早期 MVP，重点验证本地采集、原始事件存储、时间线推导和 attention 估算这条基础链路。

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
| macOS | 已支持前台应用采集和 idle 检测；窗口标题需要授予辅助功能权限，部分 Chromium / Electron 应用标题可能不可得。 |
| Linux | 可以初始化配置和数据库，也可以运行 CLI 查询命令，但采集尚未实现。 |

## 环境要求

- Python 3.11 或更新版本
- Windows 采集不需要安装第三方 Python 包。
- macOS 默认使用系统自带命令采集；如果安装了 PyObjC，会自动优先使用 Quartz 路径。
- Linux 采集暂未实现。

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

## 隐私行为

FocusLedger 当前只采集前台窗口元数据和 idle 秒数，不采集键盘输入内容、鼠标坐标或网页正文。

浏览器隐私规则：

- 普通浏览器窗口标题会被保存。
- 无痕/隐私浏览器窗口标题会保存为 `[private browsing]`。
- 一旦某个浏览器窗口句柄被识别为无痕/隐私窗口，该窗口后续标题变化也会合并到 `[private browsing]`。

重要限制：如果 agent 启动时，无痕/隐私窗口已经打开在某个具体网页上，并且窗口标题里没有明显 private/incognito 标记，当前阶段可能无法可靠识别它是隐私窗口。这个问题计划在后续浏览器扩展阶段解决。

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
