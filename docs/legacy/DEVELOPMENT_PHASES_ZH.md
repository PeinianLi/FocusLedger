# FocusLedger 开发阶段规划

本文档用于把 `PROJECT_PROPOSAL_ZH.md` 中的产品设想拆成可执行的工程阶段。

核心原则：

```text
先保证数据可信，再做分类；
先保证 workday 边界正确，再做日报；
先完成 terminal-first MVP，再考虑桌面 UI；
先跑通每日闭环，再做 OCR、同步、多设备等增强。
```

FocusLedger 的 MVP 不应只是一个时间记录器。第一版真正要验证的是：

```text
计划 deliverables
→ 自动观察真实行为
→ 判断有效生产力
→ 识别分心和空窗
→ 用户补充今日进展
→ 生成日报
→ 重新规划明天
```

---

# 1. 总体阶段

推荐开发顺序：

```text
Phase 0  项目骨架
Phase 1  Windows 基础采集
Phase 2  Idle / Attention 状态
Phase 3  Workday 模型
Phase 4  浏览器上下文采集
Phase 5  Activity Classifier v1
Phase 6  Deliverable / Todo 管理
Phase 7  提醒与 Terminal 交互
Phase 8  LLM 日报与明日计划
Phase 9  后台化与 MVP 打磨
Post-MVP  桌面 UI、OCR、同步、多设备
```

建议第一轮只做到 Phase 3。

原因：

```text
Phase 0-3 能验证最基础的数据链路：
应用启动
→ 后台采集
→ idle 判断
→ workday 切分
→ SQLite 持久化
→ CLI 查询
```

如果这部分不稳定，后续分类、提醒和日报都会建立在不可信数据上。

---

# 2. Phase 0：项目骨架

## 2.1 目标

建立一个可以长期演进的本地 Python 项目骨架。

这一阶段不追求复杂功能，只解决：

```text
项目怎么启动
配置怎么读取
数据库怎么初始化
日志怎么写
平台差异怎么隔离
后台 agent 怎么保持心跳
CLI 怎么调用核心能力
```

## 2.2 交付物

项目结构建议：

```text
focusledger/
  __init__.py
  cli.py
  config.py
  logging_config.py
  db/
    __init__.py
    connection.py
    migrations.py
    schema.sql
  agent/
    __init__.py
    runner.py
    heartbeat.py
  platform/
    __init__.py
    base.py
    windows.py
    macos.py
    linux.py
  models/
    __init__.py
  services/
    __init__.py
tests/
pyproject.toml
README.md
```

CLI 命令建议：

```text
focusledger init
focusledger start
focusledger stop
focusledger status
focusledger db path
focusledger config path
```

SQLite 初始表建议：

```text
app_metadata
agent_heartbeats
raw_activity_events
activity_segments
workday_sessions
settings
```

配置项建议：

```text
data_dir
database_path
log_level
poll_interval_seconds
idle_threshold_seconds
platform
device_id
language
timezone
```

## 2.3 关键实现点

### CLI first

MVP 先用 terminal-first 交互，不做 GUI。

原因：

```text
开发速度快
调试简单
适合验证核心行为
后续可被 TUI / desktop UI 复用
```

### Platform adapter

平台相关能力必须通过 adapter 暴露。

建议接口：

```python
class PlatformAdapter:
    def get_active_window(self) -> ActiveWindowSnapshot:
        ...

    def get_idle_seconds(self) -> float:
        ...

    def get_now(self) -> datetime:
        ...
```

Windows 是第一完整实现，macOS / Linux 可以先保留空实现或显式报错。

### Heartbeat

agent 每隔固定时间写入 heartbeat。

用途：

```text
判断后台是否仍在运行
发现 missing data
调试采集中断
为日报标记数据可信度
```

## 2.4 验收标准

完成后应满足：

```text
1. focusledger init 能创建本地数据目录和 SQLite 数据库
2. focusledger start 能启动 agent loop
3. agent 每隔 N 秒写入 heartbeat
4. focusledger status 能显示 agent 最近心跳
5. 日志能写入本地文件
6. Windows adapter 可以被导入
7. macOS / Linux adapter 不影响 Windows MVP
```

## 2.5 不做事项

