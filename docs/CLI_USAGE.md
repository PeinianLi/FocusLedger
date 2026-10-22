# FocusLedger CLI Guide

[中文版](CLI_USAGE_ZH.md)

This guide covers everyday use of `python -m focusledger ...`. Section numbers match the Chinese version.

Two general notes:

- On macOS and Linux, use `python3` wherever this guide says `python`; the system `python` may be missing or point to Python 2.
- On macOS, install with `pip install -e .` first. It adds PyObjC, which provides stable window handles for private-window tracking. Running from source without it works, but collection falls back to a subprocess path and logs a warning at startup; see "Platform support" in the README.

## After upgrading: start here (local test walkthrough)

The current database version is **v11**. When you upgrade from an older version, **the first `focusledger` command you run migrates the database automatically**. From v9 or v10 this is instant (v11 only adds the two tables that hold changes you approve, see section 13); from v8 the raw table is rebuilt in place, which takes a few seconds for a few hundred thousand rows.
Back up first (see section 18). Older code refuses to open a v11 database instead of writing to it.

A suggested order for trying everything:

1. Archive raw samples that are past retention; the database shrinks a lot (for example 235 MB → 17 MB):

   ```bash
   python -m focusledger db prune
   ```

2. Open the report on the last day you have data for (use your own date):

   ```bash
   python -m focusledger report --date 2026-08-11
   ```

   In the page, switch days with the date bar, click **Database** to see the size and what fills it, and click a sample question in **Ask the Analyst**. See section 8.

3. Ask the local model from the terminal (section 13):

   ```bash
   python -m focusledger analyst ask "Where did my time go over the last 7 tracked days?" --trace
   ```

4. Correct a day through the model, for example a night the screen stayed on while you slept. In the page, ask "8月11号的unknown全是在睡觉，帮我在数据库里面改了", check the proposal card and click **Approve**; the report updates at once, and **Undo** puts it back. See "Changing data" in section 13.

5. Press `Ctrl+C` in the terminal to stop the report server.

Two model settings do different jobs:

| Setting | Used by | Default |
|---|---|---|
| `llm_analyst_model` | `analyst` questions and the Ask the Analyst panel | `qwen3.5:9b` |
| `llm_model` | `review generate` prose and `classify auto` labelling | `qwen2.5:1.5b` |

A model that is not in `ollama list` cannot be used. Either `ollama pull <model>`, or set the value in the config file (find it with `python -m focusledger config path`) to a model you already have. Without its model the daily review falls back to the rule-based version, while `classify auto` stops with an error.

## 1. Initialize

Create the config and database on first use:

```bash
python -m focusledger init
```

Show the config file path:

```bash
python -m focusledger config path
```

Show the database path:

```bash
python -m focusledger db path
```

## 2. Start collecting

Start in the background (recommended):

```bash
python -m focusledger start --background
```

The terminal stays free for other commands, and the agent keeps running after you close it.

Start in the foreground for debugging:

```bash
python -m focusledger start
```

Foreground mode holds the terminal; press `Ctrl+C` to stop.

Collect one sample:

```bash
python -m focusledger start --once
```

Collect a fixed number of samples and exit:

```bash
python -m focusledger start --max-samples 10
```

## 3. Stop collecting

Ask the running agent to stop:

```bash
python -m focusledger stop
```

The agent exits after its next poll.

## 4. Check the agent

```bash
python -m focusledger status
```

The fields that matter:

```text
status
timestamp
age_seconds
stale
pid
device_id
```

This means the agent is running and heartbeating normally:

```text
status: running
stale: false
```

The agent writes a heartbeat immediately when its status changes (start, error, recovery) and otherwise at most every 30 seconds.

This means no heartbeat arrived in the last 60 seconds (or three poll intervals, if the interval is long):

```text
status: running
stale: true
```

Common causes are a reboot, a crash, or a process that no longer exists.

When raw samples past retention are waiting to be archived, `status` prints a line first, for example:

```text
7 day(s) of raw samples are past retention and not archived yet (2026-08-05 to 2026-08-11); the agent archives them when it starts, or run `focusledger db prune` now.
```

The agent archives automatically whenever the date changes; while it is stopped, days pile up. Run `db prune` as suggested (section 18).

