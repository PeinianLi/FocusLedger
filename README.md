# FocusLedger

**A local-first ledger of where your attention actually goes.**

[中文版 README](README_ZH.md)

FocusLedger records which application is in front on your computer and how long it has been since you last touched the keyboard or mouse. From those two signals it derives an honest daily ledger: which apps and categories took your time, how much of that time you were actually engaged, which goals moved, and where the day leaked. A local language model can then answer questions about that ledger, but every number it states is computed by FocusLedger, never by the model.

Everything stays on your machine: one SQLite file, a report server that only listens on `127.0.0.1`, and models run through a local [Ollama](https://ollama.com) install.

FocusLedger is the foundation of **Fole**, a planned personal assistant that spans a user's devices. See the [project proposal](docs/PROJECT_PROPOSAL.md) and [development phases](docs/DEVELOPMENT_PHASES.md).

## What it does today

| Area | What you get |
|---|---|
| Collection | A background agent samples the foreground window and keyboard/mouse idle time every second (Windows and macOS) |
| Attention | Idle time is turned into attention states (active, low activity, gap, interrupted, hard idle) and a weighted *effective time* |
| Workday | Start, pause, overtime and off-work, with work past midnight kept on the day it started and agent outages shown as missing data |
| Classification | 11 categories from rules, site parsing, game-store install folders, rules learned by a local model, and your own corrections |
| Deliverables | Goals and tasks, with time attributed to them by keyword |
| Distraction alerts | A local notification after a sustained gaming or entertainment streak during the workday, and a place to record why |
| Daily review | A written review and plan for tomorrow from a small local model, with a deterministic fallback |
| Analyst | Ask questions about your tracked time; a local model answers from tool results, every number is checked against the data, and any change it suggests waits for your approval and can be undone |
| Live report | A browser dashboard with date navigation, the analyst panel and a storage view, served from your own machine |
| Autostart | Run the agent at login (Task Scheduler, launchd or systemd) |

## Quick start

Requires Python 3.11 or newer. On Windows nothing else is needed; on macOS, `pip install -e .` adds PyObjC for full window tracking.

```bash
python -m focusledger init
python -m focusledger start --background
python -m focusledger report
```

`report` opens the dashboard in your browser. To ask the local model about your data, install [Ollama](https://ollama.com/download), pull a tool-capable model, and ask:

```bash
ollama pull qwen3.5:9b
python -m focusledger analyst ask "Where did my time go over the last 7 tracked days?" --trace
```

The full command reference is in [docs/CLI_USAGE.md](docs/CLI_USAGE.md) ([中文](docs/CLI_USAGE_ZH.md)).

## How it works

```text
agent samples the foreground window and idle seconds every second
→ raw samples and heartbeats are written to SQLite
→ recent days are derived from raw samples at query time:
     activity segments, attention segments, categories, deliverable time
→ once a day leaves the raw retention window it is archived:
     a few hundred cooked segments replace tens of thousands of samples
→ the CLI, the live report and the analyst read the same derived data
```

Anything that can be recomputed is never stored. Changing a classification rule or an attention threshold re-explains your history instead of leaving stale rows behind.

## Attention model

FocusLedger never reads what you type, where the pointer is, or anything from a camera. It only reads how long ago the last keyboard or mouse input happened.

| Time since last input | State | Weight |
|---:|---|---:|
| 0–3 min | `active` | 1.0 |
| 3–10 min | `low_activity` | 0.5 |
| 10–20 min | `attention_gap` | 0.0 |
| 20–30 min | `interrupted` | 0.0 |
| 30+ min | `hard_idle` | 0.0 |

When input returns after a long silence, the whole silence is reclassified as `hard_idle`, so leaving a window open while you are away never counts as work. A silence that crosses midnight is split at local midnight, so no stretch of time is counted on two days. Thresholds and weights are configurable.

## The analyst

`focusledger analyst` and the **Ask the Analyst** panel let a local model answer questions such as "which hours am I most focused?" or "how did this week compare with the last?".

- The model calls eight read tools: an index of tracked days, day and range summaries, range comparison, app usage, a timeline, an hourly profile and deliverables. The tools do all arithmetic in Python and hand back finished figures.
- Two more tools let it **propose** changes: mark time as away from the computer (for example "8月11号的unknown全是在睡觉，帮我在数据库里面改了" marks every lock-screen stretch that day as sleep), or always put an app, site or keyword in a category. Nothing is written until you approve it in the page or answer `y` in the terminal. Every applied change is logged and can be undone, and raw samples are never rewritten: marked time is a note applied whenever the day is read.
- A grounding check compares every number in the answer with the tool results. An answer given without calling any tool, or containing a number no tool returned, is sent back for a rewrite; numbers that remain ungrounded are flagged.
- Follow-up questions see the last four answers, so "mark that day" works after asking about a day. The model's thinking, for models that think, is shown alongside the answer.
- Tools are called through Ollama's native tool API, or through a JSON protocol in the reply for models that list tool support but never emit a call. The default `auto` mode probes each model once and picks.
- `analyst eval` runs a fixed question set against several models.

Measured on 51 days of real data (RTX 5070 12 GB, Ollama 0.40, five built-in questions, each answer checked by hand against the tool results):

| Model | Tool calls | Fully correct | Median time |
|---|---|---:|---:|
| `qwen3.5:9b` (default) | native | 5/5 | 22 s |
| `qwen3:8b` | native | 2/5 | 32 s |
| `deepseek-r1:8b` | text | 2/5 | 55 s |

The newest DeepSeek models (V4 family) are cloud-only on Ollama, which would send your activity data off the machine, so they are not used.

## Live report

`python -m focusledger report` serves a dashboard on `127.0.0.1` and opens it. React 18 runs from vendored files with no build step, and `--export` writes the same UI with its data inlined into a single offline HTML file.

- **Date bar**: step through every tracked day; only today refreshes live.
- **Focus Skyline**: the attention timeline, with a 15-minute focus density row, a category band and one lane per app. Scroll to zoom down to a two-minute window, drag to pan, shift-drag to zoom into a range.
- **Deep work**: stretches of 25 minutes or more of sustained engagement, click to zoom.
- **Ask the Analyst**: questions and follow-ups, live tool calls and thinking, answers whose dates link back into the report, proposal cards with Approve / Dismiss / Undo, and a list of every change made for you. The panel folds away when you do not need it.
- **Database**: file size, used versus free space, raw and archived layers, and the maintenance command to run when one would help.

## Data and storage

The database is at `%LOCALAPPDATA%\FocusLedger\focusledger.sqlite3` on Windows and `~/.focusledger/focusledger.sqlite3` elsewhere. It holds three layers:

| Layer | Tables | Kept |
|---|---|---|
| Raw | `raw_activity_events`, `agent_heartbeats`, with `applications` and `time_contexts` lookups | 7 local dates by default |
| Cooked | `activity_segments`, `attention_segments` | Indefinitely |
| Your records | workdays, pauses, deliverables, tasks, notes, reviews, distraction events, rules, corrections, time you marked as away, the log of approved changes | Never pruned |

Each sample stores UTC time plus a link to its time-zone context, so time-zone changes and daylight-saving transitions never reorder a timeline. The schema version lives in SQLite's `user_version` (currently 11); upgrades run as numbered steps on first use, and older code refuses to open a newer database.

```bash
python -m focusledger config retention
python -m focusledger config set raw-retention-days 14
python -m focusledger db prune          # archive days past retention now
python -m focusledger db compact --yes  # rebuild the file; stop the agent first
```

Archiving seven days of one-second samples on a real database took it from 235 MB to 17 MB.

## Privacy and security

- Only foreground window metadata and idle seconds are collected; no keystrokes, pointer positions or page contents.
- Private and incognito browser titles are stored as `[private browsing]`, and once a window is known to be private it stays redacted after its title changes (Windows, and macOS with PyObjC).
- The report server binds to the loopback interface only, checks the `Host` header so a DNS-rebinding page cannot read it, and only accepts analyst questions and approvals as JSON.
- The model cannot change your data on its own: write tools only return a proposal, and only your approval applies it.
- Model answers are rendered as text, never injected as HTML.
- Models run locally through Ollama; nothing is sent to a cloud service.

## Platform support

| Platform | Status |
|---|---|
| Windows | Foreground window, process and title capture, idle detection, attention |
| macOS | Foreground app and idle detection; window titles need the Screen Recording permission. Without PyObjC a subprocess fallback runs, which cannot track private windows across title changes |
| Linux | Configuration, database and every query command work; collection is not implemented yet |

## Local models

| Setting | Used by | Default |
|---|---|---|
| `llm_analyst_model` | `analyst` and the Ask the Analyst panel | `qwen3.5:9b` |
| `llm_model` | `review generate` prose and `classify auto` labelling | `qwen2.5:1.5b` |

Both run through Ollama at `127.0.0.1:11434` using only the Python standard library. Without a model the daily review falls back to a deterministic version with the same figures.

## Documentation

| Document | English | 中文 |
|---|---|---|
| Command reference | [CLI_USAGE.md](docs/CLI_USAGE.md) | [CLI_USAGE_ZH.md](docs/CLI_USAGE_ZH.md) |
| Project proposal (Fole) | [PROJECT_PROPOSAL.md](docs/PROJECT_PROPOSAL.md) | [PROJECT_PROPOSAL_ZH.md](docs/PROJECT_PROPOSAL_ZH.md) |
| Development phases (Fole) | [DEVELOPMENT_PHASES.md](docs/DEVELOPMENT_PHASES.md) | [DEVELOPMENT_PHASES_ZH.md](docs/DEVELOPMENT_PHASES_ZH.md) |
| Original productivity-audit plan | | [docs/legacy](docs/legacy/README.md) |
| Development log | | [devlog/](devlog/) |

## Not implemented yet

- A browser extension for full URLs, audio state and reliable private-window detection; sites currently come from window titles
- Collection on Linux
- A system-tray icon
- Multi-device sync, OCR and a phone companion

## Development

Run the test suite (every `tests/test_*.py`; tests build throwaway databases and never touch your real data):

```bash
python -m unittest discover -s tests
```

Check that every source file compiles, a quick syntax check that runs no code:

```bash
python -m compileall focusledger tests
```

On Windows, `pip install tzdata` first so the time-zone tests can load IANA zones such as `America/New_York`. On macOS and Linux use `python3` wherever these documents say `python`.