```text
不做浏览器采集
不做分类
不做日报
不做 LLM
不做 system tray
不做开机自启动
```

---

# 3. Phase 1：Windows 基础采集

## 3.1 目标

记录用户当前正在使用的应用、进程名、窗口标题，并按时间切成 activity segments。

这一阶段要回答：

```text
今天用户打开了哪些 app？
每个 app 大概用了多久？
用户什么时候从一个窗口切到另一个窗口？
```

## 3.2 交付物

Windows 采集能力：

```text
active window handle
process id
process name
executable path
window title
timestamp
```

数据表建议：

```text
raw_activity_events
activity_segments
applications
```

`raw_activity_events` 用来保留原始采样。

`activity_segments` 用来保存合并后的连续时间段。

CLI 命令建议：

```text
focusledger timeline --today
focusledger timeline --date 2026-05-06
focusledger summary --today
focusledger summary --app
```

## 3.3 Activity segment 规则

初版规则可以简单：

```text
每隔 poll_interval_seconds 采样一次
如果 process_name + window_title 没变，延长当前 segment
如果 process_name 或 window_title 变化，关闭旧 segment，创建新 segment
如果 agent 停止超过阈值，生成 missing_data segment
```

建议 segment 字段：

```text
id
device_id
started_at
ended_at
duration_seconds
process_name
window_title
executable_path
source
created_at
```

## 3.4 验收标准

完成后应满足：

```text
1. 打开 PyCharm、Chrome、Steam 并切换窗口后，timeline 能显示对应 segment
2. summary 能按 app 汇总使用时间
3. 窗口标题变化能被记录
4. agent 重启不会破坏已有数据
5. 数据库中可以区分 raw event 和 merged segment
```

## 3.5 风险

### 窗口标题频繁变化

浏览器、IDE、播放器标题可能频繁变化。

处理策略：

```text
Phase 1 先完整记录
Phase 5 分类时再做归一化
不要过早丢失 title 信息
```

### 采样间隔

采样太快会浪费资源，采样太慢会漏掉短窗口切换。

MVP 建议：

```text
poll_interval_seconds = 5
```

---

# 4. Phase 2：Idle / Attention 状态

## 4.1 目标

识别用户是否真的在电脑前操作，避免把“电脑开着但人离开”完整计入有效时间。

这一阶段要回答：

```text
用户什么时候 active？
什么时候 idle？
哪些时间段是 attention gap？
```

## 4.2 交付物

Windows idle 检测：

```text
GetLastInputInfo
last_input_at
idle_seconds
attention_state
```

attention 状态建议：

```text
active
idle
attention_gap
unknown
```

数据表建议：

```text
attention_events
attention_gaps
```

配置项建议：

```text
idle_threshold_seconds = 300
attention_gap_threshold_seconds = 600
return_prompt_threshold_seconds = 900
```

## 4.3 与 activity segment 的关系

activity segment 记录“屏幕上是什么”。

attention state 记录“人是否还在”。

不要直接用 idle 覆盖 activity segment。建议保持两层数据：

```text
activity_segments: Chrome 打开了 30 分钟
attention_gaps: 其中 12 分钟没有键鼠输入
effective_time: 后续计算时扣除 idle / gap
```

## 4.4 返回电脑后的修正

当用户 idle 很久后回来，系统可以询问：

```text
You were away for 28 minutes.
How should this time be counted?

[1] Away from computer
[2] Reading / watching without input
[3] Meeting / phone call
[4] Break
[5] Other
```

MVP 可以先保存为 pending correction，不一定马上做复杂 UI。

## 4.5 验收标准

完成后应满足：

```text
1. 用户 5 分钟无输入后进入 idle
2. 用户 10 分钟无输入后形成 attention_gap
3. 用户回来后 attention state 恢复 active
4. summary 能显示 idle / attention gap 总时长
5. app 使用时间和 effective time 可以分开计算
```

## 4.6 风险

### 看视频或读文档时没有输入

无输入不一定等于分心。

处理策略：

```text
Phase 2 只记录事实
Phase 4 采集浏览器 audio/video
Phase 5 再结合内容分类
```

---

# 5. Phase 3：Workday 模型

## 5.1 目标

建立 FocusLedger 的工作日边界。

日报不应简单按自然日切分，而应按用户真实工作周期切分。