## 5. Activity timeline

Today:

```bash
python -m focusledger timeline --today
```

A given date:

```bash
python -m focusledger timeline --date 2026-07-09
```

Example output:

```text
21:35:05 - 21:35:24         19s  (unknown)
21:35:25 - 21:42:10      6m 45s  chrome.exe  Example - Google Chrome
```

`(unknown)` usually means Windows returned no ordinary foreground process: the screen saver, the lock screen, system UI, or a protected window.

## 6. Time per app

Total time per process today:

```bash
python -m focusledger summary --today
```

For a given date:

```bash
python -m focusledger summary --date 2026-07-09
```

## 7. Attention

Today's attention summary:

```bash
python -m focusledger attention --today
```

Today, with every attention segment listed:

```bash
python -m focusledger attention --today --timeline
```

Any date:

```bash
python -m focusledger attention --date 2026-07-09 --timeline
```

What the states mean:

```text
active          recent keyboard or mouse input
low_activity    little interaction; possibly reading, watching or thinking
attention_gap   attention has probably drifted
interrupted     a long interruption
hard_idle       confirmed long absence or no input
unknown         idle time could not be read
```

Default thresholds:

```text
0-3 min       active
3-10 min      low_activity
10-20 min     attention_gap
20-30 min     interrupted
30+ min       hard_idle
```

Where the data comes from: for days inside the retention window (the last 7 by default), `timeline`, `summary` and `attention` are computed from raw samples at query time, so a changed threshold applies immediately. Older days read the segments stored when they were archived (section 18).

A long silence that crosses midnight is counted once: leave at 16:30 and come back at 08:00, and the part before midnight belongs to the first day while the part after belongs to the second.

## 8. Live report (local web page)

Start the report server and open the browser:

```bash
python -m focusledger report --today
```

The output looks like this:

```text
FocusLedger report server running.
  url: http://127.0.0.1:53127/
  date: 2026-07-18 (live — follows today as it is collected)
  press Ctrl+C to stop
```

Today's page is **live**: whenever the agent records new samples the page updates, **keeping your current zoom**. No need to rerun anything. `Ctrl+C` stops the server.

Common options:

```bash
python -m focusledger report --today --port 8765     # fixed port
python -m focusledger report --today --no-open       # print the URL without opening a browser
python -m focusledger report --date 2026-07-16       # open on a past day (not live)
```

The date bar at the top moves between every day with data: ‹ › step one tracked day, the drop-down jumps directly, and `Today` comes back to today. Only today refreshes live; a past day loads once.
If today has no data yet, the page offers a button that opens the last tracked day.

**Database**, next to the date bar, shows the database file size. Click it to open the storage panel:
- file size, the space actually holding data, and space freed but still inside the file;
- how many raw sample rows cover which days, how many days are archived, and how many heartbeats are stored;
- a hint to run `focusledger db prune` when raw days are past retention, and `focusledger db compact --yes` when more than 5% of the file is free (stop the agent first).

The server listens on `127.0.0.1` only, so nothing else on your network can reach it and your attention data never leaves the machine.
API calls must also carry this server's own address in the `Host` header, so a web page that points its own domain at 127.0.0.1 (DNS rebinding) still cannot read the data.

### Ask the local model from the page (Ask the Analyst)

The **Ask the Analyst** panel at the top is section 13's `analyst ask`. Type a question (or click a sample) and press `Ctrl+Enter`.

- While it works, the panel lists every tool the model calls and how long it took; expand one to see exactly the data the model received.
- **Thinking**: for a model that thinks before answering (`qwen3.5:9b`, `qwen3:8b`, `deepseek-r1:8b`), its reasoning streams into a **Thinking** block, which folds away once the answer arrives; click it to read it again. Models that do not think show no block.
- **Follow-ups**: the model sees your last four answered questions, so "把那天的 unknown 标成睡觉" works after a question about a day. **New conversation** forgets them.
- **Folding**: the `-` / `+` button next to the title hides or shows the whole panel; the page remembers your choice. Older answers fold to their question when a new one arrives; click the question to open one again.
- **Proposed changes** appear as cards under the answer: what would change and how the effective time moves. **Approve** writes it and refreshes the report, **Dismiss** drops it, and **Undo** on an applied card reverses it.
- **Changes made for you**, at the bottom of the panel, lists every change ever approved, including ones from the terminal, each with its own **Undo**. It survives a page reload.
- Tracked dates in the answer become links: click one and the report above switches to that day.
- Numbers that appear in no tool result are flagged in yellow, and an answer that had to be rewritten is marked `sent back once`.
- Pick the model at the top right. The list comes from the models Ollama has installed; the default is `llm_analyst_model`.
- One question is answered at a time, since the local model shares one GPU. Exported snapshots do not include this panel.

