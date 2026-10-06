// Live market panels for one pair, straight from Binance in the browser (ui/tv.py market()).
//
// view "ticker": live Z (executable, under the fit in use), unrealized PnL at live mids, the
//                feed's health. Indicative: the bot decides on its own quotes at the hourly close.
// view "book":   10-level order book per leg with depth bars and cumulative USDT, spread,
//                depth within ±10 bps, mark / index price and funding with a countdown.
// view "tape":   the latest trades of both legs.
// The panels share one connection (cpHub) with the charts on the page.

function cpPx(p, digits) { return cpFmt(p, digits); }

function cpFeed(hub) {
  const s = hub.status();
  const lag = hub.lag === null ? "" : ` · ${Math.max(0, hub.lag)} ms`;
  if (s === "live") return `<span class="cp-dot ok"></span>Binance live${lag} · ${hub.rate} msg/s`;
  if (s === "partial") return `<span class="cp-dot warn"></span>Binance partly connected${lag}`;
  if (s === "silent") return `<span class="cp-dot warn"></span>Binance connected, no updates for ${((Date.now() - hub.lastMsg) / 1000).toFixed(0)} s`;
  if (s === "idle") return `<span class="cp-dot"></span>not connected`;
  return `<span class="cp-dot bad"></span>Binance ${s}… (the bot's own 5 s marks are still shown elsewhere)`;
}

function cpTicker(el, d, hub) {
  const z = hub.z(d.a, d.b, d.fit);
  const P = d.position;
  let pnl = "";
  if (P && P.side && z) {
    const eq = P.cash + P.qty_a * (z.mid_a - P.entry_a) + P.qty_b * (z.mid_b - P.entry_b);
    const u = eq - P.cash;
    const pct = P.entry_equity ? 100 * u / P.entry_equity : NaN;
    const cls = u > 0 ? "pos" : u < 0 ? "neg" : "";
    pnl = `<div class="cp-tk"><span class="k">uPnL live</span><b class="${cls}">${u >= 0 ? "+" : ""}${cpFmt(u, 4)}</b>`
        + `<span class="${cls}">${isFinite(pct) ? (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%" : ""}</span></div>`;
  }
  let zs = `<div class="cp-tk"><span class="k">Z live</span><b>—</b></div>`;
  if (z) {
    // Flat: hot at an entry signal, warm near one. In a position the entry threshold means nothing.
    const ze = d.z_entry, flat = !(P && P.side);
    const hot = !flat ? "" : Math.abs(z.z) >= ze ? "hot" : Math.abs(z.z) >= ze - 0.25 ? "warm" : "";
    zs = `<div class="cp-tk"><span class="k">Z live</span><b class="cp-z ${hot}">${z.z >= 0 ? "+" : ""}${z.z.toFixed(2)}</b>`
       + `<span class="sub">buy ${z.zb.toFixed(2)} · sell ${z.zs.toFixed(2)}</span></div>`;
  }
  const qa = hub.state.bbo[d.a], qb = hub.state.bbo[d.b];
  const legs = [[d.label_a, qa, d.digits_a], [d.label_b, qb, d.digits_b]].map(([n, q, dg]) => q
    ? `<div class="cp-tk"><span class="k">${n}</span><b>${cpPx((q.bid + q.ask) / 2, dg)}</b>`
      + `<span class="sub">${(1e4 * (q.ask - q.bid) / ((q.bid + q.ask) / 2)).toFixed(1)} bps</span></div>`
    : `<div class="cp-tk"><span class="k">${n}</span><b>—</b></div>`).join("");
  el.innerHTML = `<div class="cp-ticker">${zs}${pnl}${legs}<div class="cp-feed">${cpFeed(hub)}<br>`
    + `<span class="sub">indicative · the bot decides at the hourly close</span></div></div>`;
}

