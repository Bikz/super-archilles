// Presenter card tray. Injected into index.html when /features includes "cards".
// Listens to the "copilot" window events that index.html re-dispatches from /events.
(() => {
  const tray = document.getElementById("cardTray");
  if (!tray) return;

  const MAX_TILES = 4;
  let cards = new Map(); // id -> card
  let stageId = null;
  const tiles = new Map(); // id -> { el, sig }
  const seen = new Map(); // id -> local time first seen, so stale drafts stop counting
  const DRAFT_MS = 120000;
  const put = (c) => { if (!seen.has(c.id)) seen.set(c.id, Date.now()); cards.set(c.id, c); };

  // ---- static skeleton
  tray.className = "ct";
  const head = el("div", "ct-head");
  const label = el("div", "ct-label", "Cards for the screen");
  const crew = el("div", "ct-crew");
  const clearBtn = el("button", "ghost ct-clear", "Clear screen");
  clearBtn.type = "button";
  clearBtn.onclick = () => post("/stage/clear", clearBtn);
  head.append(label, crew, clearBtn);
  const list = el("div", "ct-list");
  const more = el("div", "ct-more");
  tray.append(head, list, more);

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  async function post(url, btn) {
    if (btn) btn.disabled = true;
    try { await fetch(url, { method: "POST" }); } catch {}
    if (btn) setTimeout(() => { btn.disabled = false; }, 400);
  }

  async function backfill() {
    try {
      const [cr, sr] = await Promise.all([fetch("/cards"), fetch("/stage/current")]);
      if (cr.ok) {
        cards = new Map();
        for (const c of await cr.json()) put(c);
      }
      if (sr.ok) {
        const s = await sr.json();
        stageId = s.card ? s.card.id : null;
        if (s.card) cards.set(s.card.id, { ...cards.get(s.card.id), ...s.card });
      }
    } catch {}
    render();
  }

  window.addEventListener("copilot", (e) => {
    const ev = e.detail || {};
    if (ev.type === "start") {
      cards = new Map(); stageId = null; render(); backfill();
    } else if (ev.type === "card") {
      const { type, ...c } = ev;
      put(c); render();
    } else if (ev.type === "stats") {
      const before = crew.textContent;
      updateCrew();
      if (crew.textContent !== before) render();
    } else if (ev.type === "stage") {
      stageId = ev.card ? ev.card.id : null;
      if (ev.card) cards.set(ev.card.id, ev.card);
      render();
    }
  });

  // ---- thumbnails
  const fitters = new ResizeObserver((entries) => {
    for (const en of entries) fitFrame(en.target);
  });
  function fitFrame(wrap) {
    const f = wrap.querySelector("iframe");
    if (f) f.style.transform = `scale(${wrap.clientWidth / 1920})`;
  }

  function frameDoc(html) {
    return "<!doctype html><html><head><meta charset=\"utf-8\"><style>html,body{margin:0;width:1920px;height:1080px;" +
      "overflow:hidden;background:#0b0d10;color:#e8eaed;font-family:ui-sans-serif,system-ui,sans-serif}</style></head><body>" +
      html + "</body></html>";
  }

  function thumb(c) {
    const wrap = el("div", "ct-thumb");
    if (c.html) {
      const f = document.createElement("iframe");
      f.setAttribute("sandbox", "");
      f.setAttribute("tabindex", "-1");
      f.setAttribute("aria-hidden", "true");
      f.setAttribute("loading", "lazy");
      f.srcdoc = frameDoc(c.html);
      wrap.append(f);
      fitters.observe(wrap);
      requestAnimationFrame(() => fitFrame(wrap));
    } else if (c.image) {
      const img = document.createElement("img");
      img.src = c.image; img.alt = ""; img.loading = "lazy";
      wrap.append(img);
    } else {
      wrap.classList.add("ct-text");
      wrap.append(el("div", "ct-kind", c.kind === "qa" ? "Q and A" : (c.kind || "note")));
      wrap.append(el("div", "ct-tbody", c.body || c.title || ""));
    }
    return wrap;
  }

  function sig(c) {
    return [c.status, c.id === stageId, c.title, c.reason, (c.html || "").length, c.image, c.body].join("|");
  }

  function tile(c) {
    const onStage = c.id === stageId;
    const t = el("article", "ct-tile" + (onStage ? " on" : ""));
    t.append(thumb(c));
    const meta = el("div", "ct-meta");
    const top = el("div", "ct-top");
    if (onStage) top.append(el("span", "ct-badge", "On screen"));
    top.append(el("span", "ct-src", [c.kind, c.source].filter(Boolean).join(" from ")));
    meta.append(top, el("div", "ct-title", c.title || "Untitled"));
    if (c.reason) meta.append(el("div", "ct-reason", c.reason));
    const acts = el("div", "ct-acts");
    if (onStage) {
      const b = el("button", "ghost", "Clear screen"); b.type = "button";
      b.onclick = () => post("/stage/clear", b);
      acts.append(b);
    } else {
      const show = el("button", "ct-show", "Show"); show.type = "button";
      show.onclick = () => post(`/cards/${encodeURIComponent(c.id)}/show`, show);
      acts.append(show);
    }
    const dis = el("button", "ghost", "Dismiss"); dis.type = "button";
    dis.onclick = async () => {
      if (onStage) await post("/stage/clear");
      post(`/cards/${encodeURIComponent(c.id)}/dismiss`, dis);
    };
    acts.append(dis);
    meta.append(acts);
    t.append(meta);
    return t;
  }

  function updateCrew() {
    const now = Date.now();
    let n = 0;
    for (const c of cards.values()) if (c.status === "candidate" && now - (seen.get(c.id) || 0) < DRAFT_MS) n++;
    const txt = n ? `${n} ${n === 1 ? "draft" : "drafts"} in progress` : "";
    if (crew.textContent !== txt) crew.textContent = txt;
    return n;
  }

  function render() {
    const all = [...cards.values()];
    const newest = (a, b) => (b.created || b.at_s || 0) - (a.created || a.at_s || 0);
    const staged = stageId ? cards.get(stageId) : null;
    const proposed = all.filter((c) => c.status === "proposed" && c.id !== stageId).sort(newest);
    const show = (staged ? [staged] : []).concat(proposed).slice(0, MAX_TILES);
    const drafts = updateCrew();
    clearBtn.classList.toggle("hidden", !stageId || !!staged);
    const extra = proposed.length + (staged ? 1 : 0) - show.length;
    more.textContent = extra > 0 ? `+${extra} more ready` : "";
    label.textContent = show.length ? "Ready for the screen" : "Cards for the screen";
    tray.classList.toggle("ct-empty", !show.length && !drafts);

    const keep = new Set(show.map((c) => c.id));
    for (const [id, t] of tiles) {
      if (!keep.has(id)) { t.el.remove(); tiles.delete(id); }
    }
    show.forEach((c, i) => {
      const s = sig(c);
      let t = tiles.get(c.id);
      if (!t || t.sig !== s) {
        const fresh = tile(c);
        if (t) t.el.replaceWith(fresh);
        t = { el: fresh, sig: s };
        tiles.set(c.id, t);
      }
      if (list.children[i] !== t.el) list.insertBefore(t.el, list.children[i] || null);
    });
  }

  render();
  backfill();
})();