### Export a static snapshot

To archive a day or share it, export a self-contained HTML file with all data, styles and scripts inlined; it opens offline:

```bash
python -m focusledger report --date 2026-07-16 --export ~/Desktop/focus-0716.html
```

The snapshot runs the same front-end code as the live page, with fixed data and no refresh.

What the page shows:

```text
Stat tiles     effective focus / tracked time / focus ratio / deep work / app switches / top app
Focus Skyline  attention timeline: bar height = weight, colour = state; long idle labelled on the chart
Focus density  effective share per 15 minutes (like the volume row under a stock chart)
App lanes      one lane per app, showing how the foreground switched
Minimap        whole-day overview with the current window
Deep work      automatically detected stretches of 25+ minutes; click to zoom
Breakdowns     state shares and app ranking for the visible range
Table view     expandable list of attention segments
```

Interaction:

```text
Wheel            zoom around the cursor (down to a 2-minute window)
15m/1h/3h/Day    jump to that span (centred on "now" when live)
Drag             pan
Shift + drag     select a range and zoom into it
Minimap          drag the window to pan; drag on empty space to zoom
Double-click / Esc / 0   back to the whole day
← →              pan      + −   zoom
Hover            crosshair plus details (state, weight, segment length, app, window title)
Legend click     isolate one attention state
App ranking hover   highlight that app's segments in its lane
```

Two more behaviours in live mode: a dashed teal line marks "now", and if your view sits at the right edge it follows new data forward, like a live stock chart. Drag back and it stops following.

## 9. Workday

Reviews follow your real working period rather than the calendar day; work that runs past midnight still belongs to the day it started.

```bash
python -m focusledger workday start        # start work
python -m focusledger workday pause --reason lunch
python -m focusledger workday resume
python -m focusledger workday overtime     # keep going past off-work time
python -m focusledger workday offwork      # finish
python -m focusledger workday status
python -m focusledger workday list
```

Days off never ask for a review:

```bash
python -m focusledger workday mark-holiday --date 2026-10-01
python -m focusledger workday mark-vacation --date 2026-10-02
```

`status` shows **missing data**: stretches when the agent was not running, derived from gaps in the heartbeats (a gap counts after 60 seconds without one). The review shows them too, so you never mistake partial data for a complete day.

There is one workday per date: notes and reviews are stored once per date, even if several devices ever share one database.

## 10. Classification

Time is sorted into 11 categories (core productive, learning, general knowledge, admin, entertainment, gaming, social, communication and so on).

```bash
python -m focusledger classify summary --today     # time per category
python -m focusledger classify timeline --today    # every segment with its verdict and reason
python -m focusledger classify timeline --today --category gaming   # one category only
python -m focusledger classify unknown --today     # what is still unclassified, longest first
python -m focusledger classify categories          # list the categories
```

**Games are recognised by install folder.** Game executables have arbitrary names (`Dangan3Win.exe`, `swhaop.exe`), so besides the program name FocusLedger looks at where it is installed. Anything under a Steam (`steamapps\common`), Epic, GOG, Xbox, Riot, Ubisoft or EA game library counts as gaming.
Steam also distributes tools such as Blender; correct those with a manual correction below, which takes priority.

Let the local model label whatever is still unknown (runs offline, stores the result as rules for this machine, and uses `llm_model`):

```bash
python -m focusledger classify auto --today --dry-run   # show the labels without saving
python -m focusledger classify auto --today             # save them
python -m focusledger classify rules                    # rules learned on this machine
```

When a verdict is wrong, correct it. **Corrections re-explain your history**, because classification is derived at query time:

