# FocusLedger 项目提案

## 1. 项目名称

**FocusLedger：基于设备行为与 LLM Agent 的个人生产力审计系统**

FocusLedger 是一个面向个人使用的本地优先生产力监督工具。它通过自动记录用户在电脑上的应用使用情况、浏览器上下文、键盘鼠标空窗期、音频播放状态、todo/deliverable 进展，并结合规则系统与 LLM Agent，对用户每天的真实工作状态进行结算、分析和规划。

项目核心目标不是简单记录“打开了什么应用”，而是判断：

> 用户今天是否真正推进了计划中的 deliverables，以及有效投入了多少时间。

---

## 2. 项目背景

在假期或缺乏外部约束的情况下，用户容易出现以下问题：

- 长时间打游戏、刷网页、看视频；
- 打开工作软件但实际没有持续工作；
- 原计划完成的学习、项目、求职、论文阅读等任务不断被推迟；
- 普通 todo list 只能记录计划，不能验证实际执行；
- 普通屏幕时间工具只能统计应用使用，不能判断这些时间是否与目标相关。

FocusLedger 希望解决这个问题：  
它将主观计划和客观行为连接起来，形成一个个人生产力账本。

系统每天回答以下问题：

1. 今天每个应用用了多久？
2. 哪些时间是核心生产力时间？
3. 哪些时间是一般学习、泛知识、娱乐、游戏或分心？
4. 是否存在长时间键鼠输入空窗期？
5. 哪些 todo/deliverable 有实际推进？
6. 哪些计划没有完成？
7. 明天应该如何重新规划？

---

## 3. 产品定位

FocusLedger 不是普通 todo app，也不是简单的屏幕时间统计工具。

普通 todo app 只能回答：

```text
你计划做什么？
```

普通屏幕时间工具只能回答：

```text
你打开了哪些应用？
```

FocusLedger 要回答：

```text
你今天真正做了什么？
你有效工作了多久？
你是否推进了计划中的 deliverables？
你在哪些时间段分心、娱乐、游戏或进入注意力空窗？
明天应该如何更现实地安排？
```

核心定位：

> **基于真实设备行为数据的个人生产力审计系统。**

---

## 4. 目标用户

初始版本只服务单个用户，不考虑多人协作或商业化场景。

目标用户包括：

- 放假期间缺乏自律结构的学生；
- 需要自主管理学习、项目、求职和研究任务的人；
- 容易沉迷游戏、手机、视频或社交平台的人；
- 希望通过行为数据复盘提升生产力的人；
- 希望用 LLM Agent 辅助拆解 todo、规划日程和生成每日复盘的人。

---

## 5. 设计原则

### 5.1 自动优先

系统不依赖用户手动开始 focus session。  
系统应持续自动记录并自动推断当前活动状态。

原始设计中的手动 focus session 和不同任务注意力模式被移除。

系统采用：

```text
全天候自动记录
统一 idle 阈值
自动生成 activity segments
自动分类 app / URL / 音频 / todo 相关性
自动结算有效时间
```

### 5.2 本地优先

涉及窗口标题、浏览器 URL、截图、todo、行为数据等敏感信息，系统应默认本地处理和存储。

后续如加入截图/OCR/LLM 视觉分析，应遵循：

- 默认不上传原始截图；
- 敏感窗口不截图；
- 截图定期删除；
- 上传给 LLM 前先过滤、打码或转成摘要；
- 用户可审查和删除历史数据。

### 5.3 保守判断生产力

系统不应轻易把模糊行为算作生产力。

例如：

- 打开 Chrome 不等于 productive；
- 打开 ChatGPT 不等于 productive；
- 看 YouTube 不等于学习；
- 有知识含量的视频不一定与当前 deliverable 有关；
- 打开 PyCharm 但长时间无输入，不应完整计入 coding 时间。

原则：

> 只有与当前 todo/deliverable 明确相关的活动，才应计入 core productive time。

### 5.4 可人工修正

系统判断可能出错，因此用户应能手动修正分类。  
但系统需要保留原始系统判定和人工覆盖记录，方便审计。

---

## 6. 核心模块

系统由以下模块组成：

```text
1. Desktop Activity Collector
2. Browser Context Collector
3. Unified Attention Detector
4. Activity Classifier
5. Todo & Deliverable Manager
6. Time Ledger
7. LLM Agent
8. Daily Review Dashboard
```

---

# 7. Desktop Activity Collector

## 7.1 功能描述

该模块负责自动记录当前电脑前台应用和窗口标题。

采集字段包括：

- 时间戳；
- 前台应用名；
- 进程名；
- 窗口标题；
- 当前是否有键盘/鼠标输入；
- 最大 idle 时间；
- 当前 segment 是否连续；
- 是否与某个 todo/deliverable 相关。

示例：

```text
10:00 - 10:42  PyCharm      README.md - air-quality-monitoring-pipeline
10:42 - 10:57  Chrome       ChatGPT - Project planning
10:57 - 11:31  Chrome       YouTube - Variational Autoencoders Explained
11:31 - 12:10  Steam        Game
12:10 - 12:40  No input     Idle / attention gap
```

## 7.2 目标

该模块用于回答：

```text
用户今天每个应用用了多久？
用户主要时间花在哪里？
当前活动是否可能和工作/学习相关？
```

---

# 8. Browser Context Collector

## 8.1 功能描述

浏览器是最复杂的应用，因为它既可能是生产力工具，也可能是分心来源。

该模块负责采集浏览器上下文：

- 浏览器名称；
- 当前活跃 tab 的 URL；
- 当前页面标题；
- domain；
- 是否播放音频或视频；
- 是否处于无痕/隐私窗口。

示例数据：

```json
{
  "browser": "Chrome",
  "url": "https://www.youtube.com/watch?v=example",
  "domain": "youtube.com",
  "page_title": "Variational Autoencoders Explained - YouTube",
  "audio_playing": true,
  "browser_mode": "normal"
}
```

## 8.2 浏览器分类逻辑

浏览器活动不应只按 app 名分类，而应按 domain、URL、标题、音频状态和 todo 相关性分类。

基础规则：

```text
github.com              -> likely productive
arxiv.org               -> study / paper reading
docs.python.org         -> productive documentation
pytorch.org             -> productive documentation
youtube.com             -> requires video content classification
reddit.com              -> usually distraction unless technical thread
amazon.com              -> shopping/admin/distraction depending context
chatgpt.com             -> ambiguous, classify by title/content/context
```

---

# 9. Browser Privacy Mode Policy

## 9.1 无痕窗口规则

只要检测到浏览器处于无痕或隐私窗口，系统直接记录为 distraction。

规则：

```text
if browser_private_mode_detected:
    browser_mode = "incognito" or "private"
    content_label = "distraction"
    productivity_label = "distraction"
    effective_seconds = 0
```

系统不解析无痕窗口中的 URL，不调用 LLM 判断内容，也不尝试将其算作生产力。

原因：

- 无痕模式的设计目标是降低可追踪性；
- 这与生产力审计系统的目标冲突；
- 如果允许无痕窗口被算作 productive，会形成绕过审计的灰色区域；
- 只记录“使用了无痕窗口”比记录其具体内容更符合隐私边界。

## 9.2 记录方式

无痕窗口记录示例：

```json
{
  "app_name": "Chrome",
  "browser_mode": "incognito",
  "content_label": "distraction",
  "productivity_label": "distraction",
  "effective_seconds": 0,
  "reason": "Incognito/private browsing detected. Treated as distraction by policy."
}
```

## 9.3 手动修正

用户可以事后手动改标，但系统保留原始判定。

示例：

```json
{
  "system_label": "distraction",
  "user_label": "productive_claimed",
  "reason": "User manually overrode incognito classification."
}
```

建议即使用户手动修正，无痕窗口也不要默认 100% 计入生产力。  
可以设置：

```text
productive_claimed_weight = 0.5
```

---

# 10. Unified Attention Detector

## 10.1 设计目标

