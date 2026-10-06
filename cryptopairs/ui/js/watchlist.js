// Sidebar watchlist (ui/tv.py watchlist()): every pair with its position, Z and PnL from the
// bot's live marks. Open positions first, then the flat pairs nearest to an entry signal.
// A click sends the pair to Python ("pick"): it becomes the focus pair of every page and its Pair desk opens.

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

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  let el = parentElement.querySelector(".wl");
  if (!el) {
    el = document.createElement("div");
    el.className = "wl";
    parentElement.appendChild(el);
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
      el.querySelectorAll(".wl-row.on").forEach((x) => x.classList.remove("on"));
      const row = [...el.querySelectorAll(".wl-row")].find((x) => x.dataset.id === d.id);
      if (row) row.classList.add("on");  // instant feedback; the page follows after the rerun
      const name = row && row.querySelector(".wl-name");
      cpWlLoading(d.id, name ? name.textContent.replace(/[❚✎⟳⚑]/g, "").replace("/", " / ") : d.id);
      cpWlSwitchNow(d.id);
      el._pending = d.id;
      el._tries = 0;
      el._sent = 0;
      el._since = Date.now();
      cpWlFlush(el);
      cpWlCloseSidebar();
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
  const shown = el._pending || data.focus;  // a click waiting to be sent keeps its highlight
  const sig = JSON.stringify([data, shown]);
  if (sig === el._sig) return;  // the framework redraws far more often than the data changes
  el._sig = sig;
  const rows = data.rows || [];
  const open = rows.filter((r) => r.side), flat = rows.filter((r) => !r.side);
  const head = (t, n, right) => `<div class="wl-h"><span>${t} · ${n}</span><span>${right}</span></div>`;
  el.innerHTML = head("Open positions", open.length, "uPnL") + (open.map((r) => cpWlRow(r, shown)).join("")
      || '<div class="wl-empty">none</div>')
    + head("Watching", flat.length, "PnL") + flat.map((r) => cpWlRow(r, shown)).join("")
    + '<div class="wl-foot">Z bar: |Z| vs the entry threshold (tick) · amber = within 0.25 of a signal · '
    + '❚❚ paused · ✎ override · ⚑ exit plan · ⟳ executing</div>';
}