```bash
python -m focusledger classify correct process "obsidian" productive_core
python -m focusledger classify correct site "youtube.com" learning_general
python -m focusledger classify correct title_keyword "invoice" admin
python -m focusledger classify corrections     # list corrections
python -m focusledger classify uncorrect process "obsidian"
```

Order of precedence: manual correction → deliverable link → rules learned on this machine (`classify auto`) → site rule → app rule → game library folder → title keyword → unknown.
That is why a lecture on YouTube counts as learning while a gameplay video counts as entertainment.

## 11. Deliverables and tasks

FocusLedger cares less about how busy you were than about whether the important things moved.

```bash
python -m focusledger deliverable add "FocusLedger MVP" --priority 1 --keywords focusledger,pycharm
python -m focusledger deliverable list --today      # with today's time on each
python -m focusledger deliverable update <id> --due 2026-08-01
python -m focusledger deliverable done <id>
```

`--keywords` drives automatic attribution: when a keyword appears in an app name or window title, that time is credited to the deliverable.

```bash
python -m focusledger task add "Wire Phase 8 review" --deliverable FocusLedger --estimate 90
python -m focusledger task list --open
python -m focusledger task done <id>
python -m focusledger task block <id> "waiting for their data"
```

## 12. Distraction alerts

During a workday the agent watches for sustained entertainment or gaming. Past a threshold it sends one local notification (with a cooldown, so it never nags) and records an event for you to explain. **A "distraction" without a reason is a judgement, not information.**

```bash
python -m focusledger distraction list --today
python -m focusledger distraction reasons
python -m focusledger distraction label <id> rabbit_hole
```

Thresholds and the cooldown live in the config file: `gaming_alert_minutes`, `entertainment_alert_minutes`, `notification_cooldown_seconds`, `audio_enabled`.

Gaming alerts rely on section 10: games installed through a game store are now recognised as gaming, so they trigger alerts too.

## 13. Local LLM

The written part of the daily review comes from a small local model through Ollama: no data leaves the machine, it costs nothing, and it adds no Python dependency.

```bash
python -m focusledger llm status      # check availability and get setup hints
python -m focusledger llm test        # send one test sentence
```

