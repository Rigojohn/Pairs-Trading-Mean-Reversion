// Sidebar watchlist (ui/tv.py watchlist()): every pair with its position, Z and PnL from the
// bot's live marks. Open positions first, then the flat pairs nearest to an entry signal.
// A click sends the pair to Python ("pick"): it becomes the focus pair of every page and its Pair desk opens.
// The search box above the list filters it in the browser as you type ("/" jumps to it, ↑/↓ move, Enter opens,
// Esc clears); the text is kept for the browser tab, so it survives a page switch.

function cpWlRow(r, focus) {
  const side = r.side > 0 ? '<span class="wl-side long">L</span>' : r.side < 0 ? '<span class="wl-side short">S</span>'
    : '<span class="wl-side flat">·</span>';
  const z = isFinite(r.z) ? (r.z >= 0 ? "+" : "") + r.z.toFixed(2) : "—";
  const near = r.side ? "" : !isFinite(r.to_entry) ? "" : r.to_entry <= 0 ? "sig" : r.to_entry <= 0.25 ? "near" : "";
  const fill = isFinite(r.z) && r.ze > 0 ? Math.min(100, 100 * Math.abs(r.z) / (1.25 * r.ze)) : 0;
  const mark = r.ze > 0 ? 100 / 1.25 : 0;
  const pnl = r.side ? r.upnl : r.pnl;
  const pcls = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "";
  const flags = [r.paused ? "❚❚" : "", r.override ? "✎" : "", r.busy ? "⟳" : "", r.plan ? "⚑" : ""].join("");
  return `<div class="wl-row ${r.id === focus ? "on" : ""}" data-id="${r.id}" title="${r.id}${r.status ? " · " + r.status : ""}">`
    + `${side}<span class="wl-name">${r.a}<i>/</i>${r.b}<em>${flags}</em></span>`
    + `<span class="wl-z ${near}">${z}<b class="wl-bar"><i style="width:${fill.toFixed(0)}%"></i><u style="left:${mark}%"></u></b></span>`
    + `<span class="wl-pnl ${pcls}">${isFinite(pnl) ? (pnl >= 0 ? "+" : "") + pnl.toFixed(2) : "—"}</span></div>`;
}

// The click goes out at once through the relay component (js/relay.js), which sits outside the fragment that
// redraws this list: its trigger interrupts a running page instead of being dropped. If the desk of that pair
// is still not on screen 12 s later the click is sent again (at most 3 times).
function cpWlDelivered(el) {
  return el._focus === el._pending && /\/pair\/?$/.test(location.pathname);
}

function cpWlFlush(el) {
  if (!el._pending) return;
  if (cpWlDelivered(el) || el._tries >= 3 || Date.now() - el._since > 60000) { el._pending = null; return; }
  if (el._sent && Date.now() - el._sent < 12000) return;  // give the last one time to land
  el._tries += 1;
  el._sent = Date.now();
  (window.__cpPick || el._send.bind(null, "pick"))(el._pending);
}

// A click shows at once that it was taken: a thin bar and the pair's name at the top, and the old desk dimmed
// (its numbers belong to the previous pair). Both go when the new pair's desk is drawn (its order book is the
// last part of the desk), or after 20 s whatever happens.
function cpWlLoading(id, label) {
  let bar = document.getElementById("cp-loading");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "cp-loading";
    bar.innerHTML = "<i></i><span></span>";
    document.body.appendChild(bar);
  }
  bar.querySelector("span").textContent = "Loading " + label + "…";
  bar.dataset.id = id;
  bar.dataset.since = String(Date.now());
  bar.classList.add("on");
  // On the Pair desk the live panels switch at once (cpWlSwitchNow): only the rest is dimmed
  document.body.classList.add(/\/pair\/?$/.test(location.pathname) ? "cp-switching-desk" : "cp-switching");
  if (!window.__cpLoadingTimer) {
    window.__cpLoadingTimer = setInterval(() => {
      const b = document.getElementById("cp-loading");
      const done = !b || !b.classList.contains("on")
        || document.querySelector(".st-key-pd_book_" + CSS.escape(b.dataset.id))
        || Date.now() - Number(b.dataset.since) > 20000;
      if (!done) return;
      if (b && b.classList.contains("on") && !document.querySelector(".st-key-pd_book_" + CSS.escape(b.dataset.id))) {
        window.dispatchEvent(new CustomEvent("cp-pair-cancel"));
      }
      if (b) b.classList.remove("on");
      document.body.classList.remove("cp-switching", "cp-switching-desk");
      clearInterval(window.__cpLoadingTimer);
      window.__cpLoadingTimer = null;
    }, 80);
  }
}

