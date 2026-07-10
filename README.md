# FocusLedger

FocusLedger 是一个本地优先的个人生产力审计工具。当前版本是
terminal-first 的早期 MVP，主要用于测试最基础的数据链路：

```text
启动 agent
-> 采集当前前台窗口
-> 写入 SQLite 原始事件和 activity segments
-> 通过 heartbeat/status 检查运行状态
-> 查看 timeline 和 app summary
```

## 平台支持状态

| 平台 | 当前支持情况 |
|---|---|
| Windows | 已支持前台窗口采集。 |
| macOS | 已支持前台应用采集；窗口标题需要授予辅助功能权限，且 Chrome / Edge 暂不可得。 |
| Linux | 可以初始化配置和数据库，也可以运行 CLI 查询命令，但采集尚未实现。 |

## 环境要求

- Python 3.11 或更新版本
- Windows 采集不需要安装第三方 Python 包。
- macOS 采集默认使用系统自带命令（`lsappinfo` / `osascript` / `ioreg`），
  不需要安装任何第三方包。如果已安装 PyObjC，会自动优先使用它。
- Linux 暂不支持采集。

## Windows 使用方式

在项目目录打开 PowerShell：

```powershell
cd "C:\Work&Study\Duke\Projects\FocusLedger"
python -m focusledger init
```

后台启动持续采集：

```powershell
python -m focusledger start --background
```

命令返回后，可以在同一个 PowerShell 查看状态、时间线和注意力汇总：

```powershell
python -m focusledger status
python -m focusledger timeline --today
python -m focusledger summary --today
python -m focusledger attention --today
```

确认 `status: running` 且 `stale: false` 后，可以关闭当前 PowerShell，
后台 agent 会继续运行。

停止后台 agent：

```powershell
python -m focusledger stop
```

如果需要在前台调试：

```powershell
python -m focusledger start
```

前台模式会占用当前 PowerShell，按 `Ctrl+C` 停止。

只采集一次然后退出：

```powershell
python -m focusledger start --once
```

采集固定次数后退出：

```powershell
python -m focusledger start --max-samples 10
```

注意：当前版本还没有开机自启动。电脑重启后需要重新运行
`python -m focusledger start --background`。

## macOS 使用方式

不需要安装任何第三方包，直接运行：

```bash
cd /path/to/FocusLedger
python -m focusledger init
python -m focusledger start --background
```

在同一个终端查看状态和记录：

```bash
python -m focusledger status
python -m focusledger timeline --today
python -m focusledger summary --today
python -m focusledger attention --today
python -m focusledger stop
```

权限说明：

- 采集前台应用名、进程名、可执行路径不需要额外权限。
- 采集窗口标题需要在「系统设置 → 隐私与安全性 → 辅助功能（Accessibility）」中
  授予运行 FocusLedger 的终端（Terminal / iTerm / VS Code 等）权限。
  未授权时窗口标题为空，但应用级别的采集仍然正常工作。
- 首次授予权限后需要重启该终端。

窗口标题的已知限制（subprocess 后端）：

- 原生 Cocoa 应用（Finder、备忘录、预览、系统设置等）能通过辅助功能拿到窗口标题。
- 基于 Chromium / Electron 的应用（Chrome、VS Code、Slack、Discord 等）不会通过
  辅助功能暴露窗口标题，因此这些应用的标题为空，但应用级别采集照常工作。
- 如果安装了 PyObjC（`pip install pyobjc-framework-Cocoa pyobjc-framework-Quartz`），
  适配器会自动改用 Quartz + 屏幕录制（Screen Recording）权限，届时所有应用
  （含 Chrome / VS Code）的窗口标题都能拿到。浏览器内的具体页面 / URL 仍计划在
  后续浏览器扩展阶段（Phase 4）解决。

## Linux 使用方式

当前 Linux 可以运行 CLI 初始化和查询命令：

```bash
cd /path/to/FocusLedger
python -m focusledger init
python -m focusledger status
python -m focusledger timeline --today
python -m focusledger summary --today
```

Linux 的 active window 采集尚未实现。现在运行：

```bash
python -m focusledger start
```

会提示 Linux collection reserved for a later phase。

## 测试数据目录

默认情况下，FocusLedger 会把本地数据写到当前用户的 app data 目录。
测试阶段建议把数据目录指向项目内的临时目录。

