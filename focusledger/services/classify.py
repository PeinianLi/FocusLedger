"""Phase 5: activity classification.

Classification is derived, never stored: a segment's category is recomputed
from the current rules every time it is asked for. Editing a rule or adding a
correction therefore re-explains the whole history instead of only affecting
future samples.

Resolution order follows the spec, most specific first:

    1. manual correction
    2. deliverable-linked rule
    3. site rule
    4. process/app rule, then the app's install folder (game stores)
    5. title keyword rule
    6. fallback unknown
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from sqlite3 import Connection

from focusledger.services.browser import parse_browser_context
from focusledger.timeutil import overlap_seconds, parse_moment

PRODUCTIVE_CORE = "productive_core"
LEARNING_GENERAL = "learning_general"
NEUTRAL_EDUCATIONAL = "neutral_educational"
ADMIN = "admin"
ENTERTAINMENT = "entertainment"
GAMING = "gaming"
SOCIAL = "social"
COMMUNICATION = "communication"
IDLE = "idle"
ATTENTION_GAP = "attention_gap"
UNKNOWN = "unknown"

CATEGORIES = (
    PRODUCTIVE_CORE, LEARNING_GENERAL, NEUTRAL_EDUCATIONAL, ADMIN,
    ENTERTAINMENT, GAMING, SOCIAL, COMMUNICATION, IDLE, ATTENTION_GAP, UNKNOWN,
)

CATEGORY_LABELS = {
    PRODUCTIVE_CORE: "Core productive",
    LEARNING_GENERAL: "Learning",
    NEUTRAL_EDUCATIONAL: "General knowledge",
    ADMIN: "Admin",
    ENTERTAINMENT: "Entertainment",
    GAMING: "Gaming",
    SOCIAL: "Social",
    COMMUNICATION: "Communication",
    IDLE: "Idle",
    ATTENTION_GAP: "Attention gap",
    UNKNOWN: "Unknown",
}

# Categories that count as real output for the review.
PRODUCTIVE_CATEGORIES = (PRODUCTIVE_CORE, LEARNING_GENERAL)
DISTRACTING_CATEGORIES = (ENTERTAINMENT, GAMING, SOCIAL)

MATCH_MANUAL = "manual"
MATCH_DELIVERABLE = "deliverable"
MATCH_LEARNED = "learned"
MATCH_SITE = "site"
MATCH_PROCESS = "process"
MATCH_TITLE = "title"
MATCH_FALLBACK = "fallback"

SCOPE_PROCESS = "process"
SCOPE_SITE = "site"
SCOPE_TITLE = "title_keyword"
SCOPES = (SCOPE_PROCESS, SCOPE_SITE, SCOPE_TITLE)


@dataclass(frozen=True)
class Classification:
    category: str
    matched_by: str
    detail: str | None = None
    deliverable_id: str | None = None
    site: str | None = None

    @property
    def confidence(self) -> float:
        """How much to trust this label, by how specific the match was."""
        return {
            MATCH_MANUAL: 1.0,
            MATCH_DELIVERABLE: 0.9,
            MATCH_LEARNED: 0.85,
            MATCH_SITE: 0.8,
            MATCH_PROCESS: 0.7,
            MATCH_TITLE: 0.6,
            MATCH_FALLBACK: 0.0,
        }[self.matched_by]


# --------------------------------------------------------------------------
# built-in rules
# --------------------------------------------------------------------------
_PROCESS_RULES: list[tuple[frozenset[str], str]] = [
    (frozenset({"code", "visual studio code", "code.exe", "pycharm", "pycharm.exe", "intellij idea",
                "pycharm64.exe", "idea64.exe", "webstorm64.exe", "clion64.exe", "goland64.exe",
                "rider64.exe", "datagrip64.exe",
                "webstorm", "clion", "goland", "rider", "sublime text", "vim", "nvim", "emacs",
                "xcode", "android studio", "cursor", "codex", "codex.exe", "zed", "terminal",
                "iterm2", "warp",
                "windowsterminal.exe", "powershell.exe", "cmd.exe", "alacritty", "kitty",
                "wezterm", "docker desktop", "postman", "tableplus", "dbeaver",
                "pgadmin4.exe"}), PRODUCTIVE_CORE),
    (frozenset({"steam", "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "battle.net",
                "riotclientux.exe", "leagueclient.exe", "minecraft", "eu4", "dota2", "csgo",
                "cs2"}), GAMING),
    (frozenset({"discord", "discord.exe"}), SOCIAL),
    (frozenset({"slack", "slack.exe", "microsoft teams", "teams.exe", "zoom.us", "zoom.exe",
                "wechat", "weixin.exe", "微信", "telegram", "signal", "lark", "feishu",
                "飞书"}), COMMUNICATION),
    # The desktop apps get the same conservative label as chatgpt.com/claude.ai.
    (frozenset({"chatgpt", "chatgpt.exe", "claude", "claude.exe"}), NEUTRAL_EDUCATIONAL),
    (frozenset({"spotify", "spotify.exe", "vlc", "quicktime player", "netflix", "iina",
                "music", "tv"}), ENTERTAINMENT),
    (frozenset({"mail", "outlook", "outlook.exe", "olk.exe", "microsoft outlook", "calendar",
                "reminders",
                "notes", "finder", "explorer.exe", "system settings", "system preferences",
                "activity monitor", "task manager", "1password", "keychain access"}), ADMIN),
    (frozenset({"notion", "obsidian", "logseq", "typora", "zotero", "mendeley",
                "microsoft word", "winword.exe", "excel.exe", "microsoft excel",
                "powerpnt.exe", "microsoft powerpoint", "pages", "numbers", "keynote",
                "preview", "adobe acrobat", "acrobat.exe"}), PRODUCTIVE_CORE),
]

_SITE_RULES: dict[str, str] = {
    "github.com": PRODUCTIVE_CORE,
    "stackoverflow.com": PRODUCTIVE_CORE,
    "docs.python.org": PRODUCTIVE_CORE,
    "leetcode.com": LEARNING_GENERAL,
    "arxiv.org": LEARNING_GENERAL,
    "coursera.org": LEARNING_GENERAL,
    "chatgpt.com": NEUTRAL_EDUCATIONAL,
    "claude.ai": NEUTRAL_EDUCATIONAL,
    "wikipedia.org": NEUTRAL_EDUCATIONAL,
    "zhihu.com": NEUTRAL_EDUCATIONAL,
    "notion.so": PRODUCTIVE_CORE,
    "drive.google.com": PRODUCTIVE_CORE,
    "mail.google.com": ADMIN,
    "google.com": NEUTRAL_EDUCATIONAL,
    "reddit.com": SOCIAL,
    "x.com": SOCIAL,
    "linkedin.com": SOCIAL,
    "youtube.com": ENTERTAINMENT,     # refined by title keywords below
    "bilibili.com": ENTERTAINMENT,
}

# Title keywords that pull video sites toward learning, checked before the
# site default so a lecture does not get filed as entertainment.
_LEARNING_TITLE_HINTS = (
    "lecture", "tutorial", "course", "lesson", "explained", "introduction to",
    "deep dive", "paper", "proof", "derivation", "algorithm", "documentation",
    "docs", "教程", "课程", "讲座", "公开课", "原理", "入门",
)
_ENTERTAINMENT_TITLE_HINTS = (
    "gameplay", "walkthrough", "trailer", "highlights", "funny", "vlog", "reaction",
    "music video", "mv", "live stream", "游戏", "搞笑", "鬼畜", "综艺", "电影",
)
_GAMING_TITLE_HINTS = ("steam", "gameplay", "speedrun", "ranked match")


# Install folders of game stores. A game's executable name is arbitrary
# (Dangan3Win.exe, swhaop.exe), but where the store installs it is not.
_GAME_LIBRARY_PATH_HINTS = (
    "/steamapps/common/",
    "/epic games/",
    "/gog galaxy/games/",
    "/xboxgames/",
    "/riot games/",
    "/ubisoft game launcher/games/",
    "/ea games/",
)


def _match_process(process_name: str | None) -> str | None:
    if not process_name:
        return None
    key = process_name.strip().lower()
    for names, category in _PROCESS_RULES:
        if key in names:
            return category
    return None


def _match_install_path(executable_path: str | None) -> str | None:
    if not executable_path:
        return None
    normalized = executable_path.replace("\\", "/").lower()
    for hint in _GAME_LIBRARY_PATH_HINTS:
        if hint in normalized:
            return hint.strip("/")
    return None


def _match_title(title: str | None) -> str | None:
    if not title:
        return None
    lowered = title.lower()
    if any(hint in lowered for hint in _LEARNING_TITLE_HINTS):
        return LEARNING_GENERAL
    if any(hint in lowered for hint in _ENTERTAINMENT_TITLE_HINTS):
        return ENTERTAINMENT
    if any(hint in lowered for hint in _GAMING_TITLE_HINTS):
        return GAMING
    return None


def _match_site(site: str | None, title: str | None) -> str | None:
    if not site:
        return None
    base = _SITE_RULES.get(site)
    if base in (ENTERTAINMENT, NEUTRAL_EDUCATIONAL):
        # Video and search sites carry both kinds of use; let the page title
        # decide rather than writing off the whole domain.
        refined = _match_title(title)
        if refined:
            return refined
    if base:
        return base
    # Unknown host: allow a suffix match so subdomains inherit.
    for known, category in _SITE_RULES.items():
        if site == known or site.endswith("." + known):
            return category
    return None


# --------------------------------------------------------------------------
# stored overrides
# --------------------------------------------------------------------------
def add_correction(conn: Connection, device_id: str, scope: str, pattern: str, category: str) -> None:
    if scope not in SCOPES:
        raise ValueError(f"unsupported scope: {scope}")
    if category not in CATEGORIES:
        raise ValueError(f"unknown category: {category}")
    now = datetime.now().astimezone().isoformat()
    conn.execute(
        """
        INSERT INTO manual_corrections(id, device_id, scope, pattern, category, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope, pattern) DO UPDATE SET
            category = excluded.category, created_at = excluded.created_at
        """,
        (uuid.uuid4().hex, device_id, scope, pattern.strip().lower(), category, now),
    )
    conn.commit()


def list_corrections(conn: Connection) -> list[dict[str, object]]:
    rows = conn.execute("SELECT * FROM manual_corrections ORDER BY scope, pattern").fetchall()
    return [dict(r) for r in rows]


def remove_correction(conn: Connection, scope: str, pattern: str) -> bool:
    cur = conn.execute(
        "DELETE FROM manual_corrections WHERE scope = ? AND pattern = ?",
        (scope, pattern.strip().lower()),
    )
    conn.commit()
    return cur.rowcount > 0


def add_rule(conn: Connection, scope: str, pattern: str, category: str, note: str = "") -> None:
    """Store a learned rule. Ranks below a manual correction, above the built-ins."""
    if scope not in SCOPES:
        raise ValueError(f"unsupported scope: {scope}")
    if category not in CATEGORIES:
        raise ValueError(f"unknown category: {category}")
    now = datetime.now().astimezone().isoformat()
    pattern = pattern.strip().lower()
    conn.execute(
        "DELETE FROM classification_rules WHERE match_type = ? AND pattern = ?", (scope, pattern)
    )
    conn.execute(
        "INSERT INTO classification_rules(id, priority, match_type, pattern, category, note,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, 50, scope, pattern, category, note, now),
    )
    conn.commit()


def list_rules(conn: Connection) -> list[dict[str, object]]:
    return [
        dict(r) for r in conn.execute(
            "SELECT * FROM classification_rules ORDER BY match_type, pattern"
        ).fetchall()
    ]


def remove_rule(conn: Connection, scope: str, pattern: str) -> bool:
    cur = conn.execute(
        "DELETE FROM classification_rules WHERE match_type = ? AND pattern = ?",
        (scope, pattern.strip().lower()),
    )
    conn.commit()
    return cur.rowcount > 0


def load_rule_context(conn: Connection) -> dict[str, object]:
    """Snapshot the stored rules once so a whole day can be classified cheaply."""
    corrections: dict[str, dict[str, str]] = {scope: {} for scope in SCOPES}
    for row in list_corrections(conn):
        corrections[str(row["scope"])][str(row["pattern"])] = str(row["category"])

    learned: dict[str, dict[str, str]] = {scope: {} for scope in SCOPES}
    for row in list_rules(conn):
        learned[str(row["match_type"])][str(row["pattern"])] = str(row["category"])

    deliverables = [
        dict(r)
        for r in conn.execute(
            "SELECT id, title, match_keywords FROM deliverables WHERE status != 'archived'"
        ).fetchall()
    ]
    return {"corrections": corrections, "learned": learned, "deliverables": deliverables}


def _match_deliverable(context: dict[str, object], process_name: str | None, title: str | None):
    from focusledger.services.deliverables import keyword_matches, usable_keywords

    haystack = f"{process_name or ''} {title or ''}".lower()
    for deliverable in context.get("deliverables", []):  # type: ignore[union-attr]
        for term in usable_keywords(deliverable):
            if keyword_matches(term, haystack):
                return deliverable, term
    return None, None


# --------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------
def classify(
    process_name: str | None,
    window_title: str | None,
    context: dict[str, object] | None = None,
    executable_path: str | None = None,
) -> Classification:
    """Resolve one activity segment to a category."""
    context = context or {"corrections": {s: {} for s in SCOPES},
                          "learned": {s: {} for s in SCOPES}, "deliverables": []}
    corrections: dict[str, dict[str, str]] = context.get("corrections", {})  # type: ignore[assignment]
    learned: dict[str, dict[str, str]] = context.get("learned", {})  # type: ignore[assignment]

    browser = parse_browser_context(process_name, window_title)
    site = browser.site
    effective_title = browser.page_title if browser.is_browser else window_title

    # 1. manual correction — most specific scope first
    if site and site in corrections.get(SCOPE_SITE, {}):
        return Classification(corrections[SCOPE_SITE][site], MATCH_MANUAL, site, site=site)
    key = (process_name or "").strip().lower()
    if key and key in corrections.get(SCOPE_PROCESS, {}):
        return Classification(corrections[SCOPE_PROCESS][key], MATCH_MANUAL, key, site=site)
    lowered_title = (effective_title or "").lower()
    for pattern, category in corrections.get(SCOPE_TITLE, {}).items():
        if pattern and pattern in lowered_title:
            return Classification(category, MATCH_MANUAL, pattern, site=site)

    # 2. deliverable link
    deliverable, term = _match_deliverable(context, process_name, effective_title)
    if deliverable is not None:
        return Classification(
            PRODUCTIVE_CORE, MATCH_DELIVERABLE, term,
            deliverable_id=str(deliverable["id"]), site=site,
        )

    # 3. rules learned for this machine (e.g. labelled by the local model)
    if site and site in learned.get(SCOPE_SITE, {}):
        return Classification(learned[SCOPE_SITE][site], MATCH_LEARNED, site, site=site)
    if key and key in learned.get(SCOPE_PROCESS, {}):
        return Classification(learned[SCOPE_PROCESS][key], MATCH_LEARNED, key, site=site)
    for pattern, category in learned.get(SCOPE_TITLE, {}).items():
        if pattern and pattern in lowered_title:
            return Classification(category, MATCH_LEARNED, pattern, site=site)

    # 4. site
    site_category = _match_site(site, effective_title)
    if site_category:
        return Classification(site_category, MATCH_SITE, site, site=site)

    # 4. process
    process_category = _match_process(process_name)
    if process_category:
        # A browser process alone says nothing about what is on screen.
        if not browser.is_browser:
            return Classification(process_category, MATCH_PROCESS, key, site=site)
    game_library = _match_install_path(executable_path)
    if game_library and not browser.is_browser:
        return Classification(GAMING, MATCH_PROCESS, game_library, site=site)

    # 6. title keyword
    title_category = _match_title(effective_title)
    if title_category:
        return Classification(title_category, MATCH_TITLE, None, site=site)

    if browser.is_private:
        return Classification(UNKNOWN, MATCH_FALLBACK, "private browsing", site=site)
    return Classification(UNKNOWN, MATCH_FALLBACK, None, site=site)


def classify_segments(
    segments: list[dict[str, object]], context: dict[str, object]
) -> list[dict[str, object]]:
    """Attach a classification to each activity segment."""
    out = []
    for segment in segments:
        result = classify(
            str(segment.get("process") or segment.get("process_name") or ""),
            str(segment.get("title") or segment.get("window_title") or ""),
            context,
            executable_path=segment.get("executable_path"),  # type: ignore[arg-type]
        )
        enriched = dict(segment)
        enriched["category"] = result.category
        enriched["matched_by"] = result.matched_by
        enriched["confidence"] = result.confidence
        enriched["site"] = result.site
        enriched["deliverable_id"] = result.deliverable_id
        out.append(enriched)
    return out


def attach_attention_weight(
    classified: list[dict[str, object]], attention_segments: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Weight each activity segment by the attention overlapping it.

    Without this, a window left open while the user is away would count the
    same as a window being worked in, and every "effective" figure downstream
    would be inflated to the raw wall-clock time.
    """
    spans = []
    for segment in attention_segments:
        start = parse_moment(segment.get("started_at"))
        end = parse_moment(segment.get("ended_at"))
        if start is None or end is None:
            continue
        spans.append((start, end, float(segment.get("attention_weight") or 0.0)))

    for segment in classified:
        start = parse_moment(segment.get("started_at"))
        end = parse_moment(segment.get("ended_at"))
        if start is None or end is None:
            segment["attention_weight"] = 1.0
            continue
        total = (end - start).total_seconds()
        if total <= 0:
            segment["attention_weight"] = 0.0
            continue
        weighted = 0.0
        for span_start, span_end, weight in spans:
            shared = overlap_seconds(start, end, span_start, span_end)
            if shared > 0:
                weighted += shared * weight
        segment["attention_weight"] = min(1.0, weighted / total)
    return classified


