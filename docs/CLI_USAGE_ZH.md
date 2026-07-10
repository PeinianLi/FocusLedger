# FocusLedger CLI 操作说明

本文档面向当前 terminal-first 版本，说明 `python -m focusledger ...` 的常用操作。

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

## 8. 修改采样间隔

查看当前采样间隔可以通过 `status` 或配置文件判断。设置为 1 秒：

```bash
python -m focusledger config set poll-interval 1
```

修改后需要重启 agent 才会生效：

```bash
python -m focusledger stop
python -m focusledger start --background
```

## 9. 查看和修改 Attention 配置

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

## 10. 隐私维护

对已经写入数据库的无痕/隐私浏览器标题重新脱敏：

```bash
python -m focusledger privacy redact-private-browser-titles
```

当前规则：

```text
普通浏览器标题会保留
无痕/隐私窗口标题会显示为 [private browsing]
```

## 11. 测试阶段清空数据库

查看将要清空的数据库路径，但不删除：

```bash
python -m focusledger dev reset-db
```

确认清空并重建数据库：

```bash
python -m focusledger dev reset-db --yes
```

这个命令会删除本地 SQLite 数据库，适合测试阶段重新跑数据。

## 12. 常见工作流

日常启动：

```bash
python -m focusledger start --background
python -m focusledger status
```

查看今天：

```bash
python -m focusledger timeline --today
python -m focusledger summary --today
python -m focusledger attention --today --timeline
```

电脑重启后：

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