Setup: install [Ollama](https://ollama.com/download) → `ollama pull qwen2.5:1.5b` → done.
To switch models, change `llm_model` in the config (`qwen2.5:3b`, `qwen3:1.7b`, `llama3.2:1b` and `gemma2:2b` all work).

**It works without a model too**: the review falls back to a deterministic rule-based version with exactly the same figures.

### Ask about your data: analyst

`analyst` lets a local model look things up through tools and then answer. Every figure is computed in Python; the model only decides what to look at and how to say it.
If the answer contains a number that no tool returned, it is sent back for a rewrite; numbers still unsupported after that are flagged at the end.

```bash
python -m focusledger analyst ask "Where did my time go last week?"
python -m focusledger analyst ask "When was I most focused on Aug 7?" --trace   # show every tool call and the thinking
python -m focusledger analyst ask "..." --model deepseek-r1:8b                 # use another model once
python -m focusledger analyst eval --models qwen3.5:9b,qwen3:8b --out eval.json  # same questions, several models
python -m focusledger analyst tools                                             # list the tools
python -m focusledger analyst tool day_summary --args '{"date": "2026-08-07"}'  # run one tool without a model
```

- The default model is `llm_analyst_model` (`qwen3.5:9b`). The analyst calls tools over several rounds, which the 1.5B review model cannot do well; use around 8B or larger.
  There is no `config set` command for it; edit the config file, e.g. `"llm_analyst_model": "deepseek-r1:8b"`. Use `--model` to switch for one question.
- Tools are called in one of two ways: `native` uses Ollama's tool API; `text` has the model write a JSON call in its reply. The default `auto` probes each model once and falls back to `text` when a model never emits native calls (such as `deepseek-r1:8b`).
- `--think` / `--no-think` turns the model's thinking on or off, `--num-ctx` sets the context window (default 16384), and `--tool-mode` forces a tool-calling mode.
- Read tools: `list_days`, `day_summary`, `range_summary`, `compare_ranges`, `app_usage`, `timeline`, `hourly_profile`, `deliverables`. Change tools: `mark_absent`, `correct_category` (below).

#### Changing data: the model proposes, you approve

The model can suggest two kinds of change. It cannot write anything itself: each one becomes a proposal with a preview, and only your yes applies it.

| Tool | What it does | Example request |
|---|---|---|
| `mark_absent` | Marks time on one day as away from the computer, labelled `sleep`, `away`, `offline`, `meal` or `break`. Covers every stretch one app was in front (e.g. `(unknown)`, the lock screen) or a clock range such as 23:30–07:10 | "8月11号的unknown全是在睡觉，帮我在数据库里面改了" |
| `correct_category` | Always classifies an app, site or title keyword as a category, the same as `classify correct` (section 10) | "把 explorer.exe 归到 admin" |

```bash
python -m focusledger analyst ask "8月11号的unknown全是在睡觉，帮我在数据库里面改了"   # shows the proposal, asks y/N
python -m focusledger analyst ask "..." --yes        # apply proposals without asking
python -m focusledger analyst changes                # every change made on your behalf
python -m focusledger analyst undo 248e5a05d057      # reverse one by its id
```

A proposal looks like this:

```text
Proposed change: Mark every stretch when (unknown) was in front on 2026-08-11 as sleep (8.6 h)
  stretches: 4
  from: "04:31"
  to: "16:35"
  effective_hours_before: 16.6
  effective_hours_after: 8.0
Apply this change? [y/N]
```

- When there is no terminal to answer in (a script, a pipe), nothing is applied; re-run with `--yes`.
- Raw samples are never rewritten. A marked stretch is stored as a note beside them and applied whenever a day is read, so marked time counts as **hard idle** (weight 0), shows under **Idle** in the categories, and the report tooltip says "marked sleep". Undo deletes the note and the day reads as before. This works the same for archived days.
- Every applied change is kept in the database with what is needed to undo it, whether it came from the page or the terminal. The page lists them under **Changes made for you**.

Measured on this machine (RTX 5070 12 GB, Ollama 0.40, the five built-in `analyst eval` questions, 2026-10-22, every answer checked by hand against the tool results):

| Model | Tool calls | Fully correct | Median time | Problems |
|---|---|---:|---:|---|
| `qwen3.5:9b` | native | 5/5 | 22 s | never needed a rewrite |
| `qwen3:8b` | native | 2/5 | 32 s | computed its own shares; picked the wrong dates when comparing ranges and reversed the direction of a change; misreported lock-screen time |
| `deepseek-r1:8b` | text | 2/5 | 55 s | never makes native tool calls; searched app names for "games"; misread the hourly profile |

DeepSeek's newer V4 models are cloud-only on Ollama (`deepseek-v4.1-flash:cloud` and others). They would send your data to Ollama's servers, which breaks this project's rule that data stays on the machine, so they are not supported. The newest DeepSeek weights that run locally are `deepseek-r1:8b`.

## 14. Daily review and plan for tomorrow

```bash
python -m focusledger review write        # record your own progress first (the system cannot see real output)
python -m focusledger review generate     # generate the review
python -m focusledger review show
python -m focusledger review list
python -m focusledger review pending      # days still missing a review
python -m focusledger review generate --no-llm   # force the rule-based version
```

Reviews are saved as Markdown and also written to `<data_dir>/journal/<date>.md`. They contain the workday range, objective figures, deliverable progress, attention and distractions, the retrospective, tomorrow's plan, missing data, and classifications worth correcting.

## 15. Start at login

```bash
python -m focusledger autostart install     # macOS launchd / Windows Task Scheduler / Linux systemd
python -m focusledger autostart status
python -m focusledger autostart uninstall
```

## 16. Sampling interval

Check the current interval with `status` or in the config file. Set it to 1 second:

```bash
python -m focusledger config set poll-interval 1
```

Restart the agent for it to take effect:

```bash
python -m focusledger stop
python -m focusledger start --background
```

## 17. Attention settings

Show the current thresholds and weights:

```bash
python -m focusledger config attention
```

Set the four thresholds in minutes:

```bash
python -m focusledger config set attention-thresholds 3 10 20 30
```

Set the five weights in order:

```bash
python -m focusledger config set attention-weights 1.0 0.5 0.0 0.0 0.0
```

The order of the weights:

```text
active low_activity attention_gap interrupted hard_idle
```

## 18. Database maintenance: retention, archiving, compaction, backup

The database holds three layers:

```text
Raw samples    one row per sample (one per second by default), kept for the last 7 local dates
Archive        each day past retention, condensed into a few hundred app and attention segments, kept indefinitely
Your records   workdays, deliverables, notes, reviews, classification corrections and so on, never pruned
```

Show and change how many days of raw samples are kept:

```bash
python -m focusledger config retention
python -m focusledger config set raw-retention-days 14
```

Archive raw samples past retention right now. The agent does this whenever the date changes; while it is stopped, days pile up. Old heartbeats are removed at the same time:

```bash
python -m focusledger db prune
```

Space freed by archiving is handed back to the disk automatically. Databases created by older versions cannot do that and keep the space inside the file for reuse; to shrink the file now, stop the agent and the report server, then run:

```bash
python -m focusledger db compact --yes
```

To see how big the database is and what fills it, click **Database** at the top of the report page (section 8).

**Backup**: stop the agent and the report server, then copy the database file. Its default location:

```text
Windows        %LOCALAPPDATA%\FocusLedger\focusledger.sqlite3
macOS / Linux  ~/.focusledger/focusledger.sqlite3
```

PowerShell example:

```powershell
Copy-Item "$env:LOCALAPPDATA\FocusLedger\focusledger.sqlite3" "$env:LOCALAPPDATA\FocusLedger\backups\focusledger-backup.sqlite3"
```

When backing up before an upgrade, **do not run any `focusledger` command first** (not even `db path`): the first command upgrades the database.

**Restore**: stop the agent and the report server, copy the backup back to the original location as `focusledger.sqlite3`, and delete `focusledger.sqlite3-wal` and `focusledger.sqlite3-shm` next to it.

The schema version is stored in SQLite's `user_version`. Upgrades run as numbered steps, each exactly once.

## 19. Privacy maintenance

Redact private/incognito browser titles already stored in the database:

```bash
python -m focusledger privacy redact-private-browser-titles
```

The rules:

```text
ordinary browser titles are kept
private/incognito window titles are stored as [private browsing]
```

Archived days have no window handles, so they can only be redacted when the browser segment's own title carries a private-browsing marker; later titles of the same private window cannot be recovered there. This is mainly for catching up old data after new markers are added (for example another language).

A macOS special case: if records came from the subprocess fallback without PyObjC (`window_handle` is empty), this command can only redact rows whose own title still carries an Incognito/private marker. Titles from the same private window after it navigated away cannot be linked back. That is not a bug in the command; the data simply lacks a handle that ties those rows to one window.

## 20. Reset the database while testing

Show which database would be reset, without deleting it:

```bash
python -m focusledger dev reset-db
```

Delete and recreate it:

```bash
python -m focusledger dev reset-db --yes
```

This deletes the local SQLite database, which is useful for starting over while testing.

## 21. Common workflows

One-time setup:

```bash
python -m focusledger init
python -m focusledger autostart install
python -m focusledger deliverable add "Your main goal" --keywords keyword
```

Every day:

```bash
python -m focusledger workday start
```

Look at today:

```bash
python -m focusledger timeline --today
python -m focusledger summary --today
python -m focusledger attention --today --timeline
```

The live dashboard at any time (you can also browse past days, ask the local model and check the database size there):

```bash
python -m focusledger report --today
```

Ask the local model:

```bash
python -m focusledger analyst ask "How did my effective time change compared with last week?"
```

After the agent has been stopped for a while, archive the backlog first:

```bash
python -m focusledger status
python -m focusledger db prune
```

End of the day:

```bash
python -m focusledger workday offwork
python -m focusledger review write
python -m focusledger review generate
```

After a reboot (the agent does not start at login unless autostart is installed):

```bash
python -m focusledger status
python -m focusledger start --background
```

Start over while testing:

```bash
python -m focusledger stop
python -m focusledger dev reset-db --yes
python -m focusledger start --background
```