系统不使用摄像头，也不检测人脸或视线。  
注意力检测基于键盘鼠标输入空窗期。

核心问题：

```text
工作软件打开了，但用户是否真的持续在工作？
```

例如：

```text
PyCharm 打开 2 小时
```

不代表用户真的 coding 2 小时。用户可能中途看手机、离开电脑或发呆。

## 10.2 统一阈值

系统不区分 coding、reading、lecture 等不同注意力场景，统一使用一套 idle 阈值。

推荐配置：

```yaml
attention_thresholds:
  low_activity_after_minutes: 3
  attention_gap_after_minutes: 10
  interrupted_after_minutes: 20
  hard_idle_after_minutes: 30
```

解释：

| 无键鼠输入时长 | 状态 | 默认处理 |
|---:|---|---|
| 0-3 min | active | 正常计入 |
| 3-10 min | low_activity | 部分计入 |
| 10-20 min | attention_gap | 默认不计入，除非有音频/视频/阅读证据 |
| 20-30 min | interrupted | 认为当前活动中断 |
| 30+ min | hard_idle | 认为离开、休息、手机分心或长时间无效 |

## 10.3 注意力权重

推荐初始权重：

```yaml
attention_weights:
  active: 1.0
  low_activity: 0.5
  attention_gap: 0.0
  interrupted: 0.0
  hard_idle: 0.0
```

但对于正在播放音频/视频的 lecture-like 场景，可以结合内容分类修正。

例如：

```text
如果无输入超过 10 分钟：
  - 没有音频/视频播放：attention_weight = 0
  - 有音频/视频播放：根据内容分类决定是否计入
```

---

# 11. 视频与音频场景判断

## 11.1 设计动机

Lecture 或视频学习场景下，用户可能长时间没有键鼠输入，但仍然在听课或看视频。  
因此不能只根据 idle time 判定为分心。

但“正在播放音频”也不等于 productive。  
需要进一步判断视频内容是否和当前目标相关。

## 11.2 判断依据

系统应结合：

- 是否正在播放音频；
- 是否为视频或音频网站；
- URL；
- 页面标题；
- 视频标题；
- domain；
- 是否与当前 todo/deliverable 相关；
- LLM 对标题和上下文的分类。

## 11.3 视频内容分类

视频/音频内容采用以下 taxonomy：

```text
productive_core
  - 与当前 todo/deliverable 高度相关
  - 例如课程、论文解读、项目教程、技术文档视频

productive_general
  - 对学习或工作有帮助，但不直接对应当前 todo
  - 例如通用编程技巧、职业发展、英语学习

neutral_educational
  - 泛知识、科普、历史、纪录片
  - 有信息价值，但与当前目标无关

entertainment
  - 游戏视频、综艺、影视、搞笑、直播、音乐

distraction
  - 短视频、推荐流、八卦、reaction、无目的浏览

unknown
  - 信息不足，无法判断
```

## 11.4 视频内容权重

推荐权重：

```yaml
video_content_weights:
  productive_core: 1.0
  productive_general: 0.7
  neutral_educational: 0.3
  entertainment: 0.0
  distraction: 0.0
  unknown: 0.1
```

示例：

| 当前活动 | 分类 | 计入方式 |
|---|---|---|
| YouTube: Variational Autoencoders Explained | productive_core | 核心学习 |
| YouTube: Kubernetes Deployment Tutorial | productive_core | 核心生产力 |
| YouTube: General Python Tips | productive_general | 一般学习 |
| YouTube: History of Rome Documentary | neutral_educational | 泛知识 |
| YouTube: Elden Ring Boss Fight | entertainment | 娱乐 |
| Bilibili: 随机推荐流 | distraction | 分心 |

---

# 12. Activity Classifier

## 12.1 分类目标

系统需要将每个 activity segment 分类为：

```text
coding
study
reading
writing
planning
admin_work
job_application
video_learning
neutral_educational
gaming
social_media
entertainment
shopping
idle
attention_gap
unknown
```

同时输出更高层的 productivity label：

```text
core_productive
general_learning
neutral_educational
admin
entertainment
gaming
social
distraction
idle
unknown
```

## 12.2 分类优先级

推荐分类顺序：

```text
1. 是否为无痕/隐私窗口
2. 是否为游戏应用
3. 是否为明确娱乐网站或社交平台
4. 是否为明确生产力应用
5. 是否为浏览器活动
6. 如果是浏览器，判断 domain / URL / title
7. 如果有音频/视频播放，进行内容分类
8. 计算 todo/deliverable 相关性
9. 如果规则无法判断，调用 LLM
10. 写入 activity segment
```

无痕窗口规则优先级最高：

```text
无痕窗口 -> 直接 entertainment
```

## 12.3 LLM 使用策略

LLM 不应每分钟全量调用。  
系统应先用规则判断大部分场景，只把 ambiguous cases 交给 LLM。

适合调用 LLM 的情况：

- YouTube 视频标题分类；
- ChatGPT 页面是否和 todo 相关；
- Chrome 页面是否属于生产力；
- 页面标题模糊但可能和当前任务有关；
- 科普内容是否与当前 deliverable 有关。

LLM 分类结果应缓存，避免重复调用。

---

# 13. Todo & Deliverable Manager

## 13.1 设计原则

FocusLedger 的任务系统应围绕 deliverable，而不是普通 checkbox。

Deliverable 代表一个真正需要完成的结果，例如：

```text
Finish Air Quality Monitoring deployment documentation
Review ML exam topics
Polish resume project section
Submit two job applications
```

Task 是 deliverable 下的具体行动。

## 13.2 Deliverable 示例

```json
{
  "id": "deliv_001",
  "title": "Finish Air Quality Monitoring deployment documentation",
  "deadline": "2026-05-12",
  "priority": "high",
  "status": "in_progress",
  "definition_of_done": [
    "README contains full deployment steps",
    "MicroK8s setup documented",
    "MinIO and PostgreSQL inspection steps included",
    "Troubleshooting section added"
  ]
}
```

## 13.3 Task 示例

```json
{
  "id": "task_001",
  "deliverable_id": "deliv_001",
  "title": "Write MicroK8s deployment section",
  "estimated_minutes": 45,
  "actual_minutes": 0,
  "effective_minutes": 0,
  "status": "not_started",
  "priority": "high"
}
```

## 13.4 自动 todo 规划

用户可以通过对话输入：

```text
我这周要完成 pipeline deployment 文档，还要复习 ML exam。
```

LLM Agent 自动拆成 deliverables 和 tasks：

```text
Deliverable 1: Finish pipeline deployment documentation
- Write MicroK8s setup section
- Write MinIO setup section
- Write PostgreSQL inspection section
- Add troubleshooting section

Deliverable 2: Review ML exam topics
- Review VAE
- Review SFA
- Review RBM / DBM
- Review Bayesian inference
```

---

# 14. Time Ledger

## 14.1 核心公式

系统每天结算时使用以下公式：

```text
effective_time
=
raw_time
× app_or_site_productivity_weight
× attention_weight
× content_relevance_weight
× todo_relevance_weight
```

各项含义：

- `raw_time`：原始持续时间；
- `app_or_site_productivity_weight`：应用或网站基础权重；
- `attention_weight`：键鼠活跃度权重；
- `content_relevance_weight`：内容本身是否生产力相关；
- `todo_relevance_weight`：是否和当前 todo/deliverable 相关。

## 14.2 示例

```text
Chrome 打开 YouTube 30 分钟
音频播放中
标题：Variational Autoencoders Explained
当前 todo：Review VAE

raw_time = 30min
app_or_site_productivity_weight = 0.8
attention_weight = 1.0
content_relevance_weight = 1.0
todo_relevance_weight = 1.0

effective_time = 24min
```

另一个例子：

```text
Chrome 打开 YouTube 30 分钟
音频播放中
标题：Elden Ring Boss Fight Compilation
当前 todo：Review VAE

content_relevance_weight = 0.0
todo_relevance_weight = 0.0

effective_time = 0min
记录为 entertainment
```

---

# 15. LLM Agent