// The Pair desk's live panels and its title switch in the browser at once (js/market.js listens for
// "cp-pair"); the bot's numbers and the chart follow with the page run.
function cpWlSwitchNow(id) {
  const md = (window.__cpMd || {})[id];
  if (!md || !/\/pair\/?$/.test(location.pathname)) return;
  window.dispatchEvent(new CustomEvent("cp-pair", { detail: md }));
  const t = document.querySelector('[data-testid="stMain"] .desk-title');
  if (t) t.textContent = md.label_a + " / " + md.label_b;
}

// On a phone the sidebar is an overlay that covers the page: close it so the opened desk is visible.
function cpWlCloseSidebar() {
  if (window.innerWidth > 768) return;
  const b = document.querySelector('[data-testid="stSidebarCollapseButton"] button');
  if (b) b.click();
}

// The search text: every word must be found in one of the two coins ("ogn", "ogn ct", "band/sk").
const CP_WL_Q = "cp-wl-q";

function cpWlLoadQ() {
  try { return sessionStorage.getItem(CP_WL_Q) || ""; } catch (e) { return ""; }
}

function cpWlSaveQ(q) {
  try { if (q) sessionStorage.setItem(CP_WL_Q, q); else sessionStorage.removeItem(CP_WL_Q); } catch (e) { /* private mode */ }
}

function cpWlMatch(r, words) {
  const a = String(r.a).toUpperCase(), b = String(r.b).toUpperCase();
  return words.every((w) => a.includes(w) || b.includes(w));
}

function cpWlWords(q) {
  return q.toUpperCase().split(/[\s/_,-]+/).filter(Boolean);
}

// A pair picked by a tap or by Enter in the search box.
function cpWlPick(el, id) {
  el.querySelectorAll(".wl-row.on").forEach((x) => x.classList.remove("on"));
  const row = [...el.querySelectorAll(".wl-row")].find((x) => x.dataset.id === id);
  if (row) row.classList.add("on");  // instant feedback; the page follows after the rerun
  const name = row && row.querySelector(".wl-name");
  cpWlLoading(id, name ? name.textContent.replace(/[❚✎⟳⚑]/g, "").replace("/", " / ") : id);
  cpWlSwitchNow(id);
  el._pending = id;
  el._tries = 0;
  el._sent = 0;
  el._since = Date.now();
  cpWlFlush(el);
  cpWlCloseSidebar();
}

function cpWlRender(el) {
  const data = el._data || {};
  const shown = el._pending || data.focus;  // a click waiting to be sent keeps its highlight
  const words = cpWlWords(el._q || "");
  const all = data.rows || [];
  const rows = words.length ? all.filter((r) => cpWlMatch(r, words)) : all;
  el._shown = rows.map((r) => r.id);
  if (!el._shown.includes(el._kb)) el._kb = null;
  const sig = JSON.stringify([data, shown, words, el._kb]);
  if (sig === el._sig) return;  // the framework redraws far more often than the data changes
  el._sig = sig;
  const row = (r) => cpWlRow(r, shown).replace('class="wl-row ', `class="wl-row ${r.id === el._kb ? "kb " : ""}`);
  const open = rows.filter((r) => r.side), flat = rows.filter((r) => !r.side);
  const n = (part, whole) => words.length ? `${part.length} of ${whole.length}` : `${part.length}`;
  const head = (t, part, whole, right) => `<div class="wl-h"><span>${t} · ${n(part, whole)}</span><span>${right}</span></div>`;
  let html;
  if (words.length && !rows.length) {
    html = '<div class="wl-none">No pair matches this search</div>';
  } else {
    html = (words.length && !open.length ? "" : head("Open positions", open, all.filter((r) => r.side), "uPnL")
        + (open.map(row).join("") || '<div class="wl-empty">none</div>'))
      + (words.length && !flat.length ? "" : head("Watching", flat, all.filter((r) => !r.side), "PnL")
        + flat.map(row).join(""));
  }
  el._list.innerHTML = html
    + '<div class="wl-foot">Z bar: |Z| vs the entry threshold (tick) · amber = within 0.25 of a signal · '
    + '❚❚ auto entries paused · ✎ override · ⚑ exit plan · ⟳ executing</div>';
}