def summarize_categories(classified: list[dict[str, object]]) -> dict[str, float]:
    """Total seconds per category."""
    totals: dict[str, float] = {}
    for segment in classified:
        category = str(segment["category"])
        totals[category] = totals.get(category, 0.0) + float(segment.get("duration_seconds") or 0.0)
    return totals


def unknown_targets(classified: list[dict[str, object]]) -> list[dict[str, object]]:
    """Unclassified apps/sites, worst offenders first, for the fix-up command."""
    buckets: dict[tuple[str, str], dict[str, object]] = {}
    for segment in classified:
        if segment["category"] != UNKNOWN:
            continue
        site = segment.get("site")
        # Timeline rows carry `process_name`; classified copies carry `process`.
        # Reading only one of them silently produced "(unknown)" as the pattern,
        # which made both the fix-up command and the model prompt useless.
        process = segment.get("process") or segment.get("process_name") or "(unknown)"
        scope, pattern = (SCOPE_SITE, str(site)) if site else (
            SCOPE_PROCESS, str(process).lower()
        )
        entry = buckets.setdefault(
            (scope, pattern),
            {
                "scope": scope, "pattern": pattern, "seconds": 0.0,
                "sample_title": segment.get("title") or segment.get("window_title"),
            },
        )
        entry["seconds"] = float(entry["seconds"]) + float(segment.get("duration_seconds") or 0.0)
    return sorted(buckets.values(), key=lambda e: float(e["seconds"]), reverse=True)


