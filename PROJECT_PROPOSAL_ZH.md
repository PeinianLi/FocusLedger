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
无痕窗口 -> 直接 distraction
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
Incognito/private browsing time
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
- 无痕窗口使用：25m
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
Backend: FastAPI
Database: SQLite
Desktop Collector: pywin32 / ctypes
Idle Detection: Windows GetLastInputInfo
Browser Context: browser extension or local native messaging
Frontend: Streamlit or simple React
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
5. 游戏、娱乐、社交、无痕窗口分别用了多久？
6. 今天有哪些 attention gaps？
7. 哪些 deliverables 有实际推进？
8. 明天应该优先做什么？
```

成功日报示例：

```text
今天 PyCharm 打开了 3h20m，但有效 coding 时间约 1h45m。
Chrome 使用 2h10m，其中 38m 为 ML 相关学习，25m 为无痕窗口，默认计入 distraction。
YouTube 使用 1h30m，其中 VAE lecture 24m 计入 core productive，游戏视频 40m 计入 entertainment，历史科普 26m 计入 neutral educational。
Steam 使用 3h50m。
Air Quality deployment doc 有部分推进，但 ML exam review 进度不足。
明天建议先完成 MicroK8s deployment 文档，再复习 SFA 和 VAE。
```

---

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

> 让用户清楚看到自己每天是否真正推进了重要任务，而不是被游戏、视频、无痕浏览和注意力空窗吞掉时间。