## 15.1 Agent 角色

LLM Agent 是系统中的任务规划器和每日复盘教练。

职责包括：

- 从自然语言中提取 deliverables；
- 自动拆分 tasks；
- 根据 deadline 和历史行为安排优先级；
- 读取当天 activity ledger；
- 判断哪些任务有实际推进；
- 生成每日复盘；
- 生成明日计划；
- 对长时间游戏、娱乐、无痕窗口使用或 attention gap 提出提醒。

## 15.2 Agent 可调用工具

```json
[
  {
    "name": "get_today_activity_summary",
    "description": "Return today's classified activity timeline."
  },
  {
    "name": "create_deliverable",
    "description": "Create a new deliverable."
  },
  {
    "name": "create_task",
    "description": "Create a task under a deliverable."
  },
  {
    "name": "update_task_status",
    "description": "Update task status."
  },
  {
    "name": "classify_url",
    "description": "Classify a URL or video title."
  },
  {
    "name": "match_activity_to_todo",
    "description": "Estimate whether an activity segment is related to active todos."
  },
  {
    "name": "generate_daily_review",
    "description": "Generate daily productivity review."
  },
  {
    "name": "plan_tomorrow",
    "description": "Generate next-day task plan."
  }
]
```

## 15.3 视频/网页分类 Prompt 草案

```text
You are classifying a web page or video for a personal productivity tracker.

The user has the following active deliverables and todos:
{todos}

Current page:
- URL: {url}
- Title: {title}
- Domain: {domain}
- Audio playing: {audio_playing}

Classify the content into one of:
productive_core, productive_general, neutral_educational, entertainment, distraction, unknown.

Definitions:
- productive_core: directly related to an active todo or deliverable.
- productive_general: useful for study/work, but not directly tied to current todo.
- neutral_educational: informational or educational, but unrelated to current goals.
- entertainment: leisure content such as gaming videos, movies, comedy, music, livestreams.
- distraction: short-form feeds, random browsing, clickbait, social media scrolling.
- unknown: insufficient information.

Return JSON only:
{
  "label": "...",
  "topic": "...",
  "matched_todo_id": "... or null",
  "confidence": 0.0-1.0,
  "reason": "..."
}
```

---

# 16. Daily Review

## 16.1 每日结算类别

日报应包含以下类别：

```text
Core productive time
General learning time
Neutral educational time
Admin time
Entertainment time
Gaming time
Social time
Entertainment time, including incognito/private browsing
Attention gap / idle time
```

## 16.2 日报示例

```text
今日总结：2026-05-06

总览：
- 核心生产力时间：2h 15m
- 一般学习时间：45m
- 泛知识/不相关科普：38m
- 行政事务：20m
- 游戏：3h 20m
- 娱乐视频：1h 10m
- 社交聊天：50m
- 娱乐时间：1h10m
- 注意力空窗/idle：1h 05m

Deliverable 进展：
1. Air Quality Monitoring deployment documentation
   - 今日推进：部分完成 MicroK8s setup section
   - 估计剩余：3h

2. ML exam review
   - 今日推进：观看 VAE lecture 24m effective time
   - 尚未复习 SFA / RBM / DBM

主要问题：
- Steam 使用时间超过计划
- 下午出现 31m attention gap
- 晚上出现 25m incognito browsing，默认计入 distraction

明日建议：
1. 优先完成 MicroK8s deployment documentation
2. 复习 SFA 和 VAE
3. 将游戏预算限制为 1h30m
4. 避免使用无痕窗口绕过审计
```

---

# 17. 数据库设计草案

## 17.1 activity_segments

```sql
CREATE TABLE activity_segments (
    id TEXT PRIMARY KEY,
    start_time DATETIME,
    end_time DATETIME,
    raw_seconds INTEGER,
    effective_seconds INTEGER,

    app_name TEXT,
    process_name TEXT,
    window_title TEXT,

    browser_mode TEXT,
    url TEXT,
    domain TEXT,
    page_title TEXT,
    audio_playing BOOLEAN,

    idle_seconds_max INTEGER,
    attention_state TEXT,
    attention_weight REAL,

    content_label TEXT,
    activity_type TEXT,
    productivity_label TEXT,

    matched_todo_id TEXT,
    matched_deliverable_id TEXT,
    todo_relevance_score REAL,

    system_label TEXT,
    user_label TEXT,
    user_override BOOLEAN,

    confidence REAL,
    reason TEXT
);
```

## 17.2 classified_urls

```sql
CREATE TABLE classified_urls (
    id TEXT PRIMARY KEY,
    url TEXT,
    domain TEXT,
    page_title TEXT,
    content_label TEXT,
    topic TEXT,
    matched_todo_id TEXT,
    confidence REAL,
    reason TEXT,
    classified_at DATETIME
);
```

## 17.3 deliverables

```sql
CREATE TABLE deliverables (
    id TEXT PRIMARY KEY,
    title TEXT,
    description TEXT,
    deadline DATETIME,
    priority TEXT,
    status TEXT,
    definition_of_done TEXT,
    created_at DATETIME,
    updated_at DATETIME
);
```

## 17.4 tasks

```sql
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    deliverable_id TEXT,
    title TEXT,
    description TEXT,
    estimated_minutes INTEGER,
    actual_minutes INTEGER,
    effective_minutes INTEGER,
    status TEXT,
    due_date DATETIME,
    priority TEXT
);
```

## 17.5 daily_reviews

```sql
CREATE TABLE daily_reviews (
    id TEXT PRIMARY KEY,
    date DATE,

    core_productive_minutes INTEGER,
    general_learning_minutes INTEGER,
    neutral_educational_minutes INTEGER,
    admin_minutes INTEGER,
    entertainment_minutes INTEGER,
    gaming_minutes INTEGER,
    social_minutes INTEGER,
    incognito_minutes INTEGER,
    attention_gap_minutes INTEGER,
    idle_minutes INTEGER,

    summary TEXT,
    next_day_plan TEXT
);
```

---

# 18. 技术栈建议

## 18.1 MVP 技术栈

推荐先用 Python 快速开发：

```text
Language: Python
Backend: local background agent; FastAPI optional later
Database: SQLite
Desktop Collector: pywin32 / ctypes
Idle Detection: Windows GetLastInputInfo
Browser Context: browser extension or local native messaging
UI: Terminal-first CLI / optional TUI later
LLM: OpenAI API or local Ollama
Storage: local filesystem
```

## 18.2 后续正式版

如果后续做成完整桌面 app：

```text
Tauri + React + Rust collector + Python LLM service
```

优势：

- 桌面体验更好；
- 系统资源占用低；
- 更适合长期维护；
- 更容易打包为正式 app。

---

# 19. 开发路线图

## Phase 1：基础应用记录

目标：

```text
知道用户每天每个 app 用了多久。
```

功能：

- active window tracking；
- process name tracking；
- window title tracking；
- SQLite 存储；
- 基础 dashboard。

## Phase 2：统一 idle 检测

目标：

```text
识别键鼠输入空窗期，防止假工作时间被完整计入。
```

功能：

- GetLastInputInfo；
- 统一 idle 阈值；
- attention state；
- attention gap 记录；
- 返回电脑后允许用户修正。

## Phase 3：浏览器上下文采集

目标：

```text
识别浏览器里的网页、视频、音频和无痕窗口。
```

功能：

- active tab URL；
- page title；
- domain；
- audio/video playing；
- browser_mode；
- incognito/private detection。

## Phase 4：Activity Classifier

目标：

```text
根据 app、URL、title、audio、idle、todo 相关性自动分类活动。
```

功能：

- rule-based classifier；
- URL classifier；
- video/audio classifier；
- incognito policy；
- LLM fallback；
- classified_url cache。

## Phase 5：Todo / Deliverable 管理

目标：

```text
把真实活动时间和具体 deliverable 绑定。
```

功能：

- deliverable CRUD；
- task CRUD；
- todo relevance matching；
- effective time aggregation。

## Phase 6：LLM Agent 与 Daily Review