这一阶段要回答：

```text
今天的 workday 从什么时候开始？
什么时候下班？
是否跨午夜？
是否加班？
哪些时间是暂停记录？
哪些数据缺失？
```

## 5.2 交付物

workday session 状态：

```text
not_started
active
paused
offwork_prompted
overtime
ended
holiday
vacation
missing_review
```

CLI 命令建议：

```text
focusledger workday start
focusledger workday pause
focusledger workday resume
focusledger workday offwork
focusledger workday overtime
focusledger workday status
focusledger workday mark-holiday
focusledger workday mark-vacation
```

数据表建议：

```text
workday_sessions
workday_intervals
paused_intervals
missing_data_intervals
```

## 5.3 Workday session 字段建议

```text
id
device_id
workday_key
started_at
ended_at
timezone
status
is_overtime
offwork_target_time
review_status
missing_data_seconds
created_at
updated_at
```

`workday_key` 可以是用户开始工作时的日期，例如：

```text
2026-05-06
```

即使工作跨到 `2026-05-07 04:10`，仍归属于 `2026-05-06` 的 workday。

## 5.4 下班边界

建议支持配置：

```text
default_start_time
default_offwork_time
max_workday_hours
```

到达下班时间后，系统不应该立即结束记录，而应询问：

```text
It is your offwork time.

[1] End workday and write daily summary
[2] Continue as overtime
[3] Pause tracking
[4] Remind me later
```

## 5.5 Missing data

如果 agent heartbeat 中断，应在 workday 中记录 missing data。

示例：

```text
10:20 - 10:47 missing data
reason: agent not running
```

日报中必须显示 missing data，否则用户会误以为统计完整可信。

## 5.6 验收标准

完成后应满足：

```text
1. 用户可以显式 start / offwork
2. workday 可以跨午夜
3. pause / resume 不计入正常工作统计
4. overtime 能被标记
5. agent 中断会形成 missing data interval
6. holiday / vacation 不触发日报提醒
```

## 5.7 不做事项

```text
不做 LLM 日报
不做复杂日历集成
不做多设备合并
```

---

# 6. Phase 4：浏览器上下文采集

## 6.1 目标

浏览器不能只记录为 Chrome / Edge。系统需要知道用户在浏览器里具体看了什么。

这一阶段要回答：

```text
当前 active tab 是什么 URL？
页面标题是什么？
domain 是什么？
是否正在播放音频或视频？
是否无痕 / 隐私窗口？
```

## 6.2 交付物

浏览器上下文字段：

```text
browser_name
profile_name
tab_url
tab_title
domain
is_audio_playing
is_video_playing
browser_mode
captured_at
```

`browser_mode` 建议：

```text
normal
incognito
private
unknown
```

数据表建议：

```text
browser_context_events
browser_context_segments
```

## 6.3 实现路线

MVP 可选两种：

### 方案 A：Browser extension

优点：

```text
能准确拿到 URL、title、audible、incognito
浏览器 API 支持较直接
```

缺点：

```text
需要安装扩展
需要 native messaging 或本地 HTTP 接口
```

### 方案 B：窗口标题解析

优点：

```text
实现快
不需要扩展
```

缺点：

```text
无法稳定拿 URL
无法可靠判断 incognito
无法判断 audio/video
```

推荐：

```text
Phase 4 正式实现用 browser extension。
Phase 1 可以先靠 window title 过渡。
```

## 6.4 与隐私模式的关系

MVP 策略：

```text
incognito/private browsing 不单独作为日报类别
默认合并到 entertainment / distraction
日报可以显示为 private browsing included in entertainment
```

如果没有 URL，只记录：

```text
browser_name
browser_mode
time_range
classification = entertainment
confidence = low
```

## 6.5 验收标准

完成后应满足：

```text
1. Chrome active tab URL 能进入数据库
2. page title 能进入数据库
3. YouTube 播放音频时能被识别
4. incognito/private mode 能被识别或明确标记 unknown
5. browser context 能关联到 activity segment
```

---

# 7. Phase 5：Activity Classifier v1

## 7.1 目标

把原始行为数据分类成日报可用的生产力类别。

这一阶段要回答：

