// Topic coverage heatmap. Injected into index.html when /features includes "coverage".
// One row per plan section, one cell per point, colored by how deeply it has been covered
// (scores come from the coverage worker via POST /coverage -> "coverage" events).
(() => {
  const root = document.getElementById("coverage");
  if (!root) return;

  const LABELS = ["Not yet", "Mentioned", "Explained", "Landed"];
  let cvPlan = null;
  let cvStart = 0; // performance.now()/1000 when the talk started
  let running = false;
  let scores = new Map(); // "s:p" -> { score, evidence }
  let summaryText = "";
  let cells = new Map(); // "s:p" -> { el, sec, text, si, pi }
  let selected = null; // pinned key (tap/click)
  let hovered = null;

  const fmt = (s) => { s = Math.max(0, Math.floor(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  const elapsed = () => (cvStart ? performance.now() / 1000 - cvStart : 0);

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // ---- skeleton
  root.className = "cv cv-empty";
  const head = el("div", "cv-head");
  const label = el("div", "cv-label", "Topic coverage");
  const tally = el("div", "cv-tally");
  head.append(label, tally);
  const summary = el("div", "cv-summary");
  const grid = el("div", "cv-grid");
  const detail = el("div", "cv-detail");
  const legend = el("div", "cv-legend");
  LABELS.forEach((t, i) => {
    const k = el("span", "cv-key");
    k.append(el("i", `cv-sw cv-s${i}`), document.createTextNode(t));
    legend.append(k);
  });
  root.append(head, summary, grid, detail, legend);

  // ---- plan -> rows and cells (built once per talk; updates only touch classes so colors animate)
  function build() {
    grid.textContent = "";
    cells = new Map();
    selected = hovered = null;
    const secs = (cvPlan && cvPlan.sections) || [];
    root.classList.toggle("cv-empty", !secs.length);
    secs.forEach((s, si) => {
      const row = el("div", "cv-row" + (s.must ? " must" : ""));
      const meta = el("div", "cv-meta");
      const title = el("div", "cv-title", s.title);
      const sub = el("div", "cv-sub");
      if (s.must) sub.append(el("span", "cv-must", "must"));
      sub.append(document.createTextNode(`${s.minutes} min, by ${fmt(s.end_s)}`));
      meta.append(title, sub);
      const strip = el("div", "cv-cells");
      const pts = s.points && s.points.length ? s.points.map((p, pi) => [pi, p]) : [[-1, s.title]];
      for (const [pi, text] of pts) {
        const key = `${si}:${pi}`;
        const c = el("button", "cv-cell cv-s0");
        c.type = "button";
        c.append(el("span", "cv-ctext", text));
        c.addEventListener("mouseenter", () => { hovered = key; showDetail(); });
        c.addEventListener("mouseleave", () => { if (hovered === key) { hovered = null; showDetail(); } });
        c.addEventListener("click", () => { selected = selected === key ? null : key; showDetail(); paint(); });
        strip.append(c);
        cells.set(key, { el: c, sec: s, text, si, pi });
      }
      row.append(meta, strip);
      grid.append(row);
    });
    paint();
  }

  function paint() {
    const now = elapsed();
    let done = 0;
    for (const [key, c] of cells) {
      const v = scores.get(key) || { score: 0, evidence: "" };
      const sc = Math.max(0, Math.min(3, v.score | 0));
      if (sc >= 2) done++;
      // Uncovered must points: amber once their section's planned time has started, red once it has ended.
      const warn = running && c.sec.must && sc === 0 ? (now >= c.sec.end_s ? "late" : now >= c.sec.start_s ? "due" : "") : "";
      const cls = `cv-cell cv-s${sc}` + (warn ? ` cv-${warn}` : "") + (selected === key ? " sel" : "");
      if (c.el.className !== cls) {
        if (c.score != null && sc > c.score) { c.el.classList.remove("cv-pop"); void c.el.offsetWidth; }
        c.el.className = cls + (c.score != null && sc > c.score ? " cv-pop" : "");
      }
      c.score = sc;
      const tip = `${c.sec.title}: ${c.text}\n${LABELS[sc]}` + (v.evidence ? `: "${v.evidence}"` : "");
      if (c.el.title !== tip) c.el.title = tip;
      c.el.setAttribute("aria-label", tip);
    }
    tally.textContent = cells.size ? `${done} of ${cells.size} covered` : "";
    summary.textContent = summaryText;
    summary.className = "cv-summary" + (/^(Not yet|Thin)/.test(summaryText) ? " gap" : summaryText ? " ok" : "");
    showDetail();
  }

  function showDetail() {
    const key = hovered || selected;
    const c = key && cells.get(key);
    detail.textContent = "";
    if (!c) { detail.classList.add("idle"); detail.textContent = "Tap a cell to see the point and what was said."; return; }
    detail.classList.remove("idle");
    const v = scores.get(key) || { score: 0, evidence: "" };
    const sc = Math.max(0, Math.min(3, v.score | 0));
    const top = el("div", "cv-dtop");
    top.append(el("span", `cv-dbadge cv-s${sc}`, LABELS[sc]), el("span", "cv-dsec", c.sec.title));
    detail.append(top, el("div", "cv-dtext", c.text));
    if (v.evidence) detail.append(el("div", "cv-dev", `Heard: "${v.evidence}"`));
    else if (sc === 0 && c.sec.must) detail.append(el("div", "cv-dev", `Must cover by ${fmt(c.sec.end_s)}`));
  }

  function apply(ev) {
    for (const p of ev.points || []) {
      const key = `${p.section}:${p.point}`;
      const prev = scores.get(key);
      if (!prev || (p.score | 0) >= prev.score) scores.set(key, { score: p.score | 0, evidence: p.evidence || "" });
    }
    if (ev.summary != null) summaryText = ev.summary;
    paint();
  }

  async function backfill() {
    try {
      const r = await fetch("/coverage");
      if (r.ok) apply(await r.json());
    } catch {}
  }

  function startTalk(p, el0) {
    cvPlan = p; cvStart = performance.now() / 1000 - (el0 || 0); running = true;
    scores = new Map(); summaryText = "";
    build();
    backfill(); // empty on a brand new talk; restores state on a websocket reconnect replay
  }

  window.addEventListener("copilot", (e) => {
    const ev = e.detail || {};
    if (ev.type === "start") startTalk(ev.plan, ev.elapsed);
    else if (ev.type === "coverage") apply(ev);
    else if (ev.type === "stop") { running = false; paint(); }
  });

  // This module usually loads after the first "start" event: pick up the page's plan and clock.
  try {
    // eslint-disable-next-line no-undef
    if (typeof plan !== "undefined" && plan && plan.sections) startTalk(plan, typeof t0 === "number" ? performance.now() / 1000 - t0 : 0);
  } catch {}
  if (!cvPlan) { build(); backfill(); }

  setInterval(() => { if (running && cells.size) paint(); }, 2000);
})();