目标：

```text
自动规划任务、生成每日复盘和明日计划。
```

功能：

- natural language todo creation；
- automatic task breakdown；
- daily review；
- tomorrow planning；
- reminders and interventions。

## Phase 7：截图/OCR 增强，可选

目标：

```text
更准确地区分模糊网页或应用内容。
```

功能：

- periodic screenshot；
- OCR；
- sensitive window blacklist；
- local redaction；
- LLM semantic classification。

---

# 20. MVP 范围

## 必须完成

```text
1. Windows active app / window title tracking
2. Keyboard/mouse idle detection
3. Unified attention thresholds
4. Automatic activity segment generation
5. Browser URL/title/audio/mode collection
6. Incognito/private browsing policy
7. Rule-based activity classification
8. Video/audio content classification
9. Todo/deliverable management
10. Effective time calculation
11. Daily review generation
12. LLM planning agent
13. Background agent
14. User-level autostart
15. System tray status
16. Pause / resume tracking
17. Agent heartbeat and missing data detection
```

## 暂不需要

```text
1. 手动 focus session
2. 不同注意力模式
3. 摄像头检测
4. 手机端监控
5. 系统级强制锁应用
6. 多用户账户系统
7. 云端同步
```

---

# 21. 风险与解决方案

## 21.1 误判风险

系统可能误判：

- 用户在读论文但无输入；
- 用户在看相关课程但标题不清晰；
- 用户在 ChatGPT 中做正事但页面标题不充分；
- 用户在泛知识学习，但系统误判为 productive。

解决方案：

- 保守判断；
- 允许用户手动修正；
- 保存 system_label 和 user_label；
- ambiguous cases 调用 LLM；
- 引入 todo relevance score。

## 21.2 隐私风险

浏览器 URL、窗口标题和后续截图都可能敏感。

解决方案：

- local-first；
- 默认不上传敏感数据；
- 无痕窗口不解析 URL；
- 截图功能后置；
- 敏感窗口黑名单；
- 用户可删除历史数据。

## 21.3 LLM 成本和稳定性

如果每分钟调用 LLM，会成本高且不稳定。

解决方案：

- 规则优先；
- LLM 只处理 ambiguous cases；
- URL 分类结果缓存；
- 低置信度结果等待用户修正。

## 21.4 用户逃避系统

用户可能使用无痕窗口、关闭 app 或手动改标。

解决方案：

- 无痕窗口默认 distraction；
- 手动覆盖保留审计记录；
- daily review 明确展示 incognito/private browsing time；
- 重点是自我监督，不做强制控制。

---

# 22. 成功标准

MVP 成功标准：

系统每天能稳定回答：

```text
1. 今天每个应用用了多久？
2. 今天有效生产力时间是多少？
3. 哪些时间属于核心 productive？
4. 哪些时间属于 general learning 或 neutral educational？
5. 游戏、娱乐、社交分别用了多久？无痕/隐私浏览合并计入娱乐。
6. 今天有哪些 attention gaps？
7. 哪些 deliverables 有实际推进？
8. 明天应该优先做什么？
```

成功日报示例：

```text
今天 PyCharm 打开了 3h20m，但有效 coding 时间约 1h45m。
Chrome 使用 2h10m，其中 38m 为 ML 相关学习。
YouTube 使用 1h30m，其中 VAE lecture 24m 计入 core productive，游戏视频 40m 计入 entertainment，历史科普 26m 计入 neutral educational。
Steam 使用 3h50m。
Air Quality deployment doc 有部分推进，但 ML exam review 进度不足。
明天建议先完成 MicroK8s deployment 文档，再复习 SFA 和 VAE。
```

---


# 24. 开机自启动与后台常驻设计

## 24.1 设计目标

理想情况下，FocusLedger 应该是一个开机自启动、后台常驻的本地应用。

原因是生产力审计系统不能依赖用户每天手动打开。  
如果用户忘记启动应用，当天数据就会缺失，日报也会失真。

因此系统应满足：

```text
开机后自动启动
后台持续运行
自动开始采集应用/浏览器/idle 数据
异常退出后可恢复
用户可从托盘查看状态
用户可暂停或退出
```

目标行为：

```text
电脑开机
→ FocusLedger 自动启动
→ 后台 collector 开始运行
→ 托盘图标显示采集状态
→ 当天 activity ledger 自动生成
→ 晚上生成 daily review
```

---

## 24.2 应用运行形态

FocusLedger 不应只作为一个普通前台窗口运行，而应拆成两个层级：

```text
1. Background Service / Agent
2. User Interface / Dashboard
```

### Background Service / Agent

负责长期运行的数据采集和分类：

```text
active app tracking
window title tracking
keyboard/mouse idle detection
browser context collection
audio state detection
activity segment generation
local database writing
lightweight rule classification
reminder trigger
```

### User Interface / Dashboard

负责用户交互：

```text
查看今日统计
查看 activity timeline
管理 todo / deliverable
与 LLM Agent 对话
查看 daily review
修改错误分类
调整配置
暂停/恢复采集
```

这样即使用户没有打开 dashboard，后台 agent 也可以继续记录数据。

---

## 24.3 系统托盘设计

Windows MVP 建议提供 system tray 图标。

托盘菜单至少包括：

```text
Open Dashboard
Pause Tracking
Resume Tracking
Start Break
View Today's Summary
Settings
Quit FocusLedger
```

托盘状态可以显示：

```text
绿色：正在记录
黄色：暂停记录
红色：collector 异常
灰色：数据库不可用或未初始化
```

用户不需要一直打开主窗口，但应该能明确知道系统是否正在运行。

---

## 24.4 开机自启动策略

### Windows MVP

Windows 上可使用以下方式之一：

```text
Startup folder shortcut
Registry Run key
Windows Task Scheduler
Windows Service
```

MVP 推荐顺序：

```text
1. Startup folder shortcut
2. Windows Task Scheduler
3. Windows Service
```

### 推荐 MVP 方案：Startup Folder

第一版可以将 FocusLedger 的启动脚本或打包后的 exe 快捷方式放入：

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
```

优点：

```text
实现简单
权限要求低
适合个人使用
容易关闭
```

缺点：

```text
可靠性一般
用户登出前不会运行
不适合系统级后台服务
```

### 进阶方案：Task Scheduler

后续可以使用 Windows Task Scheduler：

```text
Trigger: At log on
Action: Start FocusLedger background agent
Run only when user is logged on
Restart on failure
```

优点：

```text
比 Startup folder 更可靠
可配置失败重启
适合正式版本
```

### 不建议 MVP 使用 Windows Service

Windows Service 更系统级，但对本项目 MVP 不必要。

原因：

```text
实现复杂
权限更麻烦
访问用户桌面窗口和浏览器上下文更困难
调试成本高
```

FocusLedger 需要读取当前用户的 foreground window、浏览器 tab、键鼠 idle 等用户会话信息，因此以 user-level background app 运行更合适。

---

## 24.5 后台进程拆分

推荐进程结构：

```text
focusledger_agent
  - 长期后台运行
  - 负责采集、分类、写数据库

focusledger_ui
  - 用户打开 dashboard 时运行
  - 负责展示、配置、LLM 对话

focusledger_browser_extension
  - 可选
  - 负责浏览器 URL/title/audio/incognito 状态采集
```

MVP 也可以先单进程实现：

```text
Python app
  - collector thread
  - classifier thread
  - local FastAPI server
  - Streamlit / simple web UI