```text
哪些时间是核心生产力？
哪些是一般学习？
哪些是泛知识？
哪些是娱乐？
哪些是游戏？
哪些是社交？
哪些是行政事务？
哪些是 idle / attention gap？
```

## 7.2 分类类别

MVP 建议类别：

```text
productive_core
learning_general
neutral_educational
admin
entertainment
gaming
social
communication
idle
attention_gap
unknown
```

## 7.3 交付物

分类组件：

```text
rule_based_classifier
url_classifier
domain_classifier
video_audio_classifier
manual_override
classified_url_cache
classification_confidence
```

数据表建议：

```text
activity_classifications
classification_rules
classified_url_cache
manual_corrections
```

## 7.4 Rule-based 规则优先级

建议优先级：

```text
1. manual correction
2. deliverable-linked rule
3. exact URL rule
4. domain rule
5. process/app rule
6. title keyword rule
7. browser audio/video rule
8. fallback unknown
```

示例：

```text
PyCharm + project path contains FocusLedger
→ productive_core

youtube.com + title contains VAE lecture
→ learning_general or productive_core

youtube.com + title contains gameplay
→ entertainment

Steam
→ gaming

Discord
→ social / communication
```

## 7.5 LLM fallback

MVP 可以先不接 LLM fallback，或者只做离线命令：

```text
focusledger classify unknown --llm
```

不要在采集 loop 中同步调用 LLM。

原因：

```text
会增加延迟
会增加成本
失败会影响后台采集
隐私边界更复杂
```

## 7.6 验收标准

完成后应满足：

```text
1. summary 能按分类汇总时间
2. Chrome / YouTube / Steam / IDE 能被基础分类
3. manual correction 能覆盖自动分类
4. unknown 项能被列出供用户修正
5. 分类结果能进入日报数据源
```

---

# 8. Phase 6：Deliverable / Todo 管理

## 8.1 目标

把真实活动时间和具体 deliverable 绑定。

FocusLedger 的重点不是“今天忙了多久”，而是：

```text
今天是否推进了真正重要的任务？
哪些 deliverable 有有效进展？
哪些任务被拖延？
```

## 8.2 交付物

Deliverable 管理：

```text
create deliverable
list deliverables
update status
set priority
set due date
archive deliverable
```

Task 管理：

```text
create task
link task to deliverable
mark task done
estimate remaining time
record blocker
```

CLI 命令建议：

```text
focusledger deliverable add
focusledger deliverable list
focusledger deliverable done
focusledger task add
focusledger task done
focusledger plan today
```

数据表建议：

```text
deliverables
tasks
deliverable_activity_links
daily_deliverable_notes
```

## 8.3 Activity 与 deliverable 的关联

MVP 先支持三种方式：

```text
manual link
project path match
keyword match
```

后续再做 LLM semantic matching。

示例：

```text
PyCharm window title contains FocusLedger
→ link to FocusLedger MVP deliverable

Chrome URL contains github.com/.../FocusLedger
→ link to FocusLedger MVP deliverable
```

## 8.4 用户手写进展

日报生成前必须让用户输入：

```text
What did you actually move forward today?
What is still blocked?
What should be prioritized tomorrow?
```

原因：

```text
客观行为只能说明用户看了什么、打开了什么
不能可靠判断真实产出
日报不能完全由系统自动生成
```

## 8.5 验收标准

完成后应满足：

```text
1. 用户可以创建 deliverable 和 task
2. activity segment 可以关联到 deliverable
3. summary 能显示每个 deliverable 的 estimated effective time
4. 下班日报前能收集用户手写进展
5. 未推进的 deliverable 能被日报识别
```

---

# 9. Phase 7：提醒与 Terminal 交互

## 9.1 目标

让 FocusLedger 从“事后统计”变成“当下干预”。

这一阶段要回答：

```text
用户明显分心时是否提醒？
用户是否能标注中断原因？
到下班时间是否进入日报流程？
未完成日报是否能第二天补写？
```

## 9.2 交付物

提醒类型：

```text
distraction_detected
offwork_time
daily_review_required
missing_daily_review
agent_missing_data
```

Terminal prompt：

```text
分心原因 multi-select
下班 / 加班 / 暂停 / 稍后提醒
日报输入流程
补日报流程
```

音频系统：

```text
play sound
mute
cooldown
test audio
fallback if audio fails
```