PowerShell：

```powershell
$env:FOCUSLEDGER_DATA_DIR="C:\Work&Study\Duke\Projects\FocusLedger\.focusledger-dev"
python -m focusledger init
```

Bash：

```bash
export FOCUSLEDGER_DATA_DIR="/path/to/FocusLedger/.focusledger-dev"
python -m focusledger init
```

注意：如果你开了多个终端，它们必须设置同一个 `FOCUSLEDGER_DATA_DIR`，
否则会读写不同的数据库。

## 常用命令

查看配置文件路径：

```bash
python -m focusledger config path
```

查看数据库路径：

```bash
python -m focusledger db path
```

把采样间隔改成 1 秒：

```bash
python -m focusledger config set poll-interval 1
```

修改采样间隔后，需要重启 agent 才会生效。

查看键盘/鼠标 attention 状态与有效时间：

```bash
python -m focusledger attention --today
python -m focusledger attention --date 2026-07-05
python -m focusledger attention --today --timeline
```

`attention --today` 会默认显示一条压缩的横向 attention 时间轴；加上 `--timeline`
会继续显示每个 attention segment 的详细起止时间。

查看当前 attention 阈值和权重：

```bash
python -m focusledger config attention
```

按分钟修改四个阈值（low activity、attention gap、interrupted、hard idle）：

```bash
python -m focusledger config set attention-thresholds 3 10 20 30
```

修改五个权重（active、low activity、attention gap、interrupted、hard idle）：

```bash
python -m focusledger config set attention-weights 1.0 0.5 0.0 0.0 0.0
```

清空当前数据库并重新创建 schema：

```bash
python -m focusledger dev reset-db --yes
```

如果不带 `--yes`，命令只会显示数据库路径，不会真的删除数据：

```bash
python -m focusledger dev reset-db
```

对已经写入数据库的无痕/隐私浏览器标题进行脱敏：

```bash
python -m focusledger privacy redact-private-browser-titles
```

## 浏览器隐私行为

当前 collector 读取的是前台窗口标题。浏览器扩展还没有实现。

当前规则：

- 普通浏览器窗口标题会被保存。
- 无痕/隐私浏览器窗口标题会保存为 `[private browsing]`。
- 一旦某个浏览器窗口句柄被识别为无痕/隐私窗口，该窗口后续标题变化也会合并到 `[private browsing]`，不会因为页面变化反复切段。

重要限制：

如果 agent 启动时，无痕/隐私窗口已经打开在某个具体网页上，并且窗口标题里没有明显的 private/incognito 标记，Phase 1 可能无法可靠识别它是隐私窗口。这个问题需要后续浏览器扩展阶段彻底解决。

## Timeline 中的 `(unknown)`

`timeline` 中出现 `(unknown)` 表示 Windows 当时没有提供普通前台进程名。

常见原因包括：

- 屏幕保护程序
- 锁屏界面
- 系统 UI
- 权限受保护的窗口
- Windows 短暂无法返回 foreground window

当前版本已经实现 Proposal Part 10 的 Unified Attention Detector。每次采样只写入
前台窗口信息和键盘/鼠标 idle 秒数；attention 状态、权重、activity segments 和
attention timeline 都在查询时从原始采样推导出来，不作为派生数据长期写入数据库。

默认规则：

| 无输入时长 | 状态 | 权重 |
|---:|---|---:|
| 0-3 分钟 | `active` | 1.0 |
| 3-10 分钟 | `low_activity` | 0.5 |
| 10-20 分钟 | `attention_gap` | 0.0 |
| 20-30 分钟 | `interrupted` | 0.0 |
| 30 分钟以上 | `hard_idle` | 0.0 |

`attention` 命令同时显示原始时间和按权重折算的 effective attention time。
旧数据库会自动升级到 schema v4，不需要清空。

Part 11 中“播放课程音视频时修正 attention gap”的规则尚未接入；在浏览器音频和
内容分类完成前，10 分钟以上无输入仍按 Part 10 的保守默认规则计算。

## 当前尚未实现

当前版本还没有实现：

- workday session
- 浏览器 URL / audio / private mode API 采集
- activity 分类
- 分心提醒
- LLM 日报
- Markdown journal

## 运行测试

```bash
python -m unittest discover -s tests
```
