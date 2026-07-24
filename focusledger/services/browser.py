"""Phase 4: browser context from window titles.

Without an extension there is no URL to read, so the only signal available is
the window title. Browsers all format it as ``<page title> - <Browser>``, and
many sites leave a recognisable fingerprint in the page title itself.

This is deliberately the conservative half of Phase 4: it recovers *site* and
*page*, never a full URL, and never guesses when the title has been redacted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BROWSER_PROCESSES = {
    "chrome", "google chrome", "chrome.exe", "msedge", "msedge.exe", "microsoft edge",
    "firefox", "firefox.exe", "safari", "brave", "brave browser", "brave.exe",
    "opera", "opera.exe", "vivaldi", "vivaldi.exe", "arc", "chromium",
}

PRIVATE_TITLE = "[private browsing]"

# Suffixes browsers append to the page title.
_SUFFIXES = [
    " - Google Chrome", " — Google Chrome", " - Chromium", " - Microsoft Edge",
    " — Microsoft Edge", " - Mozilla Firefox", " — Mozilla Firefox", " - Brave",
    " - Opera", " - Vivaldi", " - Safari", " — Safari", " - Arc",
]

# Page-title fingerprints -> canonical site. Ordered: first match wins.
_SITE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("youtube.com", re.compile(r"\bYouTube\b", re.I)),
    ("bilibili.com", re.compile(r"哔哩哔哩|\bbilibili\b", re.I)),
    ("github.com", re.compile(r"\bGitHub\b", re.I)),
    ("stackoverflow.com", re.compile(r"\bStack Overflow\b", re.I)),
    ("docs.python.org", re.compile(r"Python\s+\d|Python documentation", re.I)),
    ("google.com", re.compile(r"\bGoogle (Search|搜索)\b|- Google\s*$", re.I)),
    ("mail.google.com", re.compile(r"\bGmail\b", re.I)),
    ("drive.google.com", re.compile(r"\bGoogle (Drive|Docs|Sheets|Slides)\b", re.I)),
    ("notion.so", re.compile(r"\bNotion\b", re.I)),
    ("arxiv.org", re.compile(r"\barXiv\b", re.I)),
    ("wikipedia.org", re.compile(r"\bWikipedia\b|维基百科", re.I)),
    ("reddit.com", re.compile(r"\bReddit\b|\br/\w+", re.I)),
    ("x.com", re.compile(r"\b(Twitter|X)\s*$|\/ X\b", re.I)),
    ("linkedin.com", re.compile(r"\bLinkedIn\b", re.I)),
    ("coursera.org", re.compile(r"\bCoursera\b", re.I)),
    ("leetcode.com", re.compile(r"\bLeetCode\b", re.I)),
    ("zhihu.com", re.compile(r"知乎", re.I)),
    ("chatgpt.com", re.compile(r"\bChatGPT\b", re.I)),
    ("claude.ai", re.compile(r"\bClaude\b", re.I)),
]

# A domain written into the title, e.g. "example.com/page — Firefox".
# Captures a trailing slash so "docs.rs/tokio" can be told apart from "main.rs".
_DOMAIN_IN_TITLE = re.compile(r"\b([a-z0-9-]+(?:\.[a-z0-9-]+)+)(/)?", re.I)

# Two-label candidates are only domains when the last label is a real TLD;
# otherwise "main.py" and "report.rs" would read as sites.
_COMMON_TLDS = {
    "com", "org", "net", "io", "dev", "ai", "co", "edu", "gov", "info", "app",
    "cn", "uk", "de", "jp", "fr", "ru", "br", "in", "it", "nl", "se", "no",
}
_FILE_EXTENSIONS = {
    "py", "js", "jsx", "ts", "tsx", "css", "scss", "html", "htm", "json", "md",
    "txt", "yml", "yaml", "toml", "cfg", "ini", "log", "sh", "rb", "go", "rs",
    "java", "c", "h", "cpp", "hpp", "cs", "php", "sql", "csv", "pdf", "png",
    "jpg", "svg", "zip", "tar", "gz", "lock", "env", "xml", "vue", "swift",
}


def _looks_like_domain(candidate: str, has_path: bool) -> bool:
    labels = candidate.lower().split(".")
    if len(labels) < 2:
        return False
    last = labels[-1]
    if len(labels) == 2 and last in _FILE_EXTENSIONS and not has_path:
        # "main.py", "report.rs" — a filename, unless a path follows it.
        return False
    if len(labels) >= 3:
        return True
    return has_path or last in _COMMON_TLDS


@dataclass(frozen=True)
class BrowserContext:
    """What could be recovered about a browser window."""

    is_browser: bool
    is_private: bool
    site: str | None
    page_title: str | None

    @property
    def known(self) -> bool:
        return self.site is not None


def is_browser_process(process_name: str | None) -> bool:
    return bool(process_name) and process_name.strip().lower() in BROWSER_PROCESSES


def parse_browser_context(process_name: str | None, window_title: str | None) -> BrowserContext:
    """Recover site and page title from a browser window title."""
    if not is_browser_process(process_name):
        return BrowserContext(False, False, None, None)

    title = (window_title or "").strip()
    if not title:
        return BrowserContext(True, False, None, None)

    # Already redacted upstream: do not try to infer anything from it.
    if title == PRIVATE_TITLE:
        return BrowserContext(True, True, None, None)

    page = _strip_browser_suffix(title)
    site = _detect_site(title)
    return BrowserContext(True, False, site, page or None)


def _strip_browser_suffix(title: str) -> str:
    for suffix in _SUFFIXES:
        if title.endswith(suffix):
            return title[: -len(suffix)].strip()
    # Generic "… - Browser" fallback when the exact suffix is unknown.
    if " - " in title:
        head, _, tail = title.rpartition(" - ")
        if tail.strip().lower() in BROWSER_PROCESSES:
            return head.strip()
    return title


def _detect_site(title: str) -> str | None:
    for site, pattern in _SITE_PATTERNS:
        if pattern.search(title):
            return site
    for candidate, slash in _DOMAIN_IN_TITLE.findall(title):
        if _looks_like_domain(candidate, bool(slash)):
            return candidate.lower()
    return None