# --------------------------------------------------------------------------
# offline LLM fallback (spec Phase 5.5)
# --------------------------------------------------------------------------
_LLM_SYSTEM = (
    "You label computer applications and websites by what a person uses them for. "
    "You reply only with lines of the form `pattern => category`. "
    "No prose, no explanation, no markdown."
)

# Only the meanings a human would actually pick from; idle/attention_gap are
# attention states, not activities, so they are excluded from the choices.
_LLM_CHOICES = (
    PRODUCTIVE_CORE, LEARNING_GENERAL, NEUTRAL_EDUCATIONAL, ADMIN,
    ENTERTAINMENT, GAMING, SOCIAL, COMMUNICATION,
)


def build_label_prompt(targets: list[dict[str, object]]) -> str:
    """Ask for a category per unknown app/site, with sample titles as evidence."""
    lines = ["Label each of these by what the person was most likely doing.", ""]
    for entry in targets:
        sample = str(entry.get("sample_title") or "").strip()[:80]
        kind = "website" if entry["scope"] == SCOPE_SITE else "application"
        lines.append(
            f"- {entry['pattern']}  ({kind}"
            + (f', example window title: "{sample}"' if sample else "")
            + ")"
        )
    lines += [
        "",
        "Allowed categories, use exactly these strings:",
        ", ".join(_LLM_CHOICES),
        "",
        "Reply with one line per item, nothing else:",
        "pattern => category",
    ]
    return "\n".join(lines)