配置项：

```text
audio_enabled
audio_volume
notification_cooldown_seconds
daily_review_reminder_interval_seconds
max_review_reminders
mute_until
```

## 9.3 分心提醒

触发条件示例：

```text
gaming > 10 minutes during active workday
entertainment > 15 minutes during active workday
attention_gap > threshold
private browsing during active workday
```

提醒后询问：

```text
What happened?

[ ] intentional break
[ ] procrastination
[ ] waiting for something
[ ] research rabbit hole
[ ] emotional distraction
[ ] unclear task
[ ] other
```

这些标签进入日报，不要全部粗暴归类为 distraction。

## 9.4 下班日报流程

到达下班时间后：

```text
播放提示音
terminal 弹出选择
用户选择 End workday
用户填写 deliverable 进展
系统生成日报
日报保存为 markdown
workday 标记 ended
```

如果用户选择 overtime：

```text
workday 继续 active
后续日报标记 overtime productive time
```

## 9.5 验收标准

完成后应满足：

```text
1. 分心达到阈值后有提醒
2. 提醒有 cooldown，不会持续打断
3. 用户可以标注分心原因
4. 到下班时间能进入日报流程
5. 未完成日报第二天能提醒补写
6. 假期 / vacation 不触发日报提醒
```

---

# 10. Phase 8：LLM 日报与明日计划

## 10.1 目标

结合客观行为数据和用户手写输入，生成完整日报和明日计划。

日报不应完全自动生成。推荐流程：

```text
系统汇总客观数据
→ 用户填写 deliverable 进展
→ LLM 生成完整日报
→ 用户可修正
→ 保存 markdown journal
```

## 10.2 日报输入

LLM 输入应包含：

```text
workday time range
objective stats
classified activity summary
attention gaps
distraction labels
deliverables and tasks
user-written daily notes
missing data intervals
manual corrections
language preference
```

## 10.3 日报输出

日报 markdown 应包含：

```text
workday 时间范围
客观设备行为统计
用户手写 deliverable 进展
中断 / 分心原因
娱乐 / 游戏 / 泛知识 / 核心生产力分类
LLM 生成的今日复盘
LLM 生成的明日计划
是否加班
是否有 missing data
manual corrections
```

建议 markdown 结构：

```markdown
# Daily Review: 2026-05-06

## Workday

## Objective Stats

## Deliverable Progress

## Attention and Distractions

## Review

## Tomorrow Plan

## Missing Data

## Manual Corrections
```

## 10.4 Tomorrow plan

明日计划必须具体到 deliverable / task。

不建议只生成：

```text
明天继续努力，提高专注。
```

应该生成：

```text
1. 09:30-11:00 完成 MicroK8s deployment 文档
2. 11:15-12:00 复习 SFA
3. 14:00-15:30 复习 VAE 公式和推导
```

## 10.5 LLM 失败策略

LLM 调用失败时，不应导致日报流程失败。

Fallback：

```text
保存 objective stats
保存用户手写 notes
标记 llm_review_status = failed
允许稍后重新生成
```

CLI 命令建议：

```text
focusledger review generate --date 2026-05-06
focusledger review regenerate --date 2026-05-06
focusledger review list
```

## 10.6 验收标准

完成后应满足：

```text
1. 用户下班后可以完成日报输入
2. LLM 能生成完整日报
3. 日报保存为 markdown
4. 明日计划能基于 deliverable 生成
5. LLM 失败时仍保存基础日报
6. 用户可以补写未完成日报
```

---

# 11. Phase 9：后台化与 MVP 打磨

## 11.1 目标

让 FocusLedger 成为可以长期运行的本地工具。

这一阶段重点不是加新功能，而是让前面的闭环稳定：

```text
开机自动运行
后台持续记录
异常后能恢复
用户能知道 agent 状态
数据 schema 可迁移
基础测试覆盖关键逻辑
```

## 11.2 交付物

后台化：

```text
user-level autostart
background agent
single instance lock
graceful shutdown
crash recovery
```

Windows MVP：

```text
Startup Folder autostart
system tray status
pause / resume from tray
open terminal review prompt
```

工程质量：

```text
database migrations
config validation
structured logs
unit tests
integration tests for collector loop
packaging script
```