function cpLadder(sym, label, digits, book, bbo, mark, ownSide) {
  if (!book) return `<div class="cp-book"><div class="cp-book-h">${label}</div><div class="cp-empty">waiting for the book…</div></div>`;
  const N = 10;
  const asks = book.asks.slice(0, N).map(([p, q]) => [+p, +q]);
  const bids = book.bids.slice(0, N).map(([p, q]) => [+p, +q]);
  let cum = 0;
  const askCum = asks.map(([p, q]) => (cum += p * q));
  cum = 0;
  const bidCum = bids.map(([p, q]) => (cum += p * q));
  const maxCum = Math.max(askCum[askCum.length - 1] || 0, bidCum[bidCum.length - 1] || 0, 1);
  const bestBid = bids.length ? bids[0][0] : NaN, bestAsk = asks.length ? asks[0][0] : NaN;
  const mid = (bestBid + bestAsk) / 2;
  const within = (side, lim) => side.reduce((s, [p, q]) => (Math.abs(p / mid - 1) <= lim ? s + p * q : s), 0);
  const row = (p, q, c, cls) => `<tr class="${cls}"><td class="px">${cpPx(p, digits)}</td><td>${cpCompact(q)}</td>`
    + `<td>${cpCompact(c)}</td><td class="bar"><i style="width:${(100 * c / maxCum).toFixed(1)}%"></i></td></tr>`;
  const askRows = asks.map(([p, q], i) => row(p, q, askCum[i], "ask")).reverse().join("");
  const bidRows = bids.map(([p, q], i) => row(p, q, bidCum[i], "bid")).join("");
  const spr = 1e4 * (bestAsk - bestBid) / mid;
  let fund = "";
  if (mark) {
    const left = Math.max(0, mark.next - Date.now());
    const hh = String(Math.floor(left / 3.6e6)).padStart(2, "0"), mm = String(Math.floor(left / 6e4) % 60).padStart(2, "0"),
      ss = String(Math.floor(left / 1000) % 60).padStart(2, "0");
    const r = 100 * mark.rate;
    const pays = ownSide && r !== 0 ? ownSide * r > 0 : null; // longs pay a positive rate, shorts receive it
    const own = pays === null ? "" : ` · <span class="${pays ? "neg" : "pos"}">${pays ? "you pay" : "you receive"}</span>`;
    fund = `<div class="cp-fund"><span>mark <b>${cpPx(mark.mark, digits)}</b> · index ${cpPx(mark.index, digits)}</span>`
      + `<span>funding <b>${r >= 0 ? "+" : ""}${r.toFixed(4)}%</b> in ${hh}:${mm}:${ss}${own}</span></div>`;
  }
  const pos = ownSide ? `<span class="cp-own ${ownSide > 0 ? "long" : "short"}">${ownSide > 0 ? "LONG" : "SHORT"} leg</span>` : "";
  return `<div class="cp-book"><div class="cp-book-h">${label} ${pos}<span class="sub">${sym}</span></div>`
    + `<table><thead><tr><th>price</th><th>size</th><th>Σ USDT</th><th></th></tr></thead><tbody>${askRows}`
    + `<tr class="mid"><td class="px">${cpPx(mid, digits + 1)}</td><td colspan="3">spread ${isFinite(spr) ? spr.toFixed(1) : "—"} bps`
    + ` · ±10 bps: ${cpCompact(within(bids, 0.001))} / ${cpCompact(within(asks, 0.001))}</td></tr>${bidRows}</tbody></table>${fund}</div>`;
}

function cpBooks(el, d, hub) {
  const st = hub.state;
  const P = d.position || {};
  el.innerHTML = `<div class="cp-feedline">${cpFeed(hub)}</div><div class="cp-books">`
    + cpLadder(d.a, d.label_a, d.digits_a, st.book[d.a], st.bbo[d.a], st.mark[d.a], P.side ? Math.sign(P.qty_a) : 0)
    + cpLadder(d.b, d.label_b, d.digits_b, st.book[d.b], st.bbo[d.b], st.mark[d.b], P.side ? Math.sign(P.qty_b) : 0)
    + `</div>`;
}

function cpTape(el, d, hub) {
  const rows = hub.state.trades.filter((t) => t.s === d.a || t.s === d.b).slice(0, d.rows || 24).map((t) => {
    const tm = cpTimeFmt(d.tz).clock(t.T);
    const dg = t.s === d.a ? d.digits_a : d.digits_b;
    const lab = t.s === d.a ? d.label_a : d.label_b;
    return `<tr class="${t.sell ? "ask" : "bid"}"><td>${tm}</td><td>${lab}</td><td class="px">${cpPx(t.p, dg)}</td>`
      + `<td>${cpCompact(t.q)}</td><td>${cpCompact(t.p * t.q)}</td></tr>`;
  }).join("");
  el.innerHTML = `<div class="cp-tape"><table><thead><tr><th>time</th><th>leg</th><th>price</th><th>size</th>`
    + `<th>USDT</th></tr></thead><tbody>${rows || '<tr><td colspan="5" class="cp-empty">waiting for trades…</td></tr>'}</tbody></table></div>`;
}

const CP_VIEWS = { ticker: cpTicker, book: cpBooks, tape: cpTape };

// A click on another pair in the watchlist (event "cp-pair", js/watchlist.js) switches the panel to that
// pair at once, with the data the click carries; the page run then mounts the new pair's own panel. Until
// then a redraw of this (old) panel from Python keeps the clicked pair. "cp-pair-cancel" goes back.
function cpMkShow(st) {
  const d = st.override ? { ...st.data, ...st.override } : st.data;
  const view = CP_VIEWS[d.view] || cpTicker;
  st.draw = (hub) => view(st.el, d, hub);
  const key = JSON.stringify([d.a, d.b]);
  if (key !== st.key) {
    st.key = key;
    cpHub().subscribe(st.token, [d.a, d.b], [], (hub) => st.draw(hub));
  }
  st.draw(cpHub());
}

export default function (component) {
  const { data, parentElement } = component;
  let st = parentElement.__cpMk;
  if (!st) {
    st = parentElement.__cpMk = { token: Math.random().toString(36).slice(2), el: document.createElement("div") };
    st.el.className = "cp-mk";
    parentElement.appendChild(st.el);
    st.tick = setInterval(() => st.draw && st.draw(cpHub()), 1000); // countdowns and staleness
    st.onPair = (ev) => { st.override = ev.detail; cpMkShow(st); };
    st.onCancel = () => { st.override = null; cpMkShow(st); };
    window.addEventListener("cp-pair", st.onPair);
    window.addEventListener("cp-pair-cancel", st.onCancel);
  }
  st.data = data;
  if (st.override && st.override.pid === data.pid) st.override = null;  // Python caught up
  cpMkShow(st);
  return () => {
    clearInterval(st.tick);
    window.removeEventListener("cp-pair", st.onPair);
    window.removeEventListener("cp-pair-cancel", st.onCancel);
    cpHub().unsubscribe(st.token);
    delete parentElement.__cpMk;
  };
}