// The search box: kept across the list's redraws (it is not part of the redrawn HTML), so typing is never lost.
function cpWlSearch(el) {
  const box = document.createElement("div");
  box.className = "wl-q";
  box.innerHTML = '<input type="text" inputmode="search" enterkeyhint="go" placeholder="Search pairs" autocomplete="off" '
    + 'autocapitalize="characters" spellcheck="false" aria-label="Search pairs"><kbd title="press / to search">/</kbd><button type="button" title="Clear (Esc)">×</button>';
  const input = box.querySelector("input"), clear = box.querySelector("button");
  const set = (q) => {
    el._q = q;
    el._kb = null;
    box.classList.toggle("has", !!q);
    cpWlSaveQ(q);
    cpWlRender(el);
  };
  input.value = el._q;
  box.classList.toggle("has", !!el._q);
  input.addEventListener("input", () => set(input.value));
  input.addEventListener("keydown", (ev) => {
    const ids = el._shown || [];
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      if (!ids.length) return;
      ev.preventDefault();
      const i = ids.indexOf(el._kb);
      el._kb = ids[ev.key === "ArrowDown" ? (i + 1) % ids.length : i <= 0 ? ids.length - 1 : i - 1];
      cpWlRender(el);
      const r = el._list.querySelector(".wl-row.kb");
      if (r) r.scrollIntoView({ block: "nearest" });
    } else if (ev.key === "Enter") {
      ev.preventDefault();
      const id = el._kb || ids[0];
      if (!id) return;
      if (window.matchMedia("(pointer: coarse)").matches) input.blur();  // close the phone's keyboard
      cpWlPick(el, id);
    } else if (ev.key === "Escape") {
      ev.preventDefault();
      if (input.value) { input.value = ""; set(""); } else input.blur();
    }
  });
  clear.addEventListener("click", () => { input.value = ""; set(""); input.focus(); });
  return box;
}

// "/" anywhere on the page (not while typing in a field) jumps to the search box, if the sidebar is open.
function cpWlHotkey() {
  if (window.__cpWlHotkey) return;
  window.__cpWlHotkey = true;
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "/" || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const t = ev.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    const input = document.querySelector(".wl-q input");
    if (!input) return;
    const box = input.getBoundingClientRect();
    if (box.width === 0 || box.right <= 0) return;  // sidebar closed
    ev.preventDefault();
    input.focus();
    input.select();
  });
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  let el = parentElement.querySelector(".wl");
  if (el && !el._list) { el.remove(); el = null; }  // drawn by an older version of this script
  if (!el) {
    el = document.createElement("div");
    el.className = "wl";
    el._q = cpWlLoadQ();
    el.appendChild(cpWlSearch(el));
    el._list = document.createElement("div");
    el.appendChild(el._list);
    parentElement.appendChild(el);
    cpWlHotkey();
    // The rows are redrawn about once a second (live Z): a finger that touches a row and lifts 100 ms later
    // would find it replaced and no click would fire. So the row is remembered on pointerdown and the tap
    // is taken on pointerup, whatever the DOM looks like by then (a swipe, to scroll the list, is not a tap).
    el.addEventListener("pointerdown", (ev) => {
      const row = ev.target.closest && ev.target.closest(".wl-row");
      el._down = row ? { id: row.dataset.id, x: ev.clientX, y: ev.clientY } : null;
    });
    // on the document: when the touched row was replaced, the pointerup no longer reaches the list
    const up = (ev) => {
      if (!el.isConnected) { document.removeEventListener("pointerup", up, true); return; }
      const d = el._down;
      el._down = null;
      if (!d || Math.abs(ev.clientX - d.x) > 10 || Math.abs(ev.clientY - d.y) > 10) return;
      cpWlPick(el, d.id);
    };
    document.addEventListener("pointerup", up, true);
    // A click anywhere else (a timeframe, a tab) cancels a pick still waiting to be re-sent: a late re-send
    // would switch the pair under the user's hands.
    const elsewhere = (ev) => {
      if (!el.isConnected) { document.removeEventListener("pointerdown", elsewhere, true); return; }
      if (!el.contains(ev.target)) el._pending = null;
    };
    document.addEventListener("pointerdown", elsewhere, true);
    el.addEventListener("pointercancel", () => { el._down = null; });
    el._timer = setInterval(() => {
      if (!el.isConnected) { clearInterval(el._timer); return; }  // a rebuilt sidebar has its own list
      cpWlFlush(el);
    }, 400);
  }
  el._send = setTriggerValue;  // the latest render's callback
  el._renders = (el._renders || 0) + 1;
  el._focus = data.focus;
  if (el._pending && cpWlDelivered(el)) el._pending = null;
  el._data = data;
  cpWlRender(el);
}
