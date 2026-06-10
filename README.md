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
| macOS | 可以初始化配置和数据库，也可以运行 CLI 查询命令，但采集尚未实现。 |
| Linux | 可以初始化配置和数据库，也可以运行 CLI 查询命令，但采集尚未实现。 |

## 环境要求

- Python 3.11 或更新版本
- 当前采集 MVP 推荐在 Windows 上测试

当前版本不需要安装第三方 Python 包。

## Windows 使用方式

在项目目录打开 PowerShell：

```powershell
cd "C:\Work&Study\Duke\Projects\FocusLedger"
python -m focusledger init
```

启动持续采集：

```powershell
python -m focusledger start
```

保持这个 PowerShell 窗口打开。需要停止时，在这个窗口按 `Ctrl+C`。

另开一个 PowerShell 查看当前状态和使用记录：

```powershell
cd "C:\Work&Study\Duke\Projects\FocusLedger"
python -m focusledger status
python -m focusledger timeline --today
python -m focusledger summary --today
```

只采集一次然后退出：

```powershell
python -m focusledger start --once
```

采集固定次数后退出：

```powershell
python -m focusledger start --max-samples 10
```

从另一个终端请求正在运行的 agent 停止：

```powershell
python -m focusledger stop
```

## macOS 使用方式

当前 macOS 可以运行 CLI 初始化和查询命令：

```bash
cd /path/to/FocusLedger
python -m focusledger init
python -m focusledger status
python -m focusledger timeline --today
python -m focusledger summary --today
```

macOS 的 active window 采集尚未实现。现在运行：

```bash
python -m focusledger start
```

会提示 macOS collection reserved for a later phase。

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

当前 Windows collector 读取的是前台窗口标题。浏览器扩展还没有实现。

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

当前版本还没有实现 idle/attention 检测，所以这些时间会先原样记录。后续 Phase 2 会把这类时间更自然地归入 idle、hard idle 或 away。

## 当前尚未实现

当前版本还没有实现：

- idle / attention 检测
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
