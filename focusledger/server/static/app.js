/* FocusLedger live report UI — React 18 + htm (no build step).
 *
 * Two delivery modes, one codebase:
 *   live   — fetches /api/report and polls, so the page tracks the agent
 *   static — reads an inlined <script id="focusledger-data"> payload (export)
 */
(function () {
  "use strict";

  var useState = React.useState, useEffect = React.useEffect, useMemo = React.useMemo,
      useRef = React.useRef, useCallback = React.useCallback, useLayoutEffect = React.useLayoutEffect;
  var html = htm.bind(React.createElement);

  // ---------- constants ----------
  var STATE_ORDER = ["active", "low_activity", "attention_gap", "interrupted", "hard_idle", "unknown"];
  var STATE_LABEL = {
    active: "Active", low_activity: "Low activity", attention_gap: "Attention gap",
    interrupted: "Interrupted", hard_idle: "Hard idle", unknown: "Unknown"
  };
  var cssVar = function (n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); };
  var STATE_COLOR = {};
  STATE_ORDER.forEach(function (s) { STATE_COLOR[s] = cssVar("--st-" + s); });
  var ACCENT = cssVar("--accent"), INK3 = cssVar("--ink-3"), SURFACE = cssVar("--surface");
  var GRID = cssVar("--grid"), GRID_STRONG = cssVar("--grid-strong");
  var MONO = cssVar("--mono");
  var APP_PALETTE = ["#9085e9", "#d55181", "#3987e5", "#c98500", "#199e70", "#d95926"];
  var APP_OTHER = "#5a6d88";

  // Category colours: one hue family per meaning — green/blue = output,
  // warm = leaking time, grey = unknown. Kept distinct from the attention
  // state colours by living on their own row with their own legend.
  var CATEGORY_COLOR = {
    productive_core: "#22c39a",
    learning_general: "#3987e5",
    neutral_educational: "#9085e9",
    admin: "#5f7a99",
    communication: "#199e70",
    social: "#d55181",
    entertainment: "#fab219",
    gaming: "#d95926",
    idle: "#3d4c60",
    attention_gap: "#8a6a2f",
    unknown: "#5a6d88"
  };
  var CAT = { h: 26, top: 3, bottom: 3 };

  var MIN_SPAN = 2;            // minutes — how deep zoom can go
  var SESSION_MIN = 25, SLACK_MIN = 5;
  var BUCKET = 15;             // focus-density bucket, minutes
  var PAD = { left: 6, right: 6 };
  var SKY = { top: 12, bottom: 8, h: 168 };
  var DEN = { top: 4, bottom: 4, h: 48 };
  var LANE_H = 20, LANE_GAP = 4, APP_TOP = 8, APP_BOTTOM = 24;
  var MM = { h: 46, top: 4, bottom: 8 };
  var FLOOR = 7;

  // ---------- helpers ----------
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function fmtDur(sec) {
    sec = Math.round(sec || 0);
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    if (h) return h + "h " + pad2(m) + "m";
    if (m) return m + "m " + pad2(s) + "s";
    return s + "s";
  }
  function fmtDurLong(sec) {
    sec = Math.round(sec || 0);
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    if (h) return h + "h " + pad2(m) + "m " + pad2(s) + "s";
    if (m) return m + "m " + pad2(s) + "s";
    return s + "s";
  }
  function splitDur(sec) {
    sec = Math.round(sec || 0);
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    if (h) return [h + "h " + pad2(m), "m"];
    if (m) return [m + "m " + pad2(s), "s"];
    return [String(s), "s"];
  }
  function clock(min) { var t = Math.max(0, min); return pad2(Math.floor(t / 60) % 24) + ":" + pad2(Math.floor(t % 60)); }
  function clockS(min) {
    var t = Math.max(0, min);
    return pad2(Math.floor(t / 60) % 24) + ":" + pad2(Math.floor(t % 60)) + ":" + pad2(Math.floor((t * 60) % 60));
  }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function overlap(a0, a1, b0, b1) { return Math.max(0, Math.min(a1, b1) - Math.max(a0, b0)); }
  function fmtWeight(w) { w = (w == null ? 0 : w); return (w % 1 === 0) ? w.toFixed(1) : String(w); }
  function fmtBytes(n) {
    n = n || 0;
    if (n >= 1e9) return (n / 1e9).toFixed(2) + " GB";
    if (n >= 1e6) return (n / 1e6).toFixed(1) + " MB";
    if (n >= 1e3) return (n / 1e3).toFixed(0) + " KB";
    return n + " B";
  }
  function fmtCount(n) { return (n || 0).toLocaleString("en-US"); }
  function nowMinutes() { var d = new Date(); return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60; }

  // ---------- data ----------
  function readInlineData() {
    var node = document.getElementById("focusledger-data");
    if (!node) return null;
    try { return JSON.parse(node.textContent); } catch (e) { return null; }
  }

  // Tracked dates, today and the date the server was started for. Refreshed
  // now and then so a day the agent starts while the page is open appears.
  function useDays(isStatic) {
    var s = useState(null); var days = s[0], setDays = s[1];
    useEffect(function () {
      if (isStatic) return;
      var alive = true, timer = null;
      function load() {
        fetch("/api/days", { cache: "no-store" })
          .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
          .then(function (payload) { if (alive) setDays(payload); })
          .catch(function () {})
          .finally(function () { if (alive) timer = setTimeout(load, 60000); });
      }
      load();
      return function () { alive = false; if (timer) clearTimeout(timer); };
    }, [isStatic]);
    return days;
  }

  // Database size and what fills it. Counting rows is cheap but not free, so
  // this refreshes once a minute rather than with every report poll.
  function useStorage(isStatic, refreshKey) {
    var s = useState(null); var storage = s[0], setStorage = s[1];
    useEffect(function () {
      if (isStatic) return;
      var alive = true, timer = null;
      function load() {
        fetch("/api/storage", { cache: "no-store" })
          .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
          .then(function (payload) { if (alive) setStorage(payload); })
          .catch(function () {})
          .finally(function () { if (alive) timer = setTimeout(load, 60000); });
      }
      load();
      return function () { alive = false; if (timer) clearTimeout(timer); };
    }, [isStatic, refreshKey]);
    return storage;
  }

  // The report for one day. Only today is live: it is polled at the pace of
  // the agent, while a past day is fetched once. The data of the previous day
  // stays on screen until the next arrives, so switching does not blank the page.
  function useReport(date, today, refreshKey) {
    var inline = useMemo(readInlineData, []);
    var isStatic = !!inline;
    var state = useState(inline);
    var data = state[0], setData = state[1];
    var errState = useState(null); var error = errState[0], setError = errState[1];
    var stampState = useState(inline ? Date.now() : null);
    var updatedAt = stampState[0], setUpdatedAt = stampState[1];
    var loadingState = useState(false); var loading = loadingState[0], setLoading = loadingState[1];

    useEffect(function () {
      if (isStatic || !date) return;
      var alive = true;
      var timer = null;
      var nextDelay = 5000;
      var live = date === today;
      setLoading(true);
      function tick() {
        fetch("/api/report?date=" + (live ? "today" : date), { cache: "no-store" })
          .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
          .then(function (payload) {
            if (!alive) return;
            setData(payload); setError(null); setUpdatedAt(Date.now()); setLoading(false);
            var agent = payload && payload.agent;
            nextDelay = Math.max(2000, ((agent && agent.poll_interval_seconds) || 5) * 1000);
          })
          .catch(function (e) { if (alive) { setError(String(e.message || e)); setLoading(false); } })
          .finally(function () {
            if (!alive || !live) return;
            timer = setTimeout(tick, nextDelay);
          });
      }
      tick();
      return function () { alive = false; if (timer) clearTimeout(timer); };
      // eslint-disable-next-line
    }, [isStatic, date, today, refreshKey]);

    return { data: data, error: error, updatedAt: updatedAt, isStatic: isStatic, loading: loading };
  }

  // ---------- derived analytics ----------
  function useDerived(data) {
    return useMemo(function () {
      if (!data) return null;
      var att = data.attention || {};
      var attSegs = (att.segments || []).slice().sort(function (a, b) { return a.start_min - b.start_min; });
      var actSegs = ((data.activity && data.activity.segments) || []).slice().sort(function (a, b) { return a.start_min - b.start_min; });

      // app lanes, ordered by whole-day time
      var appRank = ((data.activity && data.activity.apps) || []).map(function (a) { return a.process; });
      var laneNames = appRank.slice(0, APP_PALETTE.length);
      var hasOther = appRank.length > laneNames.length;
      if (hasOther) laneNames = laneNames.concat(["Other"]);
      if (!laneNames.length) laneNames = ["—"];
      var colorMap = {};
      appRank.forEach(function (p, i) { if (i < APP_PALETTE.length) colorMap[p] = APP_PALETTE[i]; });

      // collapse consecutive same-process runs (title change is not a switch)
      var procRuns = [];
      actSegs.forEach(function (s) {
        var last = procRuns[procRuns.length - 1];
        if (last && last.process === s.process && s.start_min - last.end_min <= 1) last.end_min = s.end_min;
        else procRuns.push({ process: s.process, start_min: s.start_min, end_min: s.end_min });
      });

      // deep-work sessions: sustained weight >= 0.5, tolerating short dips
      var sessions = [], cur = null;
      function flush() { if (cur && cur.end - cur.start >= SESSION_MIN) sessions.push(cur); cur = null; }
      attSegs.forEach(function (s) {
        var dur = s.end_min - s.start_min;
        if (s.weight >= 0.5) {
          if (!cur) cur = { start: s.start_min, end: s.end_min, eff: dur * 60 * s.weight, slack: 0 };
          else { cur.end = s.end_min; cur.eff += dur * 60 * s.weight; cur.slack = 0; }
        } else if (cur) {
          cur.slack += dur;
          if (cur.slack > SLACK_MIN) flush();
        }
      });
      flush();

      var lo = Infinity, hi = -Infinity;
      attSegs.concat(actSegs).forEach(function (s) { lo = Math.min(lo, s.start_min); hi = Math.max(hi, s.end_min); });
      var domain = null;
      if (isFinite(lo)) {
        domain = { start: Math.floor(lo / 60) * 60, end: Math.ceil(hi / 60) * 60 };
        if (domain.end - domain.start < 60) domain.end = domain.start + 60;
      }

      var catSegs = ((data.categories && data.categories.segments) || [])
        .slice().sort(function (a, b) { return a.start_min - b.start_min; });

      var trackedHours = (att.raw_seconds || 0) / 3600;
      return {
        catSegs: catSegs,
        categoryTotals: (data.categories && data.categories.totals) || {},
        categoryLabels: (data.categories && data.categories.labels) || {},
        workday: data.workday || null,
        distractions: data.distractions || [],
        deliverables: data.deliverables || { attributed: [], stalled: [], open_tasks: [] },
        review: data.review || null,
        attSegs: attSegs, actSegs: actSegs, laneNames: laneNames, hasOther: hasOther,
        colorMap: colorMap, procRuns: procRuns, sessions: sessions, domain: domain,
        switches: Math.max(0, procRuns.length - 1),
        switchRate: trackedHours > 0 ? Math.max(0, procRuns.length - 1) / trackedHours : 0,
        deepWorkSec: sessions.reduce(function (a, s) { return a + (s.end - s.start) * 60; }, 0),
        hasData: attSegs.length > 0 || actSegs.length > 0
      };
    }, [data]);
  }

  function laneIndexOf(d, proc) {
    var i = d.laneNames.indexOf(proc);
    if (i >= 0 && (!d.hasOther || i < d.laneNames.length - 1)) return i;
    return d.hasOther ? d.laneNames.length - 1 : 0;
  }
  function appColorOf(d, p) { return d.colorMap[p] || APP_OTHER; }

  // ---------- width measurement ----------
  function useWidth(ref) {
    var s = useState(900); var width = s[0], setWidth = s[1];
    useLayoutEffect(function () {
      if (!ref.current) return;
      function measure() { if (ref.current) setWidth(ref.current.clientWidth || 900); }
      measure();
      var ro = (typeof ResizeObserver !== "undefined") ? new ResizeObserver(measure) : null;
      if (ro && ref.current) ro.observe(ref.current);
      window.addEventListener("resize", measure);
      return function () { if (ro) ro.disconnect(); window.removeEventListener("resize", measure); };
    }, [ref]);
    return width;
  }

  // ---------- small presentational pieces ----------
  function Swatch(props) { return html`<span className="swatch" style=${{ background: props.color }}></span>`; }

  function LivePill(props) {
    var agent = props.agent, isStatic = props.isStatic;
    if (isStatic) return html`<span className="live-pill"><span className="dot"></span>Snapshot</span>`;
    if (!agent || agent.status === "unknown") return html`<span className="live-pill"><span className="dot"></span>No agent</span>`;
    var stale = agent.stale;
    return html`<span className=${"live-pill " + (stale ? "stale" : "on")}>
      <span className="dot"></span>${stale ? "Agent stale" : "Live"}
    </span>`;
  }

  function StatTiles(props) {
    var data = props.data, d = props.derived;
    var att = data.attention || {};
    var raw = att.raw_seconds || 0, eff = att.effective_seconds || 0;
    var ratio = raw > 0 ? eff / raw : 0;
    var apps = (data.activity && data.activity.apps) || [];
    var top = apps.length ? apps[0] : null;
    var longest = d.sessions.length ? Math.max.apply(null, d.sessions.map(function (s) { return (s.end - s.start) * 60; })) : 0;
    var tiles = [
      { label: "Effective focus", v: splitDur(eff), accent: true, foot: "weighted attention time" },
      { label: "Tracked", v: splitDur(raw), foot: "raw sampled time" },
      { label: "Focus ratio", v: [Math.round(ratio * 100), "%"], meter: ratio, foot: fmtDur(att.totals ? att.totals.active : 0) + " fully active" },
      { label: "Deep work", v: splitDur(d.deepWorkSec), foot: d.sessions.length + " session" + (d.sessions.length === 1 ? "" : "s") + (longest ? " · best " + fmtDur(longest) : "") },
      { label: "App switches", v: [String(d.switches), "x"], foot: d.switchRate ? d.switchRate.toFixed(1) + " per hour" : "—" },
      { label: "Top app", text: top ? top.process : "—", foot: top ? fmtDur(top.total_seconds) : "no activity" }
    ];
    return html`<div className="stats">
      ${tiles.map(function (t, i) {
        return html`<div className=${"tile" + (t.accent ? " accent" : "")} key=${i}>
          <div className="label">${t.label}</div>
          ${t.text != null
            ? html`<div className="value small">${t.text}</div>`
            : html`<div className="value">${t.v[0]}<small> ${t.v[1]}</small></div>`}
          ${t.meter != null ? html`<div className="meter"><i style=${{ width: Math.round(t.meter * 100) + "%" }}></i></div>` : null}
          <div className="foot">${t.foot}</div>
        </div>`;
      })}
    </div>`;
  }

  // ---------- chart panel ----------
  function ChartPanel(props) {
    var data = props.data, d = props.derived;
    var view = props.view, setView = props.setView;
    var isolated = props.isolated, setIsolated = props.setIsolated;
    var hoverApp = props.hoverApp;
    var live = props.live;

    var stackRef = useRef(null);
    var width = useWidth(stackRef);
    var cursorState = useState(null); var cursor = cursorState[0], setCursor = cursorState[1];
    var selState = useState(null); var sel = selState[0], setSel = selState[1];
    var dragRef = useRef(null);
    var panState = useState(false); var panning = panState[0], setPanning = panState[1];

    var span = view.end - view.start;
    var plotW = Math.max(10, width - PAD.left - PAD.right);
    var xOf = useCallback(function (min) { return PAD.left + (min - view.start) / (view.end - view.start) * plotW; }, [view, plotW]);
    var minAt = useCallback(function (px) { return view.start + (px - PAD.left) / plotW * (view.end - view.start); }, [view, plotW]);

    var domain = d.domain;
    var clampView = useCallback(function (v) {
      var sp = clamp(v.end - v.start, MIN_SPAN, domain.end - domain.start);
      var mid = (v.start + v.end) / 2;
      var s = mid - sp / 2, e = mid + sp / 2;
      if (s < domain.start) { e += domain.start - s; s = domain.start; }
      if (e > domain.end) { s -= e - domain.end; e = domain.end; }
      return { start: Math.max(domain.start, s), end: Math.min(domain.end, e) };
    }, [domain]);

    // wheel must be a non-passive native listener to preventDefault
    useEffect(function () {
      var node = stackRef.current;
      if (!node) return;
      function onWheel(ev) {
        ev.preventDefault();
        var rect = node.getBoundingClientRect();
        var px = ev.clientX - rect.left;
        var anchor = clamp(minAt(px), view.start, view.end);
        var factor = Math.exp(ev.deltaY * (ev.ctrlKey ? 0.012 : 0.0026));
        var newSpan = clamp((view.end - view.start) * factor, MIN_SPAN, domain.end - domain.start);
        var frac = (anchor - view.start) / (view.end - view.start);
        setView(clampView({ start: anchor - frac * newSpan, end: anchor - frac * newSpan + newSpan }));
      }
      node.addEventListener("wheel", onWheel, { passive: false });
      return function () { node.removeEventListener("wheel", onWheel); };
    }, [minAt, view, domain, clampView, setView]);

    function relX(ev) {
      var rect = stackRef.current.getBoundingClientRect();
      return ev.clientX - rect.left;
    }
    function onPointerDown(ev) {
      var px = relX(ev);
      stackRef.current.setPointerCapture(ev.pointerId);
      if (ev.shiftKey) { dragRef.current = { mode: "sel", anchor: px }; setSel({ x0: px, x1: px }); }
      else { dragRef.current = { mode: "pan", startX: ev.clientX, view: { start: view.start, end: view.end } }; setPanning(true); }
    }
    function onPointerMove(ev) {
      var px = relX(ev);
      var rect = stackRef.current.getBoundingClientRect();
      var drag = dragRef.current;
      if (drag && drag.mode === "pan") {
        var dMin = -(ev.clientX - drag.startX) / plotW * (drag.view.end - drag.view.start);
        setView(clampView({ start: drag.view.start + dMin, end: drag.view.end + dMin }));
        return;
      }
      if (drag && drag.mode === "sel") { setSel({ x0: Math.min(drag.anchor, px), x1: Math.max(drag.anchor, px) }); return; }
      setCursor({ min: clamp(minAt(px), view.start, view.end), x: px, y: ev.clientY - rect.top });
    }
    function onPointerUp(ev) {
      var drag = dragRef.current;
      if (drag && drag.mode === "sel" && sel && sel.x1 - sel.x0 > 6) {
        setView(clampView({ start: minAt(sel.x0), end: minAt(sel.x1) }));
      }
      dragRef.current = null; setSel(null); setPanning(false);
      try { stackRef.current.releasePointerCapture(ev.pointerId); } catch (e) {}
    }
    function onPointerLeave() { if (!dragRef.current) setCursor(null); }

    // ---- segment geometry (memoised: cursor moves must not rebuild these) ----
    var skyBase = SKY.h - SKY.bottom;
    var skyRects = useMemo(function () {
      var out = [];
      d.attSegs.forEach(function (s, i) {
        if (s.end_min <= view.start || s.start_min >= view.end) return;
        var a = Math.max(s.start_min, view.start), b = Math.min(s.end_min, view.end);
        var x0 = xOf(a), x1 = xOf(b);
        var h = FLOOR + s.weight * (skyBase - SKY.top - FLOOR);
        out.push({ i: i, x: x0 + 0.7, y: skyBase - h, w: Math.max(0.8, x1 - x0 - 1.4), h: h, state: s.state });
      });
      return out;
    }, [d.attSegs, view, xOf, skyBase]);

    var laneRects = useMemo(function () {
      var out = [];
      d.actSegs.forEach(function (s, i) {
        if (s.end_min <= view.start || s.start_min >= view.end) return;
        var a = Math.max(s.start_min, view.start), b = Math.min(s.end_min, view.end);
        var x0 = xOf(a), x1 = xOf(b);
        var y = APP_TOP + laneIndexOf(d, s.process) * (LANE_H + LANE_GAP);
        out.push({ i: i, x: x0 + 0.5, y: y + 1, w: Math.max(0.8, x1 - x0 - 1), proc: s.process });
      });
      return out;
    }, [d, view, xOf]);

    var densityBars = useMemo(function () {
      var out = [];
      var base = DEN.h - DEN.bottom;
      var first = Math.floor(view.start / BUCKET) * BUCKET;
      for (var t = first; t < view.end; t += BUCKET) {
        var b0 = Math.max(t, domain.start), b1 = Math.min(t + BUCKET, domain.end);
        if (b1 <= b0) continue;
        var eff = 0;
        d.attSegs.forEach(function (at) {
          if (at.weight <= 0) return;
          var o = overlap(b0, b1, at.start_min, at.end_min);
          if (o > 0) eff += o * 60 * at.weight;
        });
        var cap = (b1 - b0) * 60;
        var ratio = cap > 0 ? Math.min(1, eff / cap) : 0;
        if (ratio <= 0.005) continue;
        var x0 = xOf(Math.max(b0, view.start)), x1 = xOf(Math.min(b1, view.end));
        var h = Math.max(1, (base - DEN.top) * ratio);
        out.push({ x: x0 + 0.7, y: base - h, w: Math.max(0.8, x1 - x0 - 1.4), h: h, key: t });
      }
      return out;
    }, [d.attSegs, view, xOf, domain]);

    var catRects = useMemo(function () {
      var out = [];
      (d.catSegs || []).forEach(function (s, i) {
        if (s.end_min <= view.start || s.start_min >= view.end) return;
        var a = Math.max(s.start_min, view.start), b = Math.min(s.end_min, view.end);
        var x0 = xOf(a), x1 = xOf(b);
        out.push({ key: i, x: x0 + 0.5, w: Math.max(0.8, x1 - x0 - 1), category: s.category });
      });
      return out;
    }, [d.catSegs, view, xOf]);

    var overlays = useMemo(function () {
      var missing = [], marks = [], lines = [];
      var wd = d.workday;
      if (wd) {
        if (wd.start_min != null && wd.start_min >= view.start && wd.start_min <= view.end) {
          lines.push({ key: "ws", x: xOf(wd.start_min), kind: "start", label: "workday" });
        }
        if (wd.end_min != null && wd.end_min >= view.start && wd.end_min <= view.end) {
          lines.push({ key: "we", x: xOf(wd.end_min), kind: "end", label: "offwork" });
        }
        (wd.missing_intervals || []).forEach(function (m, i) {
          if (m.end_min <= view.start || m.start_min >= view.end) return;
          var a = Math.max(m.start_min, view.start), b = Math.min(m.end_min, view.end);
          missing.push({ key: i, x: xOf(a), w: Math.max(2, xOf(b) - xOf(a)),
                         title: "No agent data — " + fmtDur(m.duration_seconds) });
        });
      }
      (d.distractions || []).forEach(function (ev, i) {
        if (ev.at_min < view.start || ev.at_min > view.end) return;
        marks.push({ key: i, x: xOf(ev.at_min),
                     title: ev.kind + " " + fmtDur(ev.duration_seconds) +
                            (ev.reason ? " — " + ev.reason : " — unlabeled") });
      });
      return { missing: missing, marks: marks, lines: lines };
    }, [d.workday, d.distractions, view, xOf]);

    var ticks = useMemo(function () {
      var sp = view.end - view.start;
      var step = sp <= 12 ? 2 : sp <= 45 ? 5 : sp <= 120 ? 15 : sp <= 300 ? 30 : sp <= 600 ? 60 : 120;
      var out = [];
      for (var t = Math.ceil(view.start / step) * step; t <= view.end + 0.01; t += step) {
        out.push({ t: t, x: xOf(t), major: t % 60 === 0 });
      }
      return out;
    }, [view, xOf]);

    var gaps = useMemo(function () {
      return d.attSegs.filter(function (s) {
        if (s.weight > 0 || s.end_min - s.start_min < 20) return false;
        return !(s.end_min <= view.start || s.start_min >= view.end);
      }).map(function (s) {
        var a = Math.max(s.start_min, view.start), b = Math.min(s.end_min, view.end);
        return { x: (xOf(a) + xOf(b)) / 2, w: xOf(b) - xOf(a), label: "· " + fmtDur(s.duration_seconds) + " " + STATE_LABEL[s.state].toLowerCase() + " ·", key: s.start_min };
      }).filter(function (g) { return g.w > 76; });
    }, [d.attSegs, view, xOf]);

    // ---- cursor lookups ----
    var atCursor = useMemo(function () {
      if (!cursor) return null;
      function find(list) {
        for (var i = 0; i < list.length; i++) if (cursor.min >= list[i].start_min && cursor.min < list[i].end_min) return { seg: list[i], i: i };
        return null;
      }
      return { att: find(d.attSegs), act: find(d.actSegs) };
    }, [cursor, d.attSegs, d.actSegs]);

    var laneCount = d.laneNames.length;
    var appH = APP_TOP + laneCount * (LANE_H + LANE_GAP) - LANE_GAP + APP_BOTTOM;
    var nowX = (live && domain) ? xOf(nowMinutes()) : null;
    var showNow = nowX != null && nowMinutes() >= view.start && nowMinutes() <= view.end;

    function segClass(state, isHot) {
      var c = "seg";
      if (isolated && state !== isolated) c += " dim";
      if (isHot) c += " hot";
      return c;
    }

    var presets = [["15m", 15], ["1h", 60], ["3h", 180], ["Day", null]];

    return html`<section className="panel">
      <header>
        <h2>Focus Skyline <span>// attention · density · app lanes</span></h2>
        <div className="controls">
          <span className="zoom-span">${clock(view.start)}–${clock(view.end)} · ${fmtDur(span * 60)}</span>
          ${presets.map(function (p) {
            return html`<button key=${p[0]} type="button" className="btn" onClick=${function () {
              if (p[1] == null) { setView({ start: domain.start, end: domain.end }); return; }
              var c = live ? Math.min(nowMinutes(), domain.end) : (view.start + view.end) / 2;
              setView(clampView({ start: c - p[1] / 2, end: c + p[1] / 2 }));
            }}>${p[0]}</button>`;
          })}
        </div>
      </header>

      <div className="legend">
        ${STATE_ORDER.filter(function (s) { return (data.attention.totals || {})[s] > 0; }).map(function (s) {
          return html`<button key=${s} type="button" className=${isolated === s ? "on" : ""}
            onClick=${function () { setIsolated(isolated === s ? null : s); }}>
            <${Swatch} color=${STATE_COLOR[s]} />${STATE_LABEL[s]}
            <span className="dur">×${fmtWeight((data.attention.weights || {})[s])} · ${fmtDur(data.attention.totals[s])}</span>
          </button>`;
        })}
      </div>

      <div className="readout">
        <div className="ro-item"><span className="ro-k">Time</span><span className="ro-v">${cursor ? clockS(cursor.min) : html`<span className="txt muted">—</span>`}</span></div>
        <div className="ro-item"><span className="ro-k">State</span><span className="ro-v">
          ${atCursor && atCursor.att
            ? html`<${Swatch} color=${STATE_COLOR[atCursor.att.seg.state]} /><span className="txt">${STATE_LABEL[atCursor.att.seg.state]} ×${fmtWeight(atCursor.att.seg.weight)}</span>`
            : html`<span className="txt muted">hover the timeline</span>`}
        </span></div>
        <div className="ro-item grow"><span className="ro-k">App</span><span className="ro-v">
          ${atCursor && atCursor.act
            ? html`<${Swatch} color=${appColorOf(d, atCursor.act.seg.process)} /><span className="txt">${atCursor.act.seg.process}${atCursor.act.seg.title ? "  ·  " + atCursor.act.seg.title : ""}</span>`
            : html`<span className="txt muted">—</span>`}
        </span></div>
      </div>

      <div className="tl-stack" ref=${stackRef}
        onPointerDown=${onPointerDown} onPointerMove=${onPointerMove}
        onPointerUp=${onPointerUp} onPointerCancel=${onPointerUp}
        onPointerLeave=${onPointerLeave}
        onDoubleClick=${function () { setView({ start: domain.start, end: domain.end }); }}>

        <div className=${"stage" + (panning ? " grabbing" : "")}>
          <svg className="tl" viewBox=${"0 0 " + width + " " + SKY.h} height=${SKY.h}>
            ${ticks.map(function (t) { return html`<line key=${t.t} x1=${t.x} x2=${t.x} y1=${SKY.top} y2=${skyBase} stroke=${t.major ? GRID_STRONG : GRID} />`; })}
            ${[1.0, 0.5].map(function (g) {
              var y = skyBase - (FLOOR + g * (skyBase - SKY.top - FLOOR));
              return html`<g key=${g}>
                <line x1=${PAD.left} x2=${width - PAD.right} y1=${y} y2=${y} stroke=${GRID} stroke-dasharray="2 5" />
                <text x=${width - PAD.right} y=${y - 3} fill=${INK3} font-size="8.5" font-family=${MONO} text-anchor="end">×${g.toFixed(1)}</text>
              </g>`;
            })}
            <line x1=${PAD.left} x2=${width - PAD.right} y1=${skyBase} y2=${skyBase} stroke=${GRID_STRONG} />
            ${skyRects.map(function (r) {
              return html`<rect key=${r.i} x=${r.x} y=${r.y} width=${r.w} height=${r.h} rx=${Math.min(3, r.w / 2)}
                fill=${STATE_COLOR[r.state]} opacity="0.92"
                className=${segClass(r.state, atCursor && atCursor.att && atCursor.att.i === r.i)} />`;
            })}
            ${gaps.map(function (g) {
              return html`<text key=${g.key} x=${g.x} y=${skyBase - (skyBase - SKY.top) * 0.5} fill=${INK3}
                font-size="9.5" font-family=${MONO} text-anchor="middle">${g.label}</text>`;
            })}
          </svg>
        </div>

        <div className="stage">
          <svg className="tl" viewBox=${"0 0 " + width + " " + DEN.h} height=${DEN.h}>
            ${ticks.map(function (t) { return html`<line key=${t.t} x1=${t.x} x2=${t.x} y1=${DEN.top} y2=${DEN.h - DEN.bottom} stroke=${t.major ? GRID_STRONG : GRID} />`; })}
            <line x1=${PAD.left} x2=${width - PAD.right} y1=${DEN.h - DEN.bottom} y2=${DEN.h - DEN.bottom} stroke=${GRID} />
            ${densityBars.map(function (b) { return html`<rect key=${b.key} x=${b.x} y=${b.y} width=${b.w} height=${b.h} rx="2" fill=${ACCENT} opacity="0.55" />`; })}
            <rect x=${PAD.left + 2} y=${DEN.top + 1} width="92" height="13" rx="3" fill=${SURFACE} opacity="0.78" />
            <text x=${PAD.left + 7} y=${DEN.top + 10.5} fill=${INK3} font-size="8.5" font-family=${MONO}>FOCUS /${BUCKET}MIN</text>
          </svg>
        </div>

        <div className="stage">
          <svg className="tl" viewBox=${"0 0 " + width + " " + CAT.h} height=${CAT.h}>
            <rect x=${PAD.left} y=${CAT.top} width=${plotW} height=${CAT.h - CAT.top - CAT.bottom}
              rx="3" fill="rgba(255,255,255,0.025)" />
            ${catRects.map(function (r) {
              return html`<rect key=${r.key} x=${r.x} y=${CAT.top} width=${r.w}
                height=${CAT.h - CAT.top - CAT.bottom} rx="2"
                fill=${CATEGORY_COLOR[r.category] || CATEGORY_COLOR.unknown} opacity="0.85" />`;
            })}
            <rect x=${PAD.left + 2} y=${CAT.top + 3} width="62" height="13" rx="3" fill=${SURFACE} opacity="0.8" />
            <text x=${PAD.left + 7} y=${CAT.top + 12.5} fill=${INK3} font-size="8.5" font-family=${MONO}>CATEGORY</text>
          </svg>
        </div>

        <div className="stage">
          <svg className="tl" viewBox=${"0 0 " + width + " " + appH} height=${appH}>
            ${ticks.map(function (t) { return html`<line key=${t.t} x1=${t.x} x2=${t.x} y1=${APP_TOP} y2=${appH - APP_BOTTOM} stroke=${t.major ? GRID_STRONG : GRID} />`; })}
            ${d.laneNames.map(function (n, li) {
              return html`<rect key=${"bg" + li} x=${PAD.left} y=${APP_TOP + li * (LANE_H + LANE_GAP)} width=${plotW} height=${LANE_H} rx="3" fill="rgba(255,255,255,0.025)" />`;
            })}
            ${laneRects.map(function (r) {
              var dim = hoverApp && r.proc !== hoverApp;
              return html`<rect key=${r.i} x=${r.x} y=${r.y} width=${r.w} height=${LANE_H - 2} rx="2.5"
                fill=${appColorOf(d, r.proc)} opacity=${dim ? 0.14 : 0.88}
                className=${(atCursor && atCursor.act && atCursor.act.i === r.i) ? "seg hot" : "seg"} />`;
            })}
            ${d.laneNames.map(function (n, li) {
              var y = APP_TOP + li * (LANE_H + LANE_GAP);
              var text = n.length > 22 ? n.slice(0, 21) + "…" : n;
              return html`<g key=${"lb" + li}>
                <rect x=${PAD.left + 2} y=${y + 2.5} width=${text.length * 5.7 + 20} height=${LANE_H - 5} rx="3" fill=${SURFACE} opacity="0.78" />
                <rect x=${PAD.left + 7} y=${y + LANE_H / 2 - 3.5} width="7" height="7" rx="2" fill=${n === "Other" ? APP_OTHER : appColorOf(d, n)} />
                <text x=${PAD.left + 18} y=${y + LANE_H / 2 + 3.4} fill=${INK3} font-size="9.5" font-family=${MONO}>${text}</text>
              </g>`;
            })}
            ${ticks.map(function (t) {
              return html`<text key=${"lb" + t.t} x=${t.x} y=${appH - 7} fill=${INK3} font-size="10" font-family=${MONO} text-anchor="middle">${clock(t.t)}</text>`;
            })}
          </svg>
        </div>

        ${overlays.missing.map(function (m) {
          return html`<div key=${"m" + m.key} className="overlay-missing" title=${m.title}
            style=${{ left: m.x + "px", width: m.w + "px" }}></div>`;
        })}
        ${overlays.lines.map(function (l) {
          return html`<div key=${l.key} className=${"overlay-workday " + l.kind} style=${{ left: l.x + "px" }}>
            <span className="tag">${l.label}</span></div>`;
        })}
        ${overlays.marks.map(function (m) {
          return html`<div key=${"p" + m.key} className="distraction-pin" title=${m.title}
            style=${{ left: (m.x - 4) + "px", top: "0px" }}></div>`;
        })}
        ${showNow ? html`<div style=${{ position: "absolute", left: nowX + "px", top: 0, bottom: 0, width: "0", borderLeft: "1px dashed " + ACCENT, opacity: 0.75, pointerEvents: "none", zIndex: 4 }}></div>` : null}
        ${cursor ? html`<div style=${{ position: "absolute", left: cursor.x + "px", top: 0, bottom: 0, width: "0", borderLeft: "1px dashed rgba(230,238,247,0.45)", pointerEvents: "none", zIndex: 6 }}></div>` : null}
        ${sel ? html`<div className="selbox" style=${{ left: sel.x0 + "px", width: (sel.x1 - sel.x0) + "px" }}></div>` : null}
        ${cursor && atCursor && (atCursor.att || atCursor.act)
          ? html`<${Tooltip} cursor=${cursor} at=${atCursor} d=${d} width=${width} />` : null}
      </div>

      <div className="minimap-wrap">
        <${Minimap} d=${d} view=${view} setView=${setView} clampView=${clampView} />
      </div>
    </section>`;
  }

  function Tooltip(props) {
    var cursor = props.cursor, at = props.at, d = props.d, width = props.width;
    var ref = useRef(null);
    var posState = useState({ left: cursor.x + 16, top: cursor.y - 12 });
    var pos = posState[0], setPos = posState[1];
    useLayoutEffect(function () {
      var node = ref.current; if (!node) return;
      var tw = node.offsetWidth, th = node.offsetHeight;
      var left = cursor.x + 16; if (left + tw > width) left = cursor.x - tw - 16;
      var top = cursor.y - th - 12; if (top < 0) top = cursor.y + 18;
      setPos({ left: Math.max(0, left), top: Math.max(0, top) });
    }, [cursor.x, cursor.y, width, at]);
    return html`<div className="tooltip" ref=${ref} style=${{ left: pos.left + "px", top: pos.top + "px" }}>
      <div className="tt-time">${clockS(cursor.min)}</div>
      ${at.att ? html`<div>
        <div className="tt-row"><${Swatch} color=${STATE_COLOR[at.att.seg.state]} />
          <span className="nm">${STATE_LABEL[at.att.seg.state]} ×${fmtWeight(at.att.seg.weight)}</span>
          <span className="val">${fmtDur(at.att.seg.duration_seconds)}</span></div>
        <div className="tt-sub">${at.att.seg.start_label.slice(0, 5)}–${at.att.seg.end_label.slice(0, 5)}${at.att.seg.annotation ? " · marked " + at.att.seg.annotation : at.att.seg.max_idle_seconds != null ? " · max idle " + fmtDur(at.att.seg.max_idle_seconds) : ""}</div>
      </div>` : null}
      ${at.act ? html`<div>
        <div className="tt-row"><${Swatch} color=${appColorOf(d, at.act.seg.process)} />
          <span className="nm">${at.act.seg.process}</span>
          <span className="val">${fmtDur(at.act.seg.duration_seconds)}</span></div>
        ${at.act.seg.title ? html`<div className="tt-title">${at.act.seg.title}</div>` : null}
      </div>` : null}
    </div>`;
  }

  function Minimap(props) {
    var d = props.d, view = props.view, setView = props.setView, clampView = props.clampView;
    var ref = useRef(null);
    var width = useWidth(ref);
    var dragRef = useRef(null);
    var dom = d.domain;
    var pw = Math.max(10, width - PAD.left - PAD.right);
    var base = MM.h - MM.bottom;
    function xf(min) { return PAD.left + (min - dom.start) / (dom.end - dom.start) * pw; }
    function minAt(px) { return dom.start + (px - PAD.left) / pw * (dom.end - dom.start); }
    function rel(ev) { return ev.clientX - ref.current.getBoundingClientRect().left; }

    function down(ev) {
      var m = clamp(minAt(rel(ev)), dom.start, dom.end);
      ref.current.setPointerCapture(ev.pointerId);
      if (m >= view.start && m <= view.end && (view.end - view.start) < (dom.end - dom.start)) {
        dragRef.current = { mode: "pan", anchor: m, view: { start: view.start, end: view.end } };
      } else {
        dragRef.current = { mode: "brush", anchor: m };
      }
    }
    function move(ev) {
      var drag = dragRef.current; if (!drag) return;
      var m = clamp(minAt(rel(ev)), dom.start, dom.end);
      if (drag.mode === "pan") {
        var delta = m - drag.anchor;
        setView(clampView({ start: drag.view.start + delta, end: drag.view.end + delta }));
      } else {
        setView(clampView({ start: Math.min(drag.anchor, m), end: Math.max(drag.anchor, m) }));
      }
    }
    function up(ev) { dragRef.current = null; try { ref.current.releasePointerCapture(ev.pointerId); } catch (e) {} }

    var bars = useMemo(function () {
      return d.attSegs.map(function (s, i) {
        var x0 = xf(s.start_min), x1 = xf(s.end_min);
        var h = 3 + s.weight * (base - MM.top - 3);
        return { key: i, x: x0, y: base - h, w: Math.max(0.5, x1 - x0), h: h, state: s.state };
      });
      // eslint-disable-next-line
    }, [d.attSegs, width, dom]);

    return html`<svg className="mm" ref=${ref} viewBox=${"0 0 " + width + " " + MM.h} height=${MM.h}
      onPointerDown=${down} onPointerMove=${move} onPointerUp=${up} onPointerCancel=${up}>
      <line x1=${PAD.left} x2=${width - PAD.right} y1=${base} y2=${base} stroke=${GRID} />
      ${bars.map(function (b) { return html`<rect key=${b.key} x=${b.x} y=${b.y} width=${b.w} height=${b.h} fill=${STATE_COLOR[b.state]} opacity="0.7" />`; })}
      ${d.sessions.map(function (s, i) {
        return html`<rect key=${"s" + i} x=${xf(s.start)} y=${base + 3} width=${Math.max(2, xf(s.end) - xf(s.start))} height="2.5" rx="1" fill=${ACCENT} opacity="0.8" />`;
      })}
      <rect className="mm-window" x=${xf(view.start)} y="1" width=${Math.max(2, xf(view.end) - xf(view.start))} height=${MM.h - 2} rx="3" />
    </svg>`;
  }

  function Sessions(props) {
    var d = props.d, onGo = props.onGo;
    if (!d.sessions.length) return html`<div className="muted-note">no deep-work session of ${SESSION_MIN} min or longer</div>`;
    var maxDur = Math.max.apply(null, d.sessions.map(function (s) { return s.end - s.start; }));
    return html`<div className="sessions">
      ${d.sessions.map(function (s, i) {
        var dom = null, best = 0;
        d.procRuns.forEach(function (r) { var o = overlap(s.start, s.end, r.start_min, r.end_min); if (o > best) { best = o; dom = r.process; } });
        return html`<button className="sess-row" key=${i} type="button" onClick=${function () { onGo(s); }}>
          <span className="range">${clock(s.start)} – ${clock(s.end)}</span>
          <span className="sess-bar"><i style=${{ width: ((s.end - s.start) / maxDur * 100) + "%" }}></i></span>
          <span className="figs"><b>${fmtDur((s.end - s.start) * 60)}</b><span> · eff ${fmtDur(s.eff)}</span></span>
          <span className="app">${dom ? html`<${Swatch} color=${appColorOf(d, dom)} /><span className="txt">${dom}</span>` : null}</span>
        </button>`;
      })}
    </div>`;
  }

  function RangePanels(props) {
    var d = props.d, view = props.view, setHoverApp = props.setHoverApp;
    var whole = Math.abs(view.start - d.domain.start) < 0.01 && Math.abs(view.end - d.domain.end) < 0.01;
    var rangeTxt = whole ? "// full day" : "// " + clock(view.start) + "–" + clock(view.end);

    var stateSec = useMemo(function () {
      var acc = {}; STATE_ORDER.forEach(function (s) { acc[s] = 0; });
      d.attSegs.forEach(function (s) { var o = overlap(s.start_min, s.end_min, view.start, view.end); if (o > 0) acc[s.state] += o * 60; });
      return acc;
    }, [d.attSegs, view]);

    var appStats = useMemo(function () {
      var acc = {};
      d.actSegs.forEach(function (s) {
        var o = overlap(s.start_min, s.end_min, view.start, view.end); if (o <= 0) return;
        var a = Math.max(s.start_min, view.start), b = Math.min(s.end_min, view.end);
        var st = acc[s.process] || (acc[s.process] = { process: s.process, raw: 0, eff: 0 });
        st.raw += o * 60;
        d.attSegs.forEach(function (at) {
          if (at.weight <= 0) return;
          var o2 = overlap(a, b, at.start_min, at.end_min);
          if (o2 > 0) st.eff += o2 * 60 * at.weight;
        });
      });
      return Object.keys(acc).map(function (k) { return acc[k]; })
        .filter(function (r) { return r.raw > 0; })
        .sort(function (x, y) { return y.raw - x.raw; }).slice(0, 8);
    }, [d.actSegs, d.attSegs, view]);

    var sum = STATE_ORDER.reduce(function (a, s) { return a + stateSec[s]; }, 0);
    var maxRaw = appStats.length ? appStats[0].raw : 1;

    return html`<div>
      <section className="panel">
        <header><h2>State Breakdown <span>${rangeTxt}</span></h2></header>
        ${sum <= 0 ? html`<div className="muted-note">no attention samples in this range</div>` : html`<div>
          <div className="stack">
            ${STATE_ORDER.filter(function (s) { return stateSec[s] > 0; }).map(function (s) {
              return html`<div key=${s} style=${{ flex: (stateSec[s] / sum) + " 0 0", background: STATE_COLOR[s], opacity: s === "unknown" ? 0.6 : 0.92 }} title=${STATE_LABEL[s] + " · " + fmtDur(stateSec[s])}></div>`;
            })}
          </div>
          <div className="breakdown-grid">
            ${STATE_ORDER.filter(function (s) { return stateSec[s] > 0; }).map(function (s) {
              return html`<div className="bd-item" key=${s}>
                <${Swatch} color=${STATE_COLOR[s]} />
                <span className="bd-name">${STATE_LABEL[s]}</span>
                <span className="bd-val">${fmtDur(stateSec[s])}</span>
                <span className="bd-pct">${Math.round(stateSec[s] / sum * 100)}%</span>
              </div>`;
            })}
          </div>
        </div>`}
      </section>

      <section className="panel">
        <header>
          <h2>Applications <span>${rangeTxt} · raw vs effective</span></h2>
          <div className="hint">bright = effective · faded = raw · hover to highlight its lane</div>
        </header>
        ${!appStats.length ? html`<div className="muted-note">no application activity in this range</div>` : html`<div className="rank">
          ${appStats.map(function (r) {
            var color = appColorOf(d, r.process);
            return html`<div className="rank-row" key=${r.process}
              onMouseEnter=${function () { setHoverApp(r.process); }}
              onMouseLeave=${function () { setHoverApp(null); }}>
              <div className="name" title=${r.process}>${r.process}</div>
              <div className="rank-bar">
                <div className="raw" style=${{ width: (r.raw / maxRaw * 100) + "%", background: color }}></div>
                <div className="eff" style=${{ width: (Math.min(r.eff, r.raw) / maxRaw * 100) + "%", background: color }}></div>
              </div>
              <div className="figs"><b>${fmtDur(r.eff)}</b><span> / ${fmtDur(r.raw)}</span></div>
            </div>`;
          })}
        </div>`}
      </section>
    </div>`;
  }

  function CategoryPanel(props) {
    var d = props.d, view = props.view;
    var scoped = useMemo(function () {
      var acc = {};
      (d.catSegs || []).forEach(function (s) {
        var o = overlap(s.start_min, s.end_min, view.start, view.end);
        if (o > 0) acc[s.category] = (acc[s.category] || 0) + o * 60;
      });
      return acc;
    }, [d.catSegs, view]);

    var entries = Object.keys(scoped).map(function (k) { return [k, scoped[k]]; })
      .filter(function (e) { return e[1] > 0; })
      .sort(function (a, b) { return b[1] - a[1]; });
    var sum = entries.reduce(function (a, e) { return a + e[1]; }, 0);
    var whole = Math.abs(view.start - d.domain.start) < 0.01 && Math.abs(view.end - d.domain.end) < 0.01;

    return html`<section className="panel">
      <header>
        <h2>Categories <span>${whole ? "// full day" : "// " + clock(view.start) + "–" + clock(view.end)}</span></h2>
        <div className="hint">what the time was actually spent on</div>
      </header>
      ${!entries.length
        ? html`<div className="muted-note">nothing classified in this range</div>`
        : html`<div>
            <div className="stack">
              ${entries.map(function (e) {
                return html`<div key=${e[0]} title=${(d.categoryLabels[e[0]] || e[0]) + " · " + fmtDur(e[1])}
                  style=${{ flex: (e[1] / sum) + " 0 0", background: CATEGORY_COLOR[e[0]] || CATEGORY_COLOR.unknown, opacity: 0.9 }}></div>`;
              })}
            </div>
            <div className="cat-legend">
              ${entries.map(function (e) {
                return html`<span className="item" key=${e[0]}>
                  <span className="swatch" style=${{ background: CATEGORY_COLOR[e[0]] || CATEGORY_COLOR.unknown }}></span>
                  ${d.categoryLabels[e[0]] || e[0]}
                  <span className="val">${fmtDur(e[1])} · ${Math.round(e[1] / sum * 100)}%</span>
                </span>`;
              })}
            </div>
          </div>`}
    </section>`;
  }

  function DeliverablePanel(props) {
    var block = props.d.deliverables;
    var attributed = block.attributed || [], stalled = block.stalled || [], tasks = block.open_tasks || [];
    if (!attributed.length && !stalled.length && !tasks.length) {
      return html`<section className="panel">
        <header><h2>Deliverables <span>// what actually moved</span></h2></header>
        <div className="muted-note">
          No deliverables yet. Create one so time can be attributed to real work:
          <br />focusledger deliverable add "Ship MVP" --keywords focusledger
        </div>
      </section>`;
    }
    var max = attributed.length ? attributed[0].seconds : 1;
    return html`<section className="panel">
      <header>
        <h2>Deliverables <span>// what actually moved</span></h2>
        <div className="hint">bar = time · figure = effective</div>
      </header>
      <div className="dl-list">
        ${attributed.map(function (item) {
          return html`<div className="dl-row" key=${item.id}>
            <div className="title">${item.title}</div>
            <div className="figs"><b>${fmtDur(item.effective_seconds)}</b><span> / ${fmtDur(item.seconds)}</span></div>
            <div className="bar"><i style=${{ width: (item.seconds / max * 100) + "%" }}></i></div>
            <div className="meta">
              ${item.due_date ? "due " + item.due_date + " · " : ""}priority ${item.priority}
            </div>
          </div>`;
        })}
        ${stalled.map(function (item) {
          return html`<div className="dl-row" key=${item.id}>
            <div className="title">${item.title}</div>
            <div className="figs"><span className="pill warn">no time today</span></div>
            ${item.due_date ? html`<div className="meta">due ${item.due_date}</div>` : null}
          </div>`;
        })}
      </div>
      ${tasks.length ? html`<div style=${{ marginTop: "12px" }}>
        <div className="hint" style=${{ marginBottom: "6px" }}>OPEN TASKS</div>
        ${tasks.map(function (task) {
          return html`<div className="meta" key=${task.id} style=${{ fontFamily: "var(--mono)", fontSize: "11px", color: "var(--ink-2)", padding: "2px 0" }}>
            [ ] ${task.title}${task.estimate_minutes ? "  ~" + task.estimate_minutes + "m" : ""}
            ${task.blocker ? html`<span className="pill warn" style=${{ marginLeft: "8px" }}>blocked</span>` : null}
          </div>`;
        })}
      </div>` : null}
    </section>`;
  }

  function ReviewPanel(props) {
    var review = props.d.review;
    if (!review) {
      return html`<section className="panel">
        <header><h2>Daily Review <span>// not generated yet</span></h2></header>
        <div className="muted-note">
          focusledger review write   →   focusledger review generate
        </div>
      </section>`;
    }
    var llmOk = review.llm_status === "ok";
    return html`<section className="panel">
      <header>
        <h2>Daily Review <span>// ${review.generator}</span></h2>
        <span className=${"pill " + (llmOk ? "good" : "")}>${llmOk ? "local model" : "deterministic"}</span>
      </header>
      <div className="review-md">${review.markdown}</div>
    </section>`;
  }

  function TableView(props) {
    var d = props.d;
    if (!d.attSegs.length) return null;
    return html`<details className="tableview">
      <summary>Attention segments — table view (${d.attSegs.length})</summary>
      <div className="tv-scroll">
        <table className="tv">
          <thead><tr><th>Start</th><th>End</th><th>State</th><th className="num">Duration</th><th className="num">Weight</th><th className="num">Max idle</th></tr></thead>
          <tbody>
            ${d.attSegs.map(function (s, i) {
              return html`<tr key=${i}>
                <td>${s.start_label}</td><td>${s.end_label}</td>
                <td><${Swatch} color=${STATE_COLOR[s.state]} />${STATE_LABEL[s.state]}</td>
                <td className="num">${fmtDurLong(s.duration_seconds)}</td>
                <td className="num">×${fmtWeight(s.weight)}</td>
                <td className="num">${s.max_idle_seconds == null ? "—" : fmtDurLong(s.max_idle_seconds)}</td>
              </tr>`;
            })}
          </tbody>
        </table>
      </div>
    </details>`;
  }

  // ---------- date navigation ----------
  function DateNav(props) {
    var days = props.days, date = props.date, setDate = props.setDate;
    var dates = days.dates.slice();
    if (dates.indexOf(days.today) < 0) dates.push(days.today);   // today is always selectable: it is live
    var i = dates.indexOf(date);
    var prev = i > 0 ? dates[i - 1] : null;
    var next = i >= 0 && i < dates.length - 1 ? dates[i + 1] : null;
    return html`<div className="date-nav">
      <button type="button" className="btn" disabled=${!prev} title="Previous tracked day"
        onClick=${function () { setDate(prev); }}>‹</button>
      <select className="date-select" value=${date} aria-label="Day"
        onChange=${function (ev) { setDate(ev.target.value); }}>
        ${dates.slice().reverse().map(function (d) {
          return html`<option key=${d} value=${d}>${d === days.today ? d + " · today" : d}</option>`;
        })}
      </select>
      <button type="button" className="btn" disabled=${!next} title="Next tracked day"
        onClick=${function () { setDate(next); }}>›</button>
      ${date !== days.today
        ? html`<button type="button" className="btn" onClick=${function () { setDate(days.today); }}>Today</button>`
        : null}
    </div>`;
  }

  // ---------- storage ----------
  function StoragePanel(props) {
    var s = props.storage;
    var total = s.file_bytes + s.wal_bytes;
    var pages = s.used_bytes + s.free_bytes;
    var span = function (first, last) { return first ? (first === last ? first : first + " → " + last) : "—"; };
    var rows = [
      ["Raw samples", fmtCount(s.raw.rows) + " rows",
       s.raw.days + " day(s) · " + span(s.raw.first_date, s.raw.last_date) + " · kept " + s.raw.retention_days + " days"],
      ["Archived days", s.archived.days + " day(s)",
       span(s.archived.first_date, s.archived.last_date) + " · " + fmtCount(s.archived.activity_segments) +
       " activity + " + fmtCount(s.archived.attention_segments) + " attention segments"],
      ["Heartbeats", fmtCount(s.heartbeats) + " rows", "liveness and missing-data detection"],
      ["Applications", fmtCount(s.applications) + " apps", "lookup table shared by every sample"]
    ];
    var freeShare = pages ? s.free_bytes / pages : 0;
    return html`<section className="panel storage">
      <header>
        <h2>Storage <span>// local SQLite · schema v${s.schema_version}</span></h2>
        <div className="hint" title=${s.path}>${s.path}</div>
      </header>
      <div className="storage-head">
        <div><div className="k">File</div><div className="big">${fmtBytes(total)}</div>
          <div className="foot">${s.wal_bytes ? "incl. " + fmtBytes(s.wal_bytes) + " write-ahead log" : "write-ahead log empty"}</div></div>
        <div><div className="k">In use</div><div className="big">${fmtBytes(s.used_bytes)}</div>
          <div className="foot">pages holding data</div></div>
        <div><div className="k">Free</div><div className="big">${fmtBytes(s.free_bytes)}</div>
          <div className="foot">freed but still in the file</div></div>
      </div>
      <div className="stack" title=${"in use " + fmtBytes(s.used_bytes) + " · free " + fmtBytes(s.free_bytes)}>
        <div style=${{ flex: (1 - freeShare) + " 0 0", background: "var(--accent)", opacity: 0.8 }}></div>
        ${freeShare > 0 ? html`<div style=${{ flex: freeShare + " 0 0", background: "var(--ink-3)", opacity: 0.5 }}></div>` : null}
      </div>
      <div className="storage-rows">
        ${rows.map(function (r) {
          return html`<div className="storage-row" key=${r[0]}>
            <span className="name">${r[0]}</span><span className="val">${r[1]}</span><span className="detail">${r[2]}</span>
          </div>`;
        })}
      </div>
      ${s.raw.pending_archive_days > 0 ? html`<div className="notice">
        ${s.raw.pending_archive_days} raw day(s) are past retention. Archiving turns the samples of each day into a
        few hundred segments and frees most of their space: focusledger db prune</div>` : null}
      ${freeShare > 0.05 ? html`<div className="notice">
        ${fmtBytes(s.free_bytes)} of the file is free space SQLite will reuse. To give it back to the disk now,
        stop the agent and run: focusledger db compact --yes</div>` : null}
      <div className="foot-line">Last archive run: ${s.last_archive_run_at ? s.last_archive_run_at.slice(0, 16).replace("T", " ") + " UTC" : "never"}</div>
    </section>`;
  }

  // ---------- analyst ----------
  var SUGGESTED_QUESTIONS = [
    "我有记录的最近 7 天，时间主要花在了哪些类别和应用上？",
    "一天里我通常哪几个小时最专注？",
    "把最近 7 个有记录的日子和再往前 7 个有记录的日子对比，游戏时间和有效时间有什么变化？",
    "我有记录的最后一天都做了什么？哪段时间最专注？"
  ];
  var INLINE_TOKEN = /(\*\*[^*]+\*\*|\x60[^\x60]+\x60|\b\d{4}-\d{2}-\d{2}\b)/g;
  function openDay(onDate, day) { return function () { onDate(day); }; }

  // Bold, code and dates inside one line of an answer. A tracked date becomes
  // a link that opens that day in the report above.
  function inlineMarkdown(text, ctx, key) {
    var out = [], last = 0, n = 0, m;
    INLINE_TOKEN.lastIndex = 0;
    while ((m = INLINE_TOKEN.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index));
      var tok = m[0], k = key + "-" + (n++);
      if (tok.slice(0, 2) === "**") out.push(html`<strong key=${k}>${tok.slice(2, -2)}</strong>`);
      else if (tok.charAt(0) === "\x60") out.push(html`<code key=${k}>${tok.slice(1, -1)}</code>`);
      else if (ctx.onDate && ctx.dates.indexOf(tok) >= 0) {
        out.push(html`<button key=${k} type="button" className="date-link" title="Open this day in the report"
          onClick=${openDay(ctx.onDate, tok)}>${tok}</button>`);
      } else out.push(tok);
      last = INLINE_TOKEN.lastIndex;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }

  // The few Markdown blocks local models actually produce. Built as React
  // elements, never as HTML strings, so an answer cannot inject markup.
  function Markdown(props) {
    var ctx = { onDate: props.onDate, dates: props.dates || [] };
    var lines = String(props.text || "").split(/\r?\n/);
    var isRow = function (l) { return /^\s*\|.*\|\s*$/.test(l); };
    var isItem = function (l) { return /^\s*([-*]|\d+\.)\s+/.test(l); };
    var isHead = function (l) { return /^\s*#{1,6}\s/.test(l); };
    var blocks = [], i = 0;
    while (i < lines.length) {
      var line = lines[i];
      if (!line.trim()) { i++; continue; }
      if (isRow(line)) {
        var rows = [];
        while (i < lines.length && isRow(lines[i])) { rows.push(lines[i]); i++; }
        var cells = rows.filter(function (r) { return !/^\s*\|[\s:|-]+\|\s*$/.test(r); })
          .map(function (r) { return r.trim().slice(1, -1).split("|").map(function (c) { return c.trim(); }); });
        blocks.push({ type: "table", head: cells[0] || [], body: cells.slice(1) });
      } else if (isHead(line)) {
        blocks.push({ type: "h", text: line.replace(/^\s*#+\s*/, "") }); i++;
      } else if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
        blocks.push({ type: "hr" }); i++;
      } else if (isItem(line)) {
        var ordered = /^\s*\d+\./.test(line), items = [];
        while (i < lines.length && isItem(lines[i])) { items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, "")); i++; }
        blocks.push({ type: ordered ? "ol" : "ul", items: items });
      } else {
        var para = [line]; i++;
        while (i < lines.length && lines[i].trim() && !isRow(lines[i]) && !isItem(lines[i]) && !isHead(lines[i])) {
          para.push(lines[i]); i++;
        }
        blocks.push({ type: "p", text: para.join("\n") });
      }
    }
    return html`<div className="answer">
      ${blocks.map(function (b, bi) {
        var k = "b" + bi;
        if (b.type === "h") return html`<h4 key=${k}>${inlineMarkdown(b.text, ctx, k)}</h4>`;
        if (b.type === "hr") return html`<hr key=${k} />`;
        if (b.type === "p") return html`<p key=${k}>${inlineMarkdown(b.text, ctx, k)}</p>`;
        if (b.type === "ul" || b.type === "ol") {
          var items = b.items.map(function (t, ii) { return html`<li key=${ii}>${inlineMarkdown(t, ctx, k + "-" + ii)}</li>`; });
          return b.type === "ol" ? html`<ol key=${k}>${items}</ol>` : html`<ul key=${k}>${items}</ul>`;
        }
        return html`<div className="answer-table" key=${k}><table>
          <thead><tr>${b.head.map(function (c, ci) { return html`<th key=${ci}>${inlineMarkdown(c, ctx, k + "h" + ci)}</th>`; })}</tr></thead>
          <tbody>${b.body.map(function (r, ri) {
            return html`<tr key=${ri}>${r.map(function (c, ci) { return html`<td key=${ci}>${inlineMarkdown(c, ctx, k + "r" + ri + "c" + ci)}</td>`; })}</tr>`;
          })}</tbody>
        </table></div>`;
      })}
    </div>`;
  }

  function ToolCallRow(props) {
    var c = props.call;
    var args = Object.keys(c.arguments || {}).map(function (k) { return k + "=" + c.arguments[k]; }).join(", ");
    var failed = !!(c.result && c.result.error);
    return html`<details className=${"tool-call" + (failed ? " failed" : "")}>
      <summary>
        <span className="tc-name">${c.name}</span><span className="tc-args">(${args})</span>
        <span className="tc-time">${failed ? "error · " : ""}${(c.seconds || 0).toFixed(1)}s</span>
      </summary>
      <pre className="tc-json">${JSON.stringify(c.result, null, 2)}</pre>
    </details>`;
  }

  // Small helpers for the JSON POSTs the analyst panel sends. The server only
  // accepts JSON bodies, which is what keeps other sites from posting here.
  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().then(function (payload) {
        if (!r.ok) throw new Error(payload.error || "HTTP " + r.status);
        return payload;
      });
    });
  }
  function readFlag(key) { try { return window.localStorage.getItem(key) === "1"; } catch (e) { return false; } }
  function writeFlag(key, on) { try { window.localStorage.setItem(key, on ? "1" : "0"); } catch (e) {} }

  function ProposalPreview(props) {
    var p = props.preview || {}, action = props.action;
    if (action === "mark_absent") {
      return html`<div className="pp">
        ${p.marked_hours} h marked on ${p.date} · ${p.stretches} stretch(es) ${p.from}–${p.to}
        ${p.effective_hours_before != null
          ? html`<div>effective time ${p.effective_hours_before} h → <b>${p.effective_hours_after} h</b></div>` : null}
        ${(p.app_hours_inside || []).length ? html`<div className="pp-apps">inside: ${p.app_hours_inside.map(function (a) {
          return a.app + " " + a.hours + " h"; }).join(" · ")}</div>` : null}
      </div>`;
    }
    if (action === "correct_category") {
      var recent = p.last_7_tracked_days || {};
      var cats = Object.keys(recent.current_categories || {}).map(function (k) {
        return k + " " + recent.current_categories[k] + " h"; }).join(" · ");
      return html`<div className="pp">
        ${recent.matching_hours || 0} h in the last 7 tracked days${cats ? " · now " + cats : ""}
        ${p.replaces_correction ? html`<div>replaces your earlier correction: ${p.replaces_correction}</div>` : null}
      </div>`;
    }
    return html`<pre className="tc-json">${JSON.stringify(p, null, 2)}</pre>`;
  }

  // A change the model proposed. Nothing is written until Approve; an applied
  // change can be undone from the same card. Its status lives in the panel,
  // so folding the run away and back does not lose it.
  function ProposalCard(props) {
    var proposal = props.proposal;
    var busyState = useState(false); var busy = busyState[0], setBusy = busyState[1];
    var errState = useState(null); var error = errState[0], setError = errState[1];

    function act(promise, after) {
      setBusy(true); setError(null);
      promise.then(function (payload) { after(payload); if (props.onChanged) props.onChanged(); })
        .catch(function (e) { setError(String(e.message || e)); })
        .finally(function () { setBusy(false); });
    }
    function decide(decision) {
      act(postJson("/api/analyst/proposals/" + proposal.id, { decision: decision }), props.onUpdate);
    }
    function undo() {
      act(postJson("/api/changes/" + proposal.change_id + "/undo", {}), function () {
        props.onUpdate(Object.assign({}, proposal, { status: "undone" }));
      });
    }
    var status = proposal.status;
    return html`<div className=${"proposal " + status}>
      <div className="pp-head">
        <span className=${"pill " + (status === "applied" ? "good" : "warn")}>${
          status === "pending" ? "needs your approval" : status === "applying" ? "applying" : status}</span>
        <span className="pp-summary">${proposal.summary}</span>
      </div>
      <${ProposalPreview} action=${proposal.action} preview=${proposal.preview} />
      <div className="pp-actions">
        ${status === "pending" ? html`<span>
          <button type="button" className="btn on" disabled=${busy} onClick=${function () { decide("approve"); }}>Approve</button>
          <button type="button" className="btn" disabled=${busy} onClick=${function () { decide("dismiss"); }}>Dismiss</button>
        </span>` : null}
        ${status === "applied" && proposal.change_id ? html`<button type="button" className="btn" disabled=${busy}
          onClick=${undo}>Undo</button>` : null}
        ${status === "undone" ? html`<span className="hint">undone · your data is back as it was</span>` : null}
        ${status === "dismissed" ? html`<span className="hint">dismissed · nothing was changed</span>` : null}
      </div>
      ${error ? html`<div className="error-banner">${error}</div>` : null}
    </div>`;
  }

  // Every change made on your behalf, kept in the database, so one can still
  // be undone after the page that approved it is gone.
  function ChangeLog(props) {
    var lState = useState(null); var list = lState[0], setList = lState[1];
    var busyState = useState(null); var busy = busyState[0], setBusy = busyState[1];
    var errState = useState(null); var error = errState[0], setError = errState[1];
    useEffect(function () {
      var alive = true;
      fetch("/api/changes", { cache: "no-store" })
        .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
        .then(function (payload) { if (alive) setList(payload.changes || []); })
        .catch(function () {});
      return function () { alive = false; };
    }, [props.refreshKey]);
    if (!list || !list.length) return null;

    function undo(id) {
      setBusy(id); setError(null);
      postJson("/api/changes/" + id + "/undo", {})
        .then(function () { props.onUndone(id); })
        .catch(function (e) { setError(String(e.message || e)); })
        .finally(function () { setBusy(null); });
    }
    var active = list.filter(function (c) { return !c.undone_at; }).length;
    return html`<details className="changes">
      <summary>Changes made for you · ${active} active${list.length > active ? " · " + (list.length - active) + " undone" : ""}</summary>
      ${list.map(function (c) {
        return html`<div key=${c.id} className=${"change-row" + (c.undone_at ? " undone" : "")}>
          <span className="when">${new Date(c.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
          <span className="what">${c.summary}</span>
          ${c.undone_at ? html`<span className="hint">undone</span>`
            : html`<button type="button" className="btn" disabled=${busy === c.id} onClick=${function () { undo(c.id); }}>Undo</button>`}
        </div>`;
      })}
      ${error ? html`<div className="error-banner">${error}</div>` : null}
    </details>`;
  }

  function Thinking(props) {
    var thinking = props.thinking || [];
    if (!thinking.length) return null;
    var chars = thinking.reduce(function (a, t) { return a + t.length; }, 0);
    return html`<details className="thinking" open=${props.running}>
      <summary>Thinking · ${thinking.length} step${thinking.length === 1 ? "" : "s"} · ${fmtCount(chars)} chars</summary>
      ${thinking.map(function (t, i) { return html`<pre key=${i} className="thought">${t}</pre>`; })}
    </details>`;
  }

  function AnalystRun(props) {
    var job = props.job;
    var running = job.status === "running";
    var cState = useState(!!props.collapsed); var collapsed = cState[0], setCollapsed = cState[1];
    // Fold an answer away once a newer one arrives; the user can reopen it.
    useEffect(function () { if (props.collapsed) setCollapsed(true); }, [props.collapsed]);
    var shown = running || !collapsed;
    return html`<article className=${"run" + (running ? " running" : "") + (shown ? "" : " collapsed")}>
      <button type="button" className="run-q" aria-expanded=${shown}
        onClick=${function () { if (!running) setCollapsed(!collapsed); }}>${job.question}</button>
      ${shown ? html`<div>
      <${Thinking} thinking=${job.thinking} running=${running} />
      ${(job.tool_calls || []).length ? html`<div className="run-tools">
        <div className="hint">DATA THE MODEL LOOKED AT · click to see exactly what it got</div>
        ${job.tool_calls.map(function (c, i) { return html`<${ToolCallRow} key=${i} call=${c} />`; })}
      </div>` : null}
      ${running ? html`<div className="run-wait"><span className="spinner"></span>
        ${job.model} is ${(job.tool_calls || []).length ? "working through the data" : "reading the question"} · ${Math.round(job.elapsed_seconds || 0)}s</div>` : null}
      ${job.status === "error" ? html`<div className="error-banner">${job.error}</div>` : null}
      ${job.status === "done" ? html`<div>
        <${Markdown} text=${job.answer || "(no answer)"} onDate=${props.onDate} dates=${props.dates} />
        ${(job.ungrounded_numbers || []).length ? html`<div className="warn-note">
          These numbers appear in no tool result, so the model may have computed or invented them:
          ${" " + job.ungrounded_numbers.join(", ")}</div>` : null}
        <div className="run-meta">
          ${job.model} · ${job.tool_mode} tool calls · ${(job.tool_calls || []).length} lookups · ${Math.round(job.elapsed_seconds || 0)}s
          ${(job.corrections || []).map(function (c, i) {
            return html`<span key=${i} className="pill warn" title=${c}>sent back once</span>`;
          })}
          ${job.stop_reason === "round_limit" ? html`<span className="pill warn">stopped at the round limit</span>` : null}
        </div>
      </div>` : null}
      ${(job.proposals || []).map(function (p) {
        return html`<${ProposalCard} key=${p.id} proposal=${p} onUpdate=${props.onProposal}
          onChanged=${props.onChanged} />`;
      })}
      </div>` : null}
    </article>`;
  }

  function AnalystPanel(props) {
    var mState = useState(null); var models = mState[0], setModels = mState[1];
    var selState = useState(""); var model = selState[0], setModel = selState[1];
    var qState = useState(""); var question = qState[0], setQuestion = qState[1];
    var jobState = useState(null); var job = jobState[0], setJob = jobState[1];
    var histState = useState([]); var history = histState[0], setHistory = histState[1];
    var errState = useState(null); var error = errState[0], setError = errState[1];

    useEffect(function () {
      fetch("/api/analyst/models", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (payload) {
          setModels(payload);
          var installed = payload.models || [];
          setModel(installed.indexOf(payload.default) >= 0 ? payload.default : (installed[0] || ""));
        })
        .catch(function (e) { setModels({ available: false, models: [], detail: String(e.message || e) }); });
    }, []);

    // Poll the running job; each snapshot re-arms the timer until it settles.
    useEffect(function () {
      if (!job || job.status !== "running") return;
      var alive = true;
      var timer = setTimeout(function () {
        fetch("/api/analyst/jobs/" + job.id, { cache: "no-store" })
          .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
          .then(function (snap) {
            if (!alive) return;
            if (snap.status === "running") { setJob(snap); return; }
            setJob(null);
            setHistory(function (h) { return [snap].concat(h).slice(0, 10); });
          })
          .catch(function (e) { if (alive) { setError(String(e.message || e)); setJob(null); } });
      }, 900);
      return function () { alive = false; clearTimeout(timer); };
    }, [job]);

    var foldState = useState(function () { return readFlag("fl.analyst.folded"); });
    var folded = foldState[0], setFolded = foldState[1];
    function toggleFold() { setFolded(!folded); writeFlag("fl.analyst.folded", !folded); }

    // A decided proposal replaces its copy in whichever run holds it.
    function patchProposals(match, fields) {
      function patch(run) {
        if (!run || !(run.proposals || []).some(match)) return run;
        return Object.assign({}, run, { proposals: run.proposals.map(function (p) {
          return match(p) ? Object.assign({}, p, fields) : p;
        }) });
      }
      setJob(patch);
      setHistory(function (h) { return h.map(patch); });
    }
    function updateProposal(updated) {
      patchProposals(function (p) { return p.id === updated.id; }, updated);
    }
    function changeUndone(changeId) {
      patchProposals(function (p) { return p.change_id === changeId; }, { status: "undone" });
      if (props.onChanged) props.onChanged();
    }

    var busy = !!job;
    function ask(text) {
      var q = String(text == null ? question : text).trim();
      if (!q || busy) return;
      setError(null);
      // Earlier answered turns, oldest first, so a follow-up such as "mark
      // that day" can be resolved. The server keeps the last four.
      var turns = history.filter(function (h) { return h.status === "done" && h.answer; })
        .slice(0, 4).reverse()
        .map(function (h) { return { question: h.question, answer: h.answer.slice(0, 6000) }; });
      postJson("/api/analyst/ask", { question: q, model: model || null, history: turns })
        .then(function (snapshot) { setJob(snapshot); setQuestion(""); })
        .catch(function (e) { setError(String(e.message || e)); });
    }

    var unavailable = models && !models.available;
    return html`<section className=${"panel analyst" + (folded ? " folded" : "")}>
      <header>
        <h2>
          <button type="button" className="fold-btn" aria-expanded=${!folded} onClick=${toggleFold}
            title=${folded ? "Show the analyst" : "Hide the analyst"}>${folded ? "+" : "-"}</button>
          Ask the Analyst <span>// local model · every number checked against your data · changes need your approval</span>
        </h2>
        ${!folded && models && models.models && models.models.length ? html`<div className="controls">
          ${history.length && !busy ? html`<button type="button" className="btn" title="Forget earlier questions"
            onClick=${function () { setHistory([]); setError(null); }}>New conversation</button>` : null}
          <span className="hint">MODEL</span>
          <select className="date-select" value=${model} disabled=${busy} aria-label="Model"
            onChange=${function (ev) { setModel(ev.target.value); }}>
            ${models.models.map(function (m) { return html`<option key=${m} value=${m}>${m}${m === models.default ? " · default" : ""}</option>`; })}
          </select>
        </div>` : null}
        ${folded && (busy || history.length) ? html`<span className="hint">${busy ? "working…" : history.length + " answer(s) hidden"}</span>` : null}
      </header>
      ${folded ? null : html`<div>
      ${unavailable ? html`<div className="warn-note">
        The local model is not reachable${models.detail ? " (" + models.detail + ")" : ""}.
        Start Ollama and pull a model, e.g. ollama pull ${models.default || "qwen3.5:9b"}.</div>` : null}
      <form className="ask-row" onSubmit=${function (ev) { ev.preventDefault(); ask(); }}>
        <textarea rows="2" value=${question} disabled=${busy || unavailable} maxLength="1000"
          placeholder=${history.length
            ? "Follow up; the model sees your last answers, e.g. 把那天的 unknown 标成睡觉  (Ctrl+Enter to send)"
            : "Ask about your tracked time, e.g. 上周我的时间主要花在哪里？  (Ctrl+Enter to send)"}
          onChange=${function (ev) { setQuestion(ev.target.value); }}
          onKeyDown=${function (ev) { if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); ask(); } }}></textarea>
        <button type="submit" className="btn on" disabled=${busy || unavailable || !question.trim()}>${busy ? "Working…" : "Ask"}</button>
      </form>
      ${!busy && !history.length ? html`<div className="suggest">
        ${SUGGESTED_QUESTIONS.map(function (q) {
          return html`<button key=${q} type="button" className="chip" disabled=${unavailable}
            onClick=${function () { ask(q); }}>${q}</button>`;
        })}
      </div>` : null}
      ${error ? html`<div className="error-banner">${error}</div>` : null}
      ${job ? html`<${AnalystRun} job=${job} onDate=${props.onDate} dates=${props.dates}
        onProposal=${updateProposal} onChanged=${props.onChanged} />` : null}
      ${history.map(function (h, i) {
        return html`<${AnalystRun} key=${h.id} job=${h} collapsed=${i > 0 || !!job}
          onDate=${props.onDate} dates=${props.dates} onProposal=${updateProposal} onChanged=${props.onChanged} />`;
      })}
      <${ChangeLog} refreshKey=${props.refreshKey} onUndone=${changeUndone} />
      </div>`}
    </section>`;
  }

  // ---------- app ----------
  function App() {
    var isStatic = useMemo(function () { return !!readInlineData(); }, []);
    var days = useDays(isStatic);
    // Bumped when an approved change rewrites how the data reads.
    var refreshState = useState(0); var refreshKey = refreshState[0], setRefreshKey = refreshState[1];
    var onDataChanged = useCallback(function () { setRefreshKey(function (k) { return k + 1; }); }, []);
    var storage = useStorage(isStatic, refreshKey);
    var storageOpen = useState(false); var showStorage = storageOpen[0], setShowStorage = storageOpen[1];
    var dateState = useState(null); var date = dateState[0], setDate = dateState[1];
    useEffect(function () { if (days && !date) setDate(days.initial); }, [days, date]);
    var rep = useReport(date, days && days.today, refreshKey);
    var data = rep.data;
    var d = useDerived(data);
    var viewState = useState(null); var view = viewState[0], setView = viewState[1];
    var isoState = useState(null); var isolated = isoState[0], setIsolated = isoState[1];
    var hoverState = useState(null); var hoverApp = hoverState[0], setHoverApp = hoverState[1];
    var followRef = useRef(true);
    var viewDateRef = useRef(null);

    // Keep zoom across live refreshes; follow the right edge if we were pinned
    // there. A different day starts again from its whole span.
    var domain = d && d.domain;
    var dataDate = data && data.meta && data.meta.date;
    useEffect(function () {
      if (!domain) return;
      var fresh = viewDateRef.current !== dataDate;
      viewDateRef.current = dataDate;
      if (fresh) followRef.current = true;
      setView(function (prev) {
        if (!prev || fresh) return { start: domain.start, end: domain.end };
        var sp = Math.min(prev.end - prev.start, domain.end - domain.start);
        if (followRef.current) return { start: Math.max(domain.start, domain.end - sp), end: domain.end };
        var s = clamp(prev.start, domain.start, domain.end - sp);
        return { start: s, end: s + sp };
      });
    }, [domain && domain.start, domain && domain.end, dataDate]);

    var openDate = useCallback(function (day) {
      setDate(day);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }, []);

    var setViewTracked = useCallback(function (v) {
      setView(function (prev) {
        var next = typeof v === "function" ? v(prev) : v;
        if (domain) followRef.current = (domain.end - next.end) < 1.5;
        return next;
      });
    }, [domain]);

    useEffect(function () {
      function onKey(ev) {
        if (!view || !domain) return;
        if (ev.target && /^(input|textarea|select)$/i.test(ev.target.tagName || "")) return;
        var sp = view.end - view.start;
        if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
          var delta = sp * 0.2 * (ev.key === "ArrowLeft" ? -1 : 1);
          setViewTracked({ start: clamp(view.start + delta, domain.start, domain.end - sp), end: clamp(view.start + delta, domain.start, domain.end - sp) + sp });
          ev.preventDefault();
        } else if (ev.key === "+" || ev.key === "=" || ev.key === "-" || ev.key === "_") {
          var f = (ev.key === "+" || ev.key === "=") ? 0.6 : 1 / 0.6;
          var c = (view.start + view.end) / 2;
          var ns = clamp(sp * f, MIN_SPAN, domain.end - domain.start);
          var s = clamp(c - ns / 2, domain.start, domain.end - ns);
          setViewTracked({ start: s, end: s + ns });
          ev.preventDefault();
        } else if (ev.key === "0" || ev.key === "Escape") {
          setViewTracked({ start: domain.start, end: domain.end });
        }
      }
      window.addEventListener("keydown", onKey);
      return function () { window.removeEventListener("keydown", onKey); };
    }, [view, domain, setViewTracked]);

    if (!data) {
      return html`<div className="console"><div className="empty">${rep.error ? "CONNECTION LOST — " + rep.error : "LOADING…"}</div></div>`;
    }

    var meta = data.meta || {};
    var agent = data.agent;
    var live = !rep.isStatic && meta.live !== false;
    var lastTracked = days && days.dates.length ? days.dates[days.dates.length - 1] : null;
    var switching = rep.loading;

    return html`<div className="console">
      <div className="masthead">
        <div className="brand">
          <div className="mark">FOCUS<b>LEDGER</b></div>
          <div className="sub">Attention Ledger</div>
          <${LivePill} agent=${agent} isStatic=${rep.isStatic} />
        </div>
        <div className="meta-grid">
          <div><div className="k">Date</div><div className="v">${days && date
            ? html`<${DateNav} days=${days} date=${date} setDate=${setDate} />` : meta.date}</div></div>
          <div><div className="k">Tracked</div><div className="v">${fmtDur(data.attention.raw_seconds)}</div></div>
          ${storage ? html`<div><div className="k">Database</div><div className="v">
            <button type="button" className=${"size-btn" + (showStorage ? " on" : "")}
              title="Show what the database holds" aria-expanded=${showStorage}
              onClick=${function () { setShowStorage(!showStorage); }}>${fmtBytes(storage.file_bytes + storage.wal_bytes)}</button>
          </div></div>` : null}
          <div><div className="k">Updated</div><div className="v">${rep.updatedAt ? new Date(rep.updatedAt).toLocaleTimeString() : "—"}</div></div>
        </div>
      </div>
      ${showStorage && storage ? html`<${StoragePanel} storage=${storage} />` : null}

      ${rep.error ? html`<div className="error-banner">Lost contact with the FocusLedger server (${rep.error}). Showing the last data received; retrying…</div>` : null}
      ${agent && agent.pending_archive_days > 0 && !showStorage ? html`<div className="notice">
        ${agent.pending_archive_days} day(s) of raw samples are past retention and not archived yet.
        The agent archives them when it starts, or run: focusledger db prune</div>` : null}

      ${!rep.isStatic ? html`<${AnalystPanel} onDate=${openDate} dates=${(days && days.dates) || []}
        refreshKey=${refreshKey} onChanged=${onDataChanged} />` : null}

      <div className=${switching ? "day is-loading" : "day"}>
      ${!d.hasData
        ? html`<div className="empty">
            NO SAMPLES FOR THIS DAY${live ? " YET" : ""}
            ${lastTracked && lastTracked !== meta.date ? html`<div style=${{ marginTop: "14px" }}>
              <button type="button" className="btn on" onClick=${function () { setDate(lastTracked); }}>
                Open the last tracked day · ${lastTracked}</button></div>` : null}
          </div>`
        : html`<div>
            <${StatTiles} data=${data} derived=${d} />
            ${view ? html`<${ChartPanel} data=${data} derived=${d} view=${view} setView=${setViewTracked}
              isolated=${isolated} setIsolated=${setIsolated} hoverApp=${hoverApp} live=${live} />` : null}
            <section className="panel">
              <header>
                <h2>Deep Work Sessions <span>// ≥${SESSION_MIN} min sustained · whole day</span></h2>
                <div className="hint">click a session to zoom to it</div>
              </header>
              <${Sessions} d=${d} onGo=${function (s) { setViewTracked({ start: s.start - 5, end: s.end + 5 }); }} />
            </section>
            ${view ? html`<${CategoryPanel} d=${d} view=${view} />` : null}
            <${DeliverablePanel} d=${d} />
            ${view ? html`<${RangePanels} d=${d} view=${view} setHoverApp=${setHoverApp} />` : null}
            <${ReviewPanel} d=${d} />
            <${TableView} d=${d} />
          </div>`}
      </div>

      <div className="foot-note">
        Derived at request time from raw samples · poll ${meta.poll_interval_seconds}s
        ${live ? " · live" : " · snapshot"}
      </div>
    </div>`;
  }

  ReactDOM.createRoot(document.getElementById("root")).render(html`<${App} />`);
})();