```

但长期建议拆开，避免 UI 崩溃导致 collector 停止。

---

## 24.6 自启动后的默认行为

开机自动启动后，系统不应立即弹出主窗口打扰用户。

推荐行为：

```text
后台静默启动
托盘图标出现
collector 自动开始
首次安装后显示一次 onboarding
之后默认不弹主窗口
```

如果发生异常，例如数据库不可写、浏览器插件未连接、collector 崩溃，则可以显示通知。

---

## 24.7 暂停与隐私控制

即使是开机自启动应用，也必须允许用户随时暂停。

暂停模式：

```text
Pause for 15 minutes
Pause for 1 hour
Pause until tomorrow
Pause indefinitely
```

暂停后：

```text
停止采集窗口标题
停止采集浏览器上下文
停止 idle 记录
日报中记录 paused interval
```

日报中可以显示：

```text
14:00 - 15:30 Tracking paused by user
```

这样可以避免数据缺失被误判为 idle 或 distraction。

---

## 24.8 异常恢复

后台 agent 应定期写入 heartbeat。

新增表：

```sql
CREATE TABLE agent_heartbeats (
    id TEXT PRIMARY KEY,
    timestamp DATETIME,
    agent_version TEXT,
    status TEXT,
    message TEXT
);
```

用途：

```text
判断 agent 是否正常运行
发现采集中断时间段
日报中标注 missing data
支持异常恢复
```

如果系统检测到上一次 heartbeat 距离当前时间太久，可以记录：

```text
collector_gap
```

例如：

```text
09:10 - 10:25 FocusLedger agent not running
```

该时间段不应被推测为 idle 或 distraction，而应标记为：

```text
missing_data
```

---

## 24.9 数据完整性原则

开机自启动后，日报应区分：

```text
idle
attention_gap
paused
missing_data
```

四者不能混为一谈。

含义：

| 状态 | 含义 |
|---|---|
| idle | 系统运行中，但用户长时间无输入 |
| attention_gap | 工作/学习上下文中出现长时间无输入 |
| paused | 用户主动暂停采集 |
| missing_data | agent 未运行或异常，无法判断 |

这对于日报可信度很重要。

---

## 24.10 MVP 需求更新

开机自启动加入后，MVP 必须新增：

```text
1. Background agent
2. User-level autostart
3. System tray status
4. Pause / resume tracking
5. Agent heartbeat
6. Missing data detection
```

MVP 仍不需要：

```text
1. Windows Service
2. 系统级强制锁应用
3. 多用户账户系统
4. 云同步
```

---

## 24.11 Windows MVP 实现建议

建议 MVP 使用：

```text
Python background process
pystray system tray
SQLite local database
Startup folder shortcut
FastAPI local API
Streamlit or React dashboard
```

目录结构可以扩展为：

```text
focusledger/
  agent/
    main.py
    autostart.py
    heartbeat.py
    tray.py
    collector.py
    classifier.py
  api/
    main.py
  ui/
    streamlit_app.py
  db/
    models.py
    database.py
  config/
    attention.yaml
    rules.yaml
    privacy.yaml
```

`autostart.py` 负责：

```text
enable autostart
disable autostart
check autostart status
```

`heartbeat.py` 负责：

```text
write heartbeat every 30 seconds
detect last shutdown
record missing_data intervals
```

`tray.py` 负责：

```text
show tray icon
open dashboard
pause tracking
resume tracking
quit app
```

---

## 24.12 用户体验目标

最终用户体验应类似：

```text
开机后无需手动操作。
FocusLedger 在后台自动运行。
用户从托盘可以看到它正在记录。
晚上打开 dashboard 或对话窗口，即可看到当天真实生产力结算。
```

系统不应该变成一个需要用户每天维护的任务。  
它应该像一个本地生产力审计守护进程一样，持续、低打扰、可暂停、可审计。


# 25. 交互模式与工作制设计修订

## 25.1 当前 UI 策略：Terminal-first

当前版本不需要图形化 UI，不需要 Streamlit、React 或桌面窗口。  
MVP 采用 terminal-first 交互方式。

原因：

```text
开发简单
跨平台更容易
适合 Codex 快速实现
减少 UI 复杂度
核心先验证采集、分类、提醒、日报、LLM 规划
```

因此当前交互方式为：

```text
用户正常使用电脑
FocusLedger 后台运行
如果检测到分心或长时间异常，弹出 terminal 提醒
用户在 terminal 中选择原因
到下班点时，terminal 弹出日报输入流程
用户输入今日 deliverable 进展
LLM 生成完整日报和明日计划
日报保存为长期日记
```

后续可以再做：

```text
TUI
Web dashboard
Desktop tray app
Mobile companion
```

但 MVP 不需要。

---

## 25.2 跨平台支持

项目需要支持三系统：

```text
Windows
macOS
Linux
```

但不同系统采集能力不同，因此应采用统一接口 + 平台适配器。

推荐结构：

```text
focusledger/
  collectors/
    base.py
    windows.py
    macos.py
    linux.py
  attention/
    idle_detector.py
  browser/
    base.py
    chrome.py
    firefox.py
  cli/
    main.py
  agent/
    planner.py
  db/
    models.py
```

抽象接口：

```python
class ActivityCollector:
    def get_active_app(self) -> ActiveAppInfo:
        ...

    def get_idle_seconds(self) -> float:
        ...

    def get_audio_state(self) -> AudioState:
        ...
```

平台实现：

```text
Windows:
  - foreground window
  - process name
  - window title
  - GetLastInputInfo idle detection

macOS:
  - active application
  - window title if permission granted
  - idle time via system APIs
  - accessibility permission may be required

Linux:
  - X11 support easier
  - Wayland restrictions likely
  - idle detection depends on desktop environment
```

MVP 可以先完整支持 Windows，同时保留 macOS/Linux 接口。  
但 proposal 和 repo 结构应按三系统设计。

---

## 25.3 后台运行 + Terminal 呼出

系统应支持两种 terminal 交互：

```text
1. 被动弹出：检测到分心、到下班点、需要日报、需要补总结
2. 主动呼出：用户手动运行 CLI 命令
```

主动命令示例：

```bash
focusledger status
focusledger summary
focusledger offwork
focusledger overtime
focusledger pause
focusledger resume
focusledger holiday
focusledger review
focusledger config
```

推荐 CLI 功能：

| 命令 | 功能 |
|---|---|
| `focusledger status` | 查看当前记录状态 |
| `focusledger summary` | 查看今日临时总结 |
| `focusledger offwork` | 提前下班并进入日报流程 |
| `focusledger overtime` | 下班后继续记录 |
| `focusledger pause` | 暂停记录 |
| `focusledger resume` | 恢复记录 |
| `focusledger holiday` | 标记今天/多天为假期 |
| `focusledger review` | 补写未完成日报 |
| `focusledger config` | 修改语言、工作时间、阈值等配置 |

---

## 25.4 分心提醒交互

当系统检测到用户分心时，不直接强制关闭应用，而是弹出 terminal 提醒。

触发场景包括：

```text
长时间游戏
长时间娱乐网站
长时间无键鼠输入
长时间 attention gap
工作时间内使用娱乐网站
无痕/隐私浏览窗口被检测到
```

提醒示例：

```text
FocusLedger detected a possible distraction.

Time: 14:35 - 15:02
Detected activity: YouTube / entertainment
Idle gap: 8 min
Current workday status: working

What were you doing? You may select multiple:

[1] Watching entertainment video
[2] Gaming
[3] Social media / chatting
[4] Phone distraction
[5] Eating
[6] Bathroom
[7] Went outside
[8] Break
[9] Actually working / false positive
[10] Other
```

用户可以多选，例如：

```text
5,6
```

表示吃饭 + 上厕所。  
这些信息进入 `user_context_labels`，不会简单归入 idle。

---

## 25.5 多选分心/中断标签

长时间没动静不一定是手机分心，也可能是：

```text
吃饭
上厕所
出门
洗澡
睡觉
休息
接电话
纸质阅读
和朋友聊天
误判
```

因此 `idle_gaps` 或 `activity_segments` 中需要支持多标签。

新增字段建议：

```sql
user_context_labels TEXT
```

存 JSON array：

```json
["meal", "bathroom"]
```

推荐标签 taxonomy：

```text
phone_distraction
meal
bathroom
outside
break
sleep
social_offline
paper_reading
thinking
errand
false_positive
other
```

这些标签在日报里应分开显示，不要全部粗暴归类为 distraction。

例如：

```text
Idle / gaps:
- 31m meal
- 12m bathroom
- 25m possible phone distraction
- 40m outside
```

---

## 25.6 无痕窗口归类调整

无痕/隐私浏览不再单独作为日报类别。  
检测到无痕窗口时，直接归入 `entertainment`。

规则：

```text
if browser_private_mode_detected:
    browser_mode = "incognito" or "private"
    content_label = "entertainment"
    productivity_label = "entertainment"
    effective_seconds = 0