## 11.3 Autostart 策略

MVP 推荐：

```text
Windows Startup Folder
```

暂不推荐：

```text
Windows Service
```

原因：

```text
Windows Service 对 MVP 过重
桌面窗口、用户权限、音频提醒、terminal 交互都会更复杂
```

## 11.4 System tray

MVP tray 状态：

```text
Tracking active
Paused
Offwork
Missing data
Review required
```

Tray 操作：

```text
Pause tracking
Resume tracking
Open status
Start daily review
Quit
```

## 11.5 验收标准

完成后应满足：

```text
1. Windows 登录后自动启动 FocusLedger
2. agent 长时间运行不会明显占用资源
3. 重启后能继续 workday 或正确标记 missing data
4. 用户可以从 tray 看到状态
5. 数据库 migration 可重复执行
6. 核心服务有测试覆盖
```

---

# 12. Post-MVP

以下内容不进入第一版 MVP。

只有在每日闭环稳定后再做。

## 12.1 桌面 UI

可选技术：

```text
Tauri + React + Rust collector + Python LLM service
```

适合做：

```text
dashboard
timeline viewer
daily review editor
settings UI
classification correction UI
```

不建议在 MVP 初期做。

原因：

```text
UI 会放大工程量
核心风险不在 UI，而在数据采集、分类和日报可信度
```

## 12.2 Screenshot / OCR

适合解决：

```text
网页标题不明确
应用内容无法通过 URL/title 判断
视频内容分类不准确
```

风险：

```text
隐私成本高
存储成本高
性能成本高
需要 redaction / blacklist
```

## 12.3 云同步 / 多设备

MVP 只预留：

```text
device_id
source_device
sync_status
```

暂不实现真实同步。

原因：

```text
多设备合并会显著增加 workday 边界复杂度
第一版先验证单设备 Windows 闭环
```

## 12.4 手机端监控

暂不做。

原因：

```text
平台权限复杂
采集能力受限
与桌面 MVP 的核心验证关系不大
```

---

# 13. MVP 成功标准

MVP 完成时，FocusLedger 应该稳定支持：

```text
1. 自动后台记录 Windows active app / window title
2. 识别 idle 和 attention gap
3. 按 workday 而不是自然日切分
4. 采集浏览器 URL / title / audio / mode
5. 将行为分类为 productive、learning、entertainment、gaming、social、idle 等
6. 管理 deliverables 和 tasks
7. 将 activity 与 deliverable 关联
8. 分心时音频 + terminal 提醒
9. 用户标注中断原因
10. 到下班时间要求用户填写 deliverable 进展
11. LLM 生成完整日报和明日计划
12. 保存 markdown journal
13. 第二天能补写漏掉的日报
14. 假期 / vacation 能正确跳过提醒
15. 后台 agent 能开机自启动
```

成功日报应包含：

```text
workday 时间范围
客观设备行为统计
用户手写 deliverable 进展
中断 / 分心原因
娱乐 / 游戏 / 泛知识 / 核心生产力分类
LLM 生成的今日复盘
LLM 生成的明日计划
是否加班
是否有 missing data
```

---

# 14. 推荐近期开发任务

第一周建议只做：

```text
1. 建立 Python 项目骨架
2. 建立 SQLite schema 和 migration
3. 实现 CLI init / start / status
4. 实现 Windows active window collector
5. 写入 raw_activity_events
6. 合并 activity_segments
7. 实现 summary --today
```

第二周建议做：

```text
1. GetLastInputInfo idle detection
2. attention state
3. attention gap table
4. workday start / offwork / pause / resume
5. missing data detection
6. timeline 查询优化
```

第三周再进入：

```text
1. browser extension / native messaging
2. URL/title/audio/mode collection
3. rule-based classifier
4. 基础日报 objective stats
```

---

# 15. 开发时的硬边界

为了避免 MVP 失控，前期应坚持：

```text
不做漂亮 UI
不做 OCR
不做云同步
不做手机端
不做 Windows Service
不做复杂机器学习分类
不在采集 loop 中同步调用 LLM
不把日报完全交给 LLM 自动生成
```

优先保证：

```text
数据采集稳定
workday 边界可信
idle / missing data 明确
分类可解释
用户能修正
日报可读
```

