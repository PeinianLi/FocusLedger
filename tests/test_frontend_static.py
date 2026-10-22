"""Static checks on the browser bundle.

There is no browser or JS runtime in CI, so these cannot prove the UI *feels*
right — they exist to rule out the failure mode that already bit us once: a
page that loads and then does nothing because of a silent JS-level mistake.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "focusledger" / "server" / "static"
APP_JS = STATIC / "app.js"
APP_CSS = STATIC / "app.css"
INDEX = STATIC / "index.html"
VENDOR = STATIC / "vendor"


class BundleLayoutTests(unittest.TestCase):
    def test_all_bundle_files_exist(self) -> None:
        for path in [APP_JS, APP_CSS, INDEX]:
            self.assertTrue(path.is_file(), path)

    def test_vendored_libraries_are_present_and_whole(self) -> None:
        expected = {
            "react.production.min.js": 5_000,
            "react-dom.production.min.js": 100_000,
            "htm.umd.js": 500,
        }
        for name, min_size in expected.items():
            path = VENDOR / name
            self.assertTrue(path.is_file(), f"missing vendored {name}")
            self.assertGreater(path.stat().st_size, min_size, f"{name} looks truncated")

    def test_index_loads_every_asset_it_needs(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        for ref in [
            "/static/vendor/react.production.min.js",
            "/static/vendor/react-dom.production.min.js",
            "/static/vendor/htm.umd.js",
            "/static/app.js",
            "/static/app.css",
        ]:
            self.assertIn(ref, html, f"index.html does not load {ref}")
        self.assertIn('id="root"', html)

    def test_no_external_origins_are_referenced(self) -> None:
        """The UI must work with no network beyond the local server."""
        for path in [INDEX, APP_JS, APP_CSS]:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(
                re.search(r"https?://(?!127\.0\.0\.1|localhost)", text),
                f"{path.name} reaches outside the machine",
            )


class AppScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = APP_JS.read_text(encoding="utf-8")

    def test_pointer_coordinates_never_use_offset_x(self) -> None:
        """Regression guard.

        ``offsetX`` is relative to the event target, so once the pointer is over
        an SVG mark every derived time is wrong and hover silently dies. All
        coordinates must come from clientX minus the container rect.
        """
        self.assertNotIn("offsetX", self.src)
        self.assertNotIn("offsetY", self.src)
        self.assertIn("getBoundingClientRect", self.src)

    def test_react_props_use_className(self) -> None:
        """htm forwards attributes verbatim, so `class=` would not be React."""
        self.assertIsNone(re.search(r"\bclass=", self.src))
        self.assertIn("className=", self.src)

    def test_marks_are_inert_so_the_stage_owns_pointer_events(self) -> None:
        css = APP_CSS.read_text(encoding="utf-8")
        self.assertIn("svg.tl * { pointer-events: none; }", css)

    def test_every_component_used_is_defined(self) -> None:
        defined = set(re.findall(r"function ([A-Z]\w+)\(", self.src))
        used = set(re.findall(r"<\$\{([A-Z]\w+)\}", self.src))
        self.assertTrue(used, "no components referenced — template syntax changed?")
        self.assertFalse(used - defined, f"used but not defined: {used - defined}")

    def test_zoom_floor_is_enforced(self) -> None:
        match = re.search(r"var MIN_SPAN = (\d+)", self.src)
        self.assertIsNotNone(match, "MIN_SPAN not declared")
        self.assertGreaterEqual(int(match.group(1)), 1)
        # The floor is meaningless unless clamping actually applies it.
        self.assertIn("MIN_SPAN", self.src.split("function clampView")[0] + self.src)
        self.assertIn("clamp(v.end - v.start, MIN_SPAN", self.src)

    def test_static_and_live_modes_are_both_wired(self) -> None:
        self.assertIn('getElementById("focusledger-data")', self.src)  # snapshot mode
        self.assertIn('fetch("/api/report', self.src)                  # live mode
        self.assertIn("createRoot", self.src)

    def test_analyst_questions_are_posted_as_json(self) -> None:
        """The server refuses other content types; that refusal is the CSRF guard."""
        self.assertIn('postJson("/api/analyst/ask"', self.src)
        self.assertIn('postJson("/api/analyst/proposals/"', self.src)
        self.assertIn('"Content-Type": "application/json"', self.src)
        self.assertEqual(self.src.count('method: "POST"'), 1, "every POST should go through postJson")

    def test_applied_changes_stay_undoable(self) -> None:
        """A card unmounts when its run folds, so its status must not live in the card."""
        card = self.src[self.src.index("function ProposalCard"):self.src.index("function ChangeLog")]
        self.assertIn("var proposal = props.proposal;", card)
        self.assertNotIn("useState(props.proposal)", card)
        self.assertIn('fetch("/api/changes"', self.src)  # the list that outlives a reload

    def test_model_answers_are_never_injected_as_html(self) -> None:
        """Answers are model output; they are rendered as React elements only."""
        self.assertNotIn("dangerouslySetInnerHTML", self.src)
        self.assertNotIn("innerHTML", self.src)

    def test_brackets_balance(self) -> None:
        stripped = re.sub(r'"(?:[^"\\]|\\.)*"', '""', self.src)
        stripped = re.sub(r"'(?:[^'\\]|\\.)*'", "''", stripped)
        stripped = re.sub(r"//[^\n]*", "", stripped)
        for open_ch, close_ch in [("{", "}"), ("(", ")"), ("[", "]")]:
            self.assertEqual(
                stripped.count(open_ch),
                stripped.count(close_ch),
                f"unbalanced {open_ch}{close_ch}",
            )


if __name__ == "__main__":
    unittest.main()