```

系统不解析无痕窗口中的 URL，不调用 LLM 判断内容，也不尝试将其算作生产力。

日报中不需要单独列出 incognito/private browsing time，可以合并到：

```text
Entertainment time
```

但底层 segment 仍可保留 `browser_mode` 字段，方便审计。

---

# 26. 工作日、下班时间与日报边界

## 26.1 需要工作日概念

FocusLedger 需要有 workday，而不是简单按自然日 00:00 - 23:59 统计。

原因：

```text
用户可能晚上工作、白天睡觉
用户可能跨午夜工作
用户可能提前下班或加班
用户可能几天不上班
日报应该按“工作周期”而不是自然日切分
```

因此系统应引入：

```text
Workday Session
```

Workday Session 表示一个用户认为的“今天的工作周期”。

---

## 26.2 Workday Session 数据结构

```sql
CREATE TABLE workday_sessions (
    id TEXT PRIMARY KEY,
    workday_date DATE,
    start_time DATETIME,
    planned_end_time DATETIME,
    actual_end_time DATETIME,
    status TEXT,

    is_overtime BOOLEAN,
    is_holiday BOOLEAN,
    is_sleep_shifted BOOLEAN,

    summary_submitted BOOLEAN,
    summary_submitted_at DATETIME,

    user_deliverable_notes TEXT,
    llm_daily_review TEXT,
    llm_tomorrow_plan TEXT,

    created_at DATETIME,
    updated_at DATETIME
);
```

`status` 可选：

```text
active
offwork
overtime
holiday
missing_summary
closed
```

---

## 26.3 默认工作时间

用户可以配置默认工作时间：

```yaml
work_schedule:
  default_start_time: "10:00"
  default_end_time: "18:00"
  timezone: "local"
  workdays: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
```

考虑到学生假期作息不稳定，默认不强制周末休息。  
用户可以每天选择：

```text
今天工作
今天放假
今天晚点开始
今天加班
今天提前下班
```

---

## 26.4 下班时间行为

到达配置的下班时间后，系统应弹出 terminal：

```text
It is your planned off-work time.

Do you want to end today's workday?

[1] End workday and write daily summary
[2] Continue overtime
[3] Pause tracking
[4] Mark today as holiday
[5] Remind me later
```

如果用户选择 `End workday`，进入日报输入流程。  
如果用户选择 `Continue overtime`，系统继续记录，并将 workday status 设为 `overtime`。

---

## 26.5 主动提前下班

用户可以主动运行：

```bash
focusledger offwork
```

然后系统进入：

```text
结束当前 workday
停止记录或切换到 offwork mode
要求用户输入今日 deliverable 进展
生成日报
```

offwork 之后默认不再记录，除非用户加班。

---

## 26.6 加班模式

用户可以主动运行：

```bash
focusledger overtime
```

或者在下班弹窗选择加班。

加班模式：

```text
继续记录活动
segment 标记为 overtime
日报中单独显示 overtime productive time
```

例如：

```text
Overtime:
- 21:30 - 23:10 coding
- Effective productive overtime: 1h 12m
```

---

## 26.7 下班后默认不记录

到达下班点并完成日报后，系统默认停止记录。

下班后的活动不计入当天工作审计，除非用户显式进入加班模式。

原因：

```text
避免娱乐时间被无限审计
保护私人生活边界
减少用户反感
```

但系统可以保留状态：

```text
offwork
```

此时只监听用户是否主动启动：

```bash
focusledger overtime
focusledger start
```

---

## 26.8 非健康作息与跨午夜工作

如果用户晚上工作、白天睡觉，不能简单按日期切分日报。

建议规则：

```text
Workday 以 start_time 和 actual_end_time 切分
不是按自然日 00:00 切分
```

例如：

```text
Workday 2026-05-06:
  start: 2026-05-06 20:30
  end:   2026-05-07 04:10
```

日报标题可以显示：

```text
Workday Review: 2026-05-06 20:30 - 2026-05-07 04:10
```

如果系统发现用户跨午夜仍在工作，不自动生成日报，而是等用户下班或达到最大工作窗口。

推荐最大工作窗口：

```yaml
max_workday_hours: 18
```

超过后提醒用户：

```text
This workday has lasted over 18 hours.
Do you want to close it, continue overtime, or mark part of it as sleep/offline?
```

---

# 27. 日报流程：用户先输入，LLM 后总结

## 27.1 设计原则

日报不应该完全由系统自动生成。  
因为系统只能看到设备行为，不能完全知道用户实际完成了什么。

因此日报流程应为：

```text
1. 系统生成当天客观行为摘要
2. 用户输入今天做了哪些 deliverables
3. 用户输入每个 deliverable 做到了什么程度
4. LLM 结合客观行为 + 用户输入生成完整版日报
5. LLM 规划明天任务
6. 保存为长期日记
```

---

## 27.2 下班日报输入流程

terminal 示例：

```text
Workday ended.

Objective activity summary:
- Core productive: 2h 15m
- General learning: 45m
- Entertainment: 3h 20m
- Gaming: 1h 10m
- Attention gaps: 1h 05m

Please write what you completed today.
You can use free text.

Example:
- Finished MicroK8s deployment section
- Debugged MinIO bucket init issue
- Reviewed VAE lecture halfway
- Did not start SFA review

Your input:
>
```

用户输入后，系统继续问：

```text
For each important deliverable, describe progress level:

1. Air Quality Monitoring deployment doc
Progress: [not started / partial / mostly done / done / blocked]
Notes:
>

2. ML exam review
Progress:
>
```

---

## 27.3 LLM 完整日报生成

LLM 输入应包括：

```text
objective activity summary
activity segments
attention gaps and user labels
gaming/entertainment time
todo/deliverable list
user-written daily notes
user progress ratings
unfinished tasks
historical plan from previous day
```

LLM 输出：

```text
完整日报
deliverable progress summary
今天的主要问题
明天计划
建议优先级
风险提醒
```

---

## 27.4 日报保存为日记

用户完成日报后，系统应保存为长期记录。

推荐保存两份：

```text
SQLite structured record
Markdown daily journal
```

Markdown 路径：

```text
journals/
  2026/
    05/
      2026-05-06_workday.md
```

跨午夜情况：

```text
journals/
  2026/
    05/
      2026-05-06_2030_to_2026-05-07_0410_workday.md
```

日报 markdown 应包含：

```text
workday time range
objective stats
user-written deliverable notes
LLM-generated review
tomorrow plan
raw issue summary
manual corrections
```

---

## 27.5 日记用途

长期保存日报的目的：

```text
几年后可以回看自己每天做了什么
追踪长期成长轨迹
复盘项目推进过程
观察拖延模式和生产力变化
积累个人工作日志
```

因此日报应尽量可读，不只是 JSON 数据。

---

# 28. 未提交日报的补写机制

## 28.1 问题

用户可能下班时没有写日报，或者直接关机、睡觉、离开。

这时不能丢失总结流程。

## 28.2 第二天上班提醒

如果上一个 workday 没有完成日报，第二天开工时提醒：

```text
You have an unfinished daily review from your last workday.

Workday:
2026-05-06 20:30 - 2026-05-07 04:10

Do you want to complete it now?

[1] Complete review now
[2] Skip and mark as incomplete
[3] Remind me later
[4] Mark previous day as holiday/off
```

系统应允许补写。

---

## 28.3 missing_summary 状态

如果下班后未总结，workday status 设为：

```text
missing_summary
```

直到用户补写或跳过。

---

# 29. 假期与长时间未上班

## 29.1 假期需求

用户可能几天没来上班。系统不应自动把这些天全部视为 failure。

当系统发现用户连续多天没有 workday 或没有启动记录，应询问：

```text
You have not started a workday for 3 days.