def parse_label_reply(reply: str, targets: list[dict[str, object]]) -> list[dict[str, str]]:
    """Read the model's answer, keeping only lines that name a real target and category.

    A small model will occasionally invent a category or a pattern; anything not
    recognised is dropped rather than guessed at, so a bad reply degrades to
    fewer labels instead of wrong ones.
    """
    by_pattern = {str(t["pattern"]).lower(): t for t in targets}
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for raw in reply.splitlines():
        line = raw.strip().lstrip("-*• ").strip()
        if "=>" not in line:
            continue
        pattern, _, category = line.partition("=>")
        pattern = pattern.strip().strip("`\"'").lower()
        category = category.strip().strip("`\"'.").lower()
        target = by_pattern.get(pattern)
        if target is None or category not in _LLM_CHOICES or pattern in seen:
            continue
        seen.add(pattern)
        out.append({
            "scope": str(target["scope"]),
            "pattern": pattern,
            "category": category,
            "seconds": float(target.get("seconds") or 0.0),
        })
    return out


def label_unknowns_with_llm(conn: Connection, config, targets, apply: bool = True):
    """Have the local model label unknown apps/sites, then store them as rules.

    Runs offline on demand — never inside the collection loop, per the spec:
    a model call there would add latency and let a model failure cost samples.
    """
    from focusledger.services import llm as llm_service

    if not targets:
        return []
    reply = llm_service.generate(
        config, _LLM_SYSTEM, build_label_prompt(targets), temperature=0.0, max_tokens=400
    )
    labels = parse_label_reply(reply, targets)
    if apply:
        model = getattr(config, "llm_model", "local")
        for label in labels:
            add_rule(conn, label["scope"], label["pattern"], label["category"], note=f"llm:{model}")
    return labels
