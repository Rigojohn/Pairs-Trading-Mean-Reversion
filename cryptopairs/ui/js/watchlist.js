// Sidebar watchlist (ui/tv.py watchlist()): every pair with its position, Z and PnL from the
// bot's live marks. Open positions first, then the flat pairs nearest to an entry signal.
// A click sends the pair to Python ("pick"), which makes it the focus pair of every page.

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

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  let el = parentElement.querySelector(".wl");
  if (!el) {
    el = document.createElement("div");
    el.className = "wl";
    parentElement.appendChild(el);
    el.addEventListener("click", (ev) => {
      const row = ev.target.closest(".wl-row");
      if (!row) return;
      el.querySelectorAll(".wl-row.on").forEach((x) => x.classList.remove("on"));
      row.classList.add("on");  // instant feedback; the page follows after the rerun
      setTriggerValue("pick", row.dataset.id);
    });
  }
  const rows = data.rows || [];
  const open = rows.filter((r) => r.side), flat = rows.filter((r) => !r.side);
  const head = (t, n, right) => `<div class="wl-h"><span>${t} · ${n}</span><span>${right}</span></div>`;
  el.innerHTML = head("Open positions", open.length, "uPnL") + (open.map((r) => cpWlRow(r, data.focus)).join("")
      || '<div class="wl-empty">none</div>')
    + head("Watching", flat.length, "PnL") + flat.map((r) => cpWlRow(r, data.focus)).join("")
    + '<div class="wl-foot">Z bar: |Z| vs the entry threshold (tick) · amber = within 0.25 of a signal · '
    + '❚❚ paused · ✎ override · ⚑ exit plan · ⟳ executing</div>';
}