What happened?

[1] Vacation / holiday
[2] Sick / personal rest
[3] Traveling
[4] Forgot to use FocusLedger
[5] Other
```

## 29.2 假期记录

新增表：

```sql
CREATE TABLE holidays (
    id TEXT PRIMARY KEY,
    start_date DATE,
    end_date DATE,
    label TEXT,
    notes TEXT,
    created_at DATETIME
);
```

标签：

```text
vacation
sick
travel
personal_rest
school_break
forgot_tracking
other
```

## 29.3 假期不计入失败

假期期间：

```text
不生成 productivity failure
不提醒未完成日报
不要求补工作记录
可在长期日记中保留一条 holiday note
```

例如：

```text
2026-05-08 to 2026-05-10:
Holiday: travel
Notes: Went to Atlanta, no work tracking.
```

---

# 30. 多设备同步设计预留

## 30.1 需求背景

用户可能在不同设备上工作，例如：

```text
家里的台式机
学校的笔记本
实验室电脑
虚拟机
```

因此后期需要支持多设备同步。

## 30.2 当前 MVP 策略

MVP 不需要立即实现云同步，但数据库 schema 和日志设计应预留 `device_id`。

所有核心表都应包含：

```sql
device_id TEXT
```

例如：

```sql
ALTER TABLE activity_segments ADD COLUMN device_id TEXT;
ALTER TABLE workday_sessions ADD COLUMN device_id TEXT;
ALTER TABLE agent_heartbeats ADD COLUMN device_id TEXT;
```

## 30.3 设备注册

新增表：

```sql
CREATE TABLE devices (
    id TEXT PRIMARY KEY,
    device_name TEXT,
    platform TEXT,
    hostname TEXT,
    created_at DATETIME,
    last_seen_at DATETIME,
    sync_enabled BOOLEAN
);
```

示例：

```json
{
  "id": "device_home_desktop",
  "device_name": "Home Desktop",
  "platform": "Windows",
  "hostname": "JERRY-PC",
  "sync_enabled": true
}
```

## 30.4 后期同步方式

后期可选方案：

```text
Local-first sync
Cloud database
Self-hosted server
Git-based journal sync
Syncthing / Dropbox / iCloud Drive folder sync
```

考虑到日报会保存为 markdown，最简单可行的长期方案是：

```text
SQLite for local structured data
Markdown journals synced by Git or cloud folder
```

正式多设备同步可采用：

```text
central server + device_id + conflict resolution
```

## 30.5 多设备日报合并

同一个 workday 可能来自多台设备。

例如：

```text
09:00 - 13:00 学校笔记本
15:00 - 18:00 家里台式机
```

日报应按 workday 聚合，而不是按 device 分裂。

Daily review 输入应包含：

```text
all activity_segments where workday_session_id = current_workday
across all devices
```

需要处理：

```text
clock skew
overlapping segments
duplicate records
missing device sync
```

MVP 可以只保留 schema，不实现复杂同步。

---

# 31. 双语支持

## 31.1 支持语言

项目应支持：

```text
中文
English
```

配置：

```yaml
language: "zh"  # zh or en
```

## 31.2 双语范围

需要双语的部分：

```text
terminal prompts
菜单选项
日报模板
LLM prompt
LLM 输出语言
错误提示
配置说明
```

## 31.3 i18n 结构

推荐结构：

```text
i18n/
  zh.yaml
  en.yaml
```

示例：

```yaml
daily_review_prompt_title:
  zh: "请填写今天完成了哪些事情"
  en: "Please write what you completed today"

offwork_prompt:
  zh: "已经到下班时间，是否结束今天的工作？"
  en: "It is your planned off-work time. Do you want to end today's workday?"
```

代码中不要硬编码中文或英文。

## 31.4 LLM 输出语言

LLM Agent 应根据用户配置输出。

例如：

```yaml
llm:
  output_language: "zh"
```

如果用户切换英文，则日报、计划、提醒都应切到英文。

---

# 32. 更新后的 MVP 范围

## 32.1 必须完成

```text
1. Terminal-first CLI interaction
2. Cross-platform architecture with platform adapters
3. Windows collector as first complete implementation
4. User-level autostart / background agent
5. Active app / window title tracking
6. Keyboard/mouse idle detection
7. Unified attention thresholds
8. Automatic activity segment generation
9. Browser URL/title/audio/mode collection
10. Incognito/private browsing classified into entertainment
11. Rule-based activity classification
12. Video/audio content classification
13. Todo/deliverable management
14. Workday session model
15. Offwork time and overtime mode
16. Pause/resume tracking
17. Distraction terminal prompt with multi-select labels
18. User-written daily deliverable progress input
19. LLM-generated full daily review after user input
20. Markdown journal export
21. Missing daily review reminder
22. Holiday / vacation marking
23. Bilingual support: Chinese and English
24. Audio notification system for distraction, offwork, and review reminders
25. Configurable mute / cooldown / test audio commands
26. Device_id schema reserved for future multi-device sync
```

## 32.2 暂不需要

```text
1. 手动 focus session
2. 不同注意力模式
3. 摄像头检测
4. 手机端监控
5. GUI dashboard
6. 系统级强制锁应用
7. 云端多设备同步完整实现
8. 复杂冲突解决
9. OCR / screenshot 分析
```

---

# 33. 更新后的核心用户流程

## 33.1 正常工作日

```text
用户开机
→ FocusLedger 后台启动
→ 用户正常使用电脑
→ 系统自动记录应用、浏览器、idle、音频状态
→ 检测到分心时播放音频提醒并弹出 terminal
→ 用户选择分心/中断原因
→ 到下班时间播放音频提醒并弹出日报流程
→ 用户输入今天做了哪些 deliverables 和完成程度
→ LLM 生成完整版日报和明日计划
→ 保存为 markdown 日记
```

## 33.2 提前下班

```text
用户运行 focusledger offwork
→ 系统停止当前 workday
→ 进入日报输入流程
→ 生成日报
→ 下班后默认不再记录
```

## 33.3 加班

```text
到下班时间
→ 用户选择 overtime
→ 系统继续记录
→ segment 标记为 overtime
→ 用户之后手动 offwork
→ 生成包含 overtime 的日报
```

## 33.4 第二天补日报

```text
用户昨天没有写日报
→ 第二天启动 workday
→ terminal 提醒补写
→ 用户补充昨日 deliverable 进展
→ LLM 补生成昨日完整版日报
```

## 33.5 放假

```text
用户几天没工作
→ 系统提醒
→ 用户选择 vacation / sick / travel / personal rest
→ 系统记录 holiday
→ 不生成失败评价
```

---

# 34. 更新后的成功标准

MVP 成功标准更新为：

系统能稳定完成以下闭环：

```text
自动后台记录
→ 分心时音频 + terminal 提醒
→ 用户标注中断原因
→ 到下班时间要求用户填写 deliverable 进展
→ LLM 生成完整日报和明日计划
→ 保存长期 markdown 日记
→ 第二天能补写漏掉的日报
→ 假期能正确跳过
```

成功日报应包含：

```text
workday 时间范围
客观设备行为统计
用户手写 deliverable 进展
中断/分心原因
娱乐/游戏/泛知识/核心生产力分类
LLM 生成的今日复盘
LLM 生成的明日计划
是否加班
是否有 missing data
```


# 35. 音频提醒系统

## 35.1 设计目标

FocusLedger 需要接入本地音频系统，在关键事件发生时播放声音提醒用户。

原因：

```text
terminal 提醒可能被用户忽略
用户可能正在游戏、看视频、离开键盘或注意力漂移
音频提醒比纯文字提醒更容易打断分心状态
下班提醒需要明确、及时
补日报提醒也需要更强提示
```

音频提醒的目标不是强制控制用户，而是提供低成本、明确、可配置的提示。

---

## 35.2 需要音频提醒的事件

MVP 至少支持以下事件：

```text
1. distraction_detected
2. long_idle_gap_detected
3. offwork_time_reached
4. daily_review_required
5. missing_review_reminder
6. overtime_started
7. tracking_paused
8. tracking_resumed
9. holiday_prompt
10. agent_error_or_collector_failure
```

其中最重要的是：

```text
分心提醒
下班提醒
日报提醒
```

---

## 35.3 音频提醒与 Terminal 提醒联动

音频提醒不应单独存在，而应和 terminal prompt 联动。

典型流程：

```text
系统检测到分心
→ 播放分心提醒音
→ 弹出 terminal prompt
→ 用户选择当前在做什么
→ 系统记录用户选择
```

下班流程：

```text
到达计划下班时间
→ 播放下班提醒音
→ 弹出 terminal prompt
→ 用户选择下班、加班、暂停、稍后提醒或放假
```

补日报流程：

```text
第二天检测到未完成日报
→ 播放补日报提醒音
→ 弹出 terminal prompt
→ 用户选择现在补写、稍后提醒、跳过或标记为假期
```

---

## 35.4 音频事件配置

推荐使用配置文件：

```yaml
audio:
  enabled: true
  volume: 0.7
  mute_during_offwork: true
  respect_do_not_disturb: true
  repeat_until_acknowledged: false
  max_repeat_count: 3
  repeat_interval_seconds: 30

  sounds:
    distraction_detected: "assets/sounds/distraction.wav"
    long_idle_gap_detected: "assets/sounds/idle_gap.wav"
    offwork_time_reached: "assets/sounds/offwork.wav"
    daily_review_required: "assets/sounds/daily_review.wav"
    missing_review_reminder: "assets/sounds/missing_review.wav"
    overtime_started: "assets/sounds/overtime.wav"
    tracking_paused: "assets/sounds/pause.wav"
    tracking_resumed: "assets/sounds/resume.wav"
    error: "assets/sounds/error.wav"
```

用户应能通过 CLI 修改：

```bash
focusledger audio on
focusledger audio off
focusledger audio test
focusledger audio volume 0.5
focusledger audio set distraction_detected assets/sounds/custom.wav
```

---

## 35.5 音频提醒强度

不同事件应使用不同强度的提醒。

| 事件 | 声音强度 | 是否可重复 |
|---|---|---|
| 分心提醒 | 中 | 可选 |
| 长时间 idle | 低/中 | 可选 |
| 下班提醒 | 中/高 | 可重复 |
| 日报填写 | 中 | 可重复 |
| 补日报 | 中 | 可重复 |
| collector 异常 | 高 | 可重复 |
| 暂停/恢复 | 低 | 不重复 |

建议默认：

```text
分心提醒播放一次
下班提醒播放一次，并弹出 terminal
日报提醒如果用户没有响应，可每 30 分钟提醒一次，最多 3 次
错误提醒可以更明显
```

---

## 35.6 勿扰与静音策略

音频提醒必须可控，避免变成新的干扰源。

支持：

```text
全局静音
下班后静音
睡眠时间静音
会议/演讲/录屏时静音
游戏时是否提醒可配置
勿扰时间段
```

配置示例：

```yaml
audio:
  enabled: true
  quiet_hours:
    enabled: true
    start: "01:00"
    end: "09:00"

  policies:
    play_during_gaming: true
    play_during_video: true
    play_when_system_muted: false
    play_after_offwork: false
```

如果系统处于 offwork 状态，默认不播放分心提醒。  
如果用户进入 overtime 状态，则恢复提醒。

---

## 35.7 跨平台音频后端

项目需要支持 Windows、macOS、Linux，因此音频播放应通过统一接口。

推荐接口：

```python
class AudioNotifier:
    def play(self, event_name: str) -> None:
        ...

    def test(self) -> None:
        ...

    def set_volume(self, volume: float) -> None:
        ...

    def is_available(self) -> bool:
        ...
```

平台实现：

```text
Windows:
  - winsound for simple WAV playback
  - playsound / sounddevice for richer playback

macOS:
  - afplay command
  - sounddevice / simpleaudio

Linux:
  - paplay / aplay
  - sounddevice / simpleaudio
```

MVP 推荐：

```text
优先使用 simpleaudio 或 sounddevice
失败时 fallback 到系统命令
再失败时只显示 terminal 提醒
```

系统不应因为音频播放失败而停止记录。

---

## 35.8 音频文件策略

不建议 repo 中放大量复杂音频。  
MVP 可以提供少量短音效：

```text
distraction.wav
offwork.wav
daily_review.wav
error.wav
```

音效要求：

```text
短
清晰
不刺耳
不含版权风险
可替换
```

也可以支持系统 beep fallback：

```text
如果音频文件不存在
→ 使用系统 beep
→ 同时输出 terminal warning
```

---

## 35.9 与系统音量/音频播放检测的关系

FocusLedger 需要区分两个音频方向：

```text
1. audio notifier：系统主动播放提醒音
2. audio state detector：检测浏览器/系统是否正在播放音频，用于判断 lecture/video 场景
```

这两个模块不能混淆。

```text
AudioNotifier:
  播放提醒用户的声音

AudioStateDetector:
  检测用户当前是否在播放音频/视频
```

例如：

```text
YouTube 正在播放 VAE lecture
→ AudioStateDetector 发现 audio_playing = true

用户已经娱乐视频 40 分钟
→ AudioNotifier 播放 distraction reminder
```

---

## 35.10 事件节流

避免频繁播放音频。

每个事件应有 cooldown：

```yaml
audio_cooldowns:
  distraction_detected_seconds: 600
  long_idle_gap_detected_seconds: 900
  offwork_time_reached_seconds: 3600
  daily_review_required_seconds: 1800
  missing_review_reminder_seconds: 3600
  error_seconds: 300
```

例如：

```text
如果 10 分钟内已经播放过分心提醒，不再重复播放。
```

---

## 35.11 日志记录

每次音频提醒都应记录：

```sql
CREATE TABLE notification_events (
    id TEXT PRIMARY KEY,
    timestamp DATETIME,
    event_type TEXT,
    channel TEXT,
    sound_file TEXT,
    delivered BOOLEAN,
    acknowledged BOOLEAN,
    related_segment_id TEXT,
    related_workday_id TEXT,
    message TEXT
);
```

`channel` 可选：

```text
terminal
audio
terminal+audio
```

用途：

```text
避免重复提醒
分析用户是否忽略提醒
复盘当天被提醒了几次
debug 音频系统
```

---

## 35.12 CLI 命令

新增 CLI 命令：

```bash
focusledger audio on
focusledger audio off
focusledger audio test
focusledger audio volume 0.7
focusledger audio status
focusledger audio list
```

示例：

```bash
focusledger audio test offwork_time_reached
```

输出：

```text
Playing sound for event: offwork_time_reached
Sound file: assets/sounds/offwork.wav
Status: delivered
```

---

## 35.13 用户体验目标

音频提醒的理想体验：

```text
用户分心时听到简短提示音
terminal 同时出现选择菜单
用户选择“吃饭 / 上厕所 / 手机分心 / 误判”等原因
系统记录该中断原因

到下班点时播放明确提示音
terminal 询问是否下班、加班、暂停或稍后提醒
用户填写今日 deliverable 进展
系统生成完整日报
```

音频提醒应当：

```text
足够明显
不刺耳
可关闭
有冷却时间
不破坏 offwork/private time
跨平台可用
失败时不影响主流程
```

# 23. 总结

FocusLedger 的核心价值是：

```text
把主观计划、客观设备行为、注意力状态和 LLM 规划连接起来。
```

它不是简单地记录时间，也不是普通 todo list。

它要形成一个每日审计闭环：

```text
计划 deliverables
→ 自动观察真实行为
→ 判断有效生产力
→ 识别分心和空窗
→ 生成日报
→ 重新规划明天
```

最终目标：

> 让用户清楚看到自己每天是否真正推进了重要任务，而不是被游戏、视频和注意力空窗吞掉时间。
