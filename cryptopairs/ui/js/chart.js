// A Lightweight Charts chart that updates in place (ui/tv.py, specs built by ui/tvcharts.py).
//
// Streamlit calls this function on every rerun with the new spec. The chart is created once
// per element; each series is kept and only receives setData when its data hash changes, so a
// page refreshing every 2 s costs almost nothing and the user's zoom stays. A new ``rev``
// (another pair, timeframe or window) resets the visible range.
// Series with a ``live`` binding are also updated from the Binance stream in the browser:
//   z       mid executable Z from both legs' best bid/ask under the fit in use
//   spread  log A - alpha - beta log B at the mids (x scale: 100 for %)
//   kline   the forming candle; rebased: 100 x close / base; volume: the candle's quote volume
// Older history on demand (spec.older, the Pair desk's Spread Z): when the left edge comes within 50 bars
// of the earliest point, the chart asks Python for the bars before it ("older" trigger) and puts the piece
// (spec.older_chunk) in front of what it has. Bars that leave the recent window as time passes are kept
// too, so nothing older than the window is ever lost while the chart stays on the same rev.

function cpChartOptions(spec, tz) {
  const tf = cpTimeFmt(tz);
  const room = spec.right_px ? { rightOffsetPixels: spec.right_px } : {};  // free pixels after the last point
  return {
    autoSize: true,
    layout: {
      background: { type: "solid", color: "rgba(0,0,0,0)" }, textColor: "#8b93a5", fontSize: 11,
      fontFamily: "Inter, 'Segoe UI', Roboto, sans-serif", attributionLogo: true,
      panes: { separatorColor: "#232b3a", separatorHoverColor: "rgba(240,180,41,0.25)", enableResize: true },
    },
    grid: { vertLines: { color: "#151b27" }, horzLines: { color: "#151b27" } },
    rightPriceScale: { borderColor: "#232b3a", scaleMargins: { top: 0.08, bottom: 0.08 } },
    // minBarSpacing: zooming out can reach days of 1-minute bars (back to an open position's entry).
    timeScale: { borderColor: "#232b3a", timeVisible: true, secondsVisible: !!spec.seconds, rightOffset: 6,
                 shiftVisibleRangeOnNewBar: true, tickMarkFormatter: tf.tick, minBarSpacing: 0.05, ...room },
    crosshair: {
      mode: 0,
      vertLine: { color: "#4b5468", labelBackgroundColor: "#232b3a" },
      horzLine: { color: "#4b5468", labelBackgroundColor: "#232b3a" },
    },
    localization: { timeFormatter: tf.crosshair },
  };
}

function cpRows(s) {
  const d = s.data || {};
  const t = d.t || [];
  const out = new Array(t.length);
  if (s.type === "Candlestick") {
    for (let i = 0; i < t.length; i++) out[i] = { time: t[i], open: d.o[i], high: d.h[i], low: d.l[i], close: d.c[i] };
  } else {
    for (let i = 0; i < t.length; i++) {
      const v = d.v[i];
      out[i] = v === null || v === undefined ? { time: t[i] } : { time: t[i], value: v };
      if (d.c && d.c[i] && out[i].value !== undefined) out[i].color = d.c[i];
    }
  }
  return out;
}

function cpSeriesOptions(s, st) {
  const o = Object.assign({}, s.options || {});
  if (s.marker_room) o.autoscaleInfoProvider = (orig) => cpMarkerRoom(st, s.id, orig());
  if (s.role === "guide") o.autoscaleInfoProvider = () => null; // drawn, but never stretches the price scale
  if (s.include && s.include.length) { // levels drawn as price lines stay in view
    const inc = s.include;
    o.autoscaleInfoProvider = (orig) => {
      const r = orig();
      if (!r || !r.priceRange) return r;
      r.priceRange.minValue = Math.min(r.priceRange.minValue, ...inc);
      r.priceRange.maxValue = Math.max(r.priceRange.maxValue, ...inc);
      return r;
    };
  }
  return o;
}

function cpBucket(ms, tf, label) {
  const sec = Math.floor(ms / 1000);
  const start = Math.floor(sec / tf) * tf;
  return label === "end" ? start + tf : start;
}

function cpApplyLive(st, hub) {
  for (const e of st.series.values()) {
    const L = e.live;
    if (!L) continue;
    let point = null;
    if (L.kind === "z" || L.kind === "spread") {
      const z = hub.z(L.a, L.b, L.fit);
      if (z) point = { time: cpBucket(z.ts, L.tf, L.label), value: L.kind === "z" ? z.z : z.u * (L.scale || 1) };
    } else {
      const k = ((hub.state.kline[L.symbol] || {})[L.interval]);
      if (!k) continue;
      const time = Math.floor(k.t / 1000);
      if (L.kind === "kline") point = { time, open: k.o, high: k.h, low: k.l, close: k.c };
      else if (L.kind === "rebased") point = { time, value: 100 * k.c / L.base };
      else if (L.kind === "volume") point = { time, value: k.q, color: k.c >= k.o ? L.up : L.down };
    }
    if (!point || (e.lastTime !== null && point.time < e.lastTime)) continue;
    try {
      e.api.update(point);
      e.lastTime = point.time;
      e.liveValue = point.value !== undefined ? point.value : point.close;
    } catch (err) { /* out-of-order tick: ignore */ }
  }
  cpLegend(st);
}

function cpLegend(st, param) {
  if (!st.legend) return;
  const parts = [];
  for (const [id, e] of st.series) {
    if (!e.title) continue;
    let v = null;
    if (param && param.seriesData && param.seriesData.get(e.api)) {
      const p = param.seriesData.get(e.api);
      v = p.value !== undefined ? p.value : p.close;
    } else {
      v = e.liveValue !== undefined && e.liveValue !== null ? e.liveValue : e.lastValue;
    }
    const txt = v === null || v === undefined ? "—" : Number(v).toFixed(e.digits);
    parts.push(`<span class="cp-leg"><i style="background:${e.color}"></i>${e.title} <b>${txt}</b></span>`);
  }
  st.legend.innerHTML = parts.join("");
}

function cpLastValue(s) {
  const d = s.data || {};
  const arr = s.type === "Candlestick" ? d.c : d.v;
  if (!arr) return null;
  for (let i = arr.length - 1; i >= 0; i--) if (arr[i] !== null && arr[i] !== undefined) return arr[i];
  return null;
}

// Markers stacked on one bar ("marker_room" series: the NAV's closed trades, several pairs often closing in the
// same bar, at a high): the price scale leaves room for the tallest stack in view, above and below the line,
// or the upper ones fell outside the pane. Heights as the library stacks them (text + shape + margin).
function cpMarkerRoom(st, sid, r) {
  const e = st.series.get(sid), ts = st.chart && st.chart.timeScale(), vr = ts && ts.getVisibleRange();
  if (!r || !e || !e.markList || !vr) return r;
  const bs = Math.min(Math.max(ts.options().barSpacing, 12), 30);
  const up = new Map(), down = new Map();
  for (const m of e.markList) {
    const g = m.position === "aboveBar" ? up : m.position === "belowBar" ? down : null;
    if (!g || m.time < vr.from || m.time > vr.to) continue;
    const shape = bs * (m.size || 1);
    g.set(m.time, (g.get(m.time) || 0) + 1.2 * 11 + shape + Math.max(0.1 * shape, 3));
  }
  const most = (g) => (g.size ? Math.max(...g.values()) + 6 : 0);
  let above = most(up), below = most(down);
  const pane = st.chart.panes()[e.pane], cap = 0.45 * (pane ? pane.getHeight() : 0);  // the line keeps most of it
  if (cap && above + below > cap) { const k = cap / (above + below); above *= k; below *= k; }
  r.margins = { above: Math.max(above, (r.margins || {}).above || 0), below: Math.max(below, (r.margins || {}).below || 0) };
  return r;
}

// ------------------------------------------------------------- position boxes
// A series' ``boxes`` (ui/tvcharts.position_boxes): one box per trade behind it, from the bar of its entry
// marker to the bar of its exit marker (the open one to the latest point), with a thin edge at the entry
// and at the exit. Each is drawn on its own, so trades next to each other stay apart on every timeframe,
// and a trade within a single bar still shows. A bar the chart lacks counts as the next one it has.
const CP_BOX_MIN_PX = 3;

class CpBoxes {
  constructor() {
    this.items = [];
    this.first = null;
    this.rects = [];
    this.view = { zOrder: () => "bottom", renderer: () => this };
  }
  attached(p) { this.chart = p.chart; this.redraw = p.requestUpdate; }
  detached() { this.chart = null; this.redraw = null; }
  set(items, first) {  // first: the earliest bar loaded; a box that ends before it is not there yet
    this.items = items;
    this.first = first;
    if (this.redraw) this.redraw();
  }
  paneViews() { return [this.view]; }
  updateAllViews() {
    this.rects = [];
    if (!this.chart || this.first === null || !this.items.length) return;
    const ts = this.chart.timeScale();
    const x = (t) => {
      const i = ts.timeToIndex(t, true);
      return i === null || i === undefined ? null : ts.logicalToCoordinate(i);
    };
    const a = ts.logicalToCoordinate(0), b = ts.logicalToCoordinate(1);
    if (a === null || b === null) return;
    const minW = Math.max(CP_BOX_MIN_PX, 0.5 * (b - a));
    for (const it of this.items) {
      if (it.to !== null && it.to < this.first) continue;
      let l = x(Math.max(it.from, this.first)), r = x(it.to === null ? 4e9 : it.to);
      if (l === null || r === null) continue;
      if (r - l < minW) { const c = (l + r) / 2; l = c - minW / 2; r = c + minW / 2; }
      this.rects.push({ l, r, fill: it.fill, edge: it.edge, inEdge: it.from >= this.first, outEdge: it.to !== null });
    }
  }
  draw(target) {
    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: k }) => {
      const h = bitmapSize.height, w = Math.max(1, Math.floor(k));
      for (const q of this.rects) {
        const l = Math.round(q.l * k), r = Math.round(q.r * k);
        ctx.fillStyle = q.fill;
        ctx.fillRect(l, 0, Math.max(1, r - l), h);
        ctx.fillStyle = q.edge;
        if (q.inEdge) ctx.fillRect(l - (w >> 1), 0, w, h);
        if (q.outEdge) ctx.fillRect(r - (w >> 1), 0, w, h);
      }
    });
  }
}

// ------------------------------------------------------------- older history
const CP_OLDER_EDGE = 50;    // bars from the left edge that ask for more
const CP_OLDER_RETRY = 1500; // ms without an answer before asking again (a fragment's trigger can be dropped;
                             // an answer takes ~1 s, and a second ask for the same piece is harmless)

function cpOlderNew(rev) {
  return { rev, base: new Map(), prev: new Map(), mBase: new Map(), mPrev: new Map(), got: new Set(),
           done: false, pending: null, first: null };
}

// The rows of one series: everything kept from before the recent window, then the recent window.
function cpOlderRows(O, sid, recent, chunk) {
  let base = O.base.get(sid) || [];
  const first = recent.length ? recent[0].time : Infinity;
  const prev = O.prev.get(sid);
  if (prev) {  // bars that left the recent window since the last draw
    const last = base.length ? base[base.length - 1].time : -Infinity;
    const gone = prev.filter((r) => r.time > last && r.time < first);
    if (gone.length) base = base.concat(gone);
  }
  if (base.length && base[base.length - 1].time >= first) base = base.filter((r) => r.time < first);
  if (chunk && chunk.length) {
    const earliest = base.length ? base[0].time : first;
    const older = chunk.filter((r) => r.time < earliest);
    if (older.length) base = older.concat(base);
  }
  O.base.set(sid, base);
  O.prev.set(sid, recent);
  return base.length ? base.concat(recent) : recent;
}

function cpOlderMarkers(O, sid, recent, first, chunk) {
  const key = (m) => m.time + "|" + m.text;
  const seen = new Set(), out = [];
  const add = (m) => { const k = key(m); if (!seen.has(k)) { seen.add(k); out.push(m); } };
  (O.mBase.get(sid) || []).forEach(add);
  (O.mPrev.get(sid) || []).filter((m) => m.time < first).forEach(add);
  (chunk || []).forEach(add);
  const base = out.filter((m) => m.time < first).sort((a, b) => a.time - b.time);
  O.mBase.set(sid, base);
  O.mPrev.set(sid, recent);
  const all = base.slice();
  recent.forEach((m) => { if (!seen.has(key(m))) all.push(m); });
  return all.sort((a, b) => a.time - b.time);
}

function cpOlderLabel(st, text) {
  if (st.olderEl && st.olderEl.textContent !== text) st.olderEl.textContent = text;
}

function cpOlderEarliest(st) {
  const e = st.series.get("z") || [...st.series.values()][0];
  const d = e ? e.api.data() : [];
  return d.length ? d[0].time : null;
}

function cpOlderSend(st) {
  const O = st.older, P = O.pending;
  P.sent = Date.now();
  P.tries += 1;
  st.send("older", { rev: O.rev, before: P.before, n: P.sent });
  cpOlderLabel(st, P.waiting ? "waiting for Binance…" : "loading older…");
}

function cpOlderCheck(st) {
  const O = st.older;
  if (!O || !st.chart || !st.send || st.rendering) return;  // mid-draw the range is not the final one yet
  const r = st.chart.timeScale().getVisibleLogicalRange();
  const near = r && r.from <= CP_OLDER_EDGE;
  if (O.done) {
    cpOlderLabel(st, near && O.first ? "start of history · " + new Date(O.first * 1000).toLocaleDateString(
      "en-GB", { timeZone: st.tz || undefined, day: "2-digit", month: "2-digit", year: "numeric" }) : "");
    return;
  }
  if (O.pending || !near) return;
  const before = cpOlderEarliest(st);
  if (!before) return;
  O.pending = { before, sent: 0, tries: 0, waiting: false };
  cpOlderSend(st);
}

function cpOlderTick(st) {
  const O = st.older;
  if (!O || !O.pending || Date.now() - O.pending.sent < CP_OLDER_RETRY) return;
  if (O.pending.tries >= 10) { O.pending = null; cpOlderLabel(st, ""); return; }
  cpOlderSend(st);
}

function cpRender(LWC, st, data) {
  const spec = data.spec;
  if (!st.chart) {
    st.chart = LWC.createChart(st.box, cpChartOptions(spec, data.tz));
    st.chart.subscribeCrosshairMove((param) => cpLegend(st, param.time ? param : null));
    st.chart.timeScale().subscribeVisibleLogicalRangeChange(() => cpOlderCheck(st));
  }
  st.tz = data.tz;
  st.rendering = true;
  // Older history: a new piece is put in front of each series below.
  if (!(spec.older && spec.older.enabled)) st.older = null;
  else if (!st.older || st.older.rev !== data.rev) st.older = cpOlderNew(data.rev);
  const O = st.older;
  let chunk = null;
  const ch = spec.older_chunk;
  if (O && ch && !O.got.has(ch.before)) {
    if (ch.retry) {  // Binance busy or the budget spent: ask again in a few seconds
      if (O.pending && O.pending.before === ch.before) Object.assign(O.pending, { tries: 0, waiting: true });
    } else {
      O.got.add(ch.before);
      chunk = ch;
    }
  }
  const ro = spec.right_offset === undefined ? 6 : spec.right_offset;
  if (st.secondsVisible !== !!spec.seconds || st.rightOffset !== ro) {
    st.chart.applyOptions({ timeScale: { secondsVisible: !!spec.seconds, rightOffset: ro } });
  }
  st.secondsVisible = !!spec.seconds;
  st.rightOffset = ro;
  const wanted = new Set(), remark = [];
  for (const s of spec.series) {
    wanted.add(s.id);
    let e = st.series.get(s.id);
    const pane = s.pane || 0;
    if (e && (e.type !== s.type || e.pane !== pane)) {
      st.chart.removeSeries(e.api);
      st.series.delete(s.id);
      e = null;
    }
    if (!e) {
      const api = st.chart.addSeries(LWC[s.type + "Series"], cpSeriesOptions(s, st), pane);
      e = { api, type: s.type, pane, hash: null, ohash: null, lhash: null, mhash: null, lines: [], markers: null,
            lastTime: null, firstTime: null, live: null, boxes: null, bhash: null };
      st.series.set(s.id, e);
    }
    const oh = JSON.stringify([s.options || {}, s.margins || null, s.include || null]);
    if (oh !== e.ohash) {
      e.api.applyOptions(cpSeriesOptions(s, st));
      if (s.margins) e.api.priceScale().applyOptions({ scaleMargins: s.margins });
      e.ohash = oh;
    }
    const piece = chunk && chunk.series && chunk.series[s.id];
    if (s.hash !== e.hash || piece) {
      let rows = cpRows(s);
      if (O) rows = cpOlderRows(O, s.id, rows, piece ? cpRows({ type: s.type, data: piece }) : null);
      e.api.setData(rows);
      e.hash = s.hash;
      e.lastTime = rows.length ? rows[rows.length - 1].time : null;
      e.firstTime = rows.length ? rows[0].time : null;
      e.liveValue = null;
    }
    const lh = JSON.stringify(s.lines || []);
    if (lh !== e.lhash) {
      e.lines.forEach((l) => e.api.removePriceLine(l));
      e.lines = (s.lines || []).map((l) => e.api.createPriceLine(Object.assign(
        { lineWidth: 1, lineStyle: 2, axisLabelVisible: true }, l)));
      e.lhash = lh;
    }
    let marks = s.markers || [];
    if (O) {
      const first = s.data && s.data.t && s.data.t.length ? s.data.t[0] : Infinity;
      marks = cpOlderMarkers(O, s.id, marks, first, chunk && chunk.markers && chunk.markers[s.id]);
    }
    const mh = JSON.stringify(marks);
    if (mh !== e.mhash) {
      e.markList = marks;
      e.mhash = mh;
      remark.push(e);
    }
    const bh = JSON.stringify([s.boxes || [], e.firstTime]);
    if (bh !== e.bhash && (s.boxes || e.boxes)) {
      if (!e.boxes) e.api.attachPrimitive(e.boxes = new CpBoxes());
      e.boxes.set(s.boxes || [], e.firstTime);
      e.bhash = bh;
    }
    e.live = s.live || null;
    e.title = s.legend || null;
    e.color = (s.options || {}).color || (s.options || {}).upColor || "#8b93a5";
    e.digits = s.digits === undefined ? 2 : s.digits;
    e.lastValue = cpLastValue(s);
  }
  for (const [id, e] of [...st.series]) {
    if (!wanted.has(id)) { st.chart.removeSeries(e.api); st.series.delete(id); }
  }
  // Markers once every series has its data: the library puts a marker on the bar the time scale has at that
  // moment, and while the series get their data one by one a moved last point (the NAV's "now") is briefly
  // there twice, which left the latest marker on a bar that then vanished (drawn at the top, cut off).
  for (const e of remark) {
    if (!e.markers) e.markers = LWC.createSeriesMarkers(e.api, []);
    e.markers.setMarkers(e.markList);
  }
  const panes = st.chart.panes();
  (spec.panes || []).forEach((f, i) => { if (panes[i]) panes[i].setStretchFactor(f); });
  if (st.rev !== data.rev) {
    st.rev = data.rev;
    st.autoFit = true;
    let n = 0, lastTime = null;
    for (const e of st.series.values()) {
      const d = e.api.data() || [];
      if (d.length > n) { n = d.length; lastTime = d[d.length - 1].time; }
    }
    const ts = st.chart.timeScale();
    // bar_px (the NAV chart, the Spread Z): one point every bar_px pixels in every timeframe, as TradingView opens
    // a chart, so a finer timeframe shows less time; a NAV history that does not fill the width shows whole.
    // keep_zoom (the Spread Z): another timeframe or pair keeps the width of a bar the user had, as TradingView
    // does (bar_px the first time, or coming from another kind of chart).
    // The end in bars of the time scale: gaps (the bot down) are bars there but not in data().
    const idx = lastTime !== null ? ts.timeToIndex(lastTime, true) : null;
    const end = idx === null || idx === undefined ? n : idx + 1;
    if (spec.bar_px) {
      const px = spec.keep_zoom && st.keptZoom ? Math.min(Math.max(ts.options().barSpacing, 1), 40) : spec.bar_px;
      const pad = Math.max(ro, (spec.right_px || 0) / px);
      const plot = ts.width() || Math.max(st.box.clientWidth - 70, 0) || 1000;  // less the price scale until laid out
      if (spec.keep_zoom || end > plot / px - pad) {  // keep_zoom: never stretched to fill (it would carry over)
        ts.applyOptions({ barSpacing: px });  // exactly px, so a bar keeps its width through every switch
        ts.scrollToPosition(pad, false);      // the latest bar, pad bars from the right edge
        st.autoFit = false;  // "fit" waits for a double-click
      } else {
        ts.fitContent();
      }
    } else if (spec.visible_bars && n > spec.visible_bars) {
      ts.setVisibleLogicalRange({ from: end - spec.visible_bars, to: end + 6 });
    } else {
      ts.fitContent();
    }
    st.keptZoom = !!spec.keep_zoom;
  } else if (spec.fit && st.autoFit) {
    st.chart.timeScale().fitContent(); // "fit": keep the whole history in view as bars are added
  }
  st.fitMode = !!spec.fit;
  // Live bindings: (re)subscribe to the page's market hub with the symbols they need.
  const syms = new Set(), klines = [];
  for (const e of st.series.values()) {
    const L = e.live;
    if (!L) continue;
    if (L.a) { syms.add(L.a); syms.add(L.b); }
    if (L.symbol) { syms.add(L.symbol); klines.push([L.symbol, L.interval]); }
  }
  const key = JSON.stringify([[...syms].sort(), klines]);
  const hub = cpHub();
  if (key !== st.subKey) {
    st.subKey = key;
    if (syms.size) hub.subscribe(st.token, [...syms], klines, (h) => cpApplyLive(st, h));
    else hub.unsubscribe(st.token);
  }
  cpLegend(st);
  if (st.status) st.status.textContent = "";
  if (O && chunk) {  // the piece is in: done, or ask for the next one if the edge is still near
    if (O.pending && O.pending.before === chunk.before) O.pending = null;
    O.done = !!chunk.done;
    if (chunk.done) O.first = cpOlderEarliest(st);
    cpOlderLabel(st, "");
  }
  st.rendering = false;
  if (O) setTimeout(() => cpOlderCheck(st), 0);
  else cpOlderLabel(st, "");
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  let st = parentElement.__cpTv;
  if (!st) {
    st = parentElement.__cpTv = { series: new Map(), token: Math.random().toString(36).slice(2) };
    st.wrap = document.createElement("div");
    st.wrap.className = "cp-tv";
    st.legend = document.createElement("div");
    st.legend.className = "cp-tv-legend";
    st.box = document.createElement("div");
    st.box.className = "cp-tv-box";
    st.status = document.createElement("div");
    st.status.className = "cp-tv-status";
    st.status.textContent = "loading chart…";
    st.olderEl = document.createElement("div");
    st.olderEl.className = "cp-tv-older";
    st.wrap.append(st.legend, st.box, st.status, st.olderEl);
    st.olderTimer = setInterval(() => cpOlderTick(st), 1000);
    parentElement.appendChild(st.wrap);
    // With "fit", the chart shows everything until the user zooms or pans; a double-click shows everything again.
    const manual = () => { if (st.fitMode) st.autoFit = false; };
    ["wheel", "mousedown", "touchstart"].forEach((ev) => st.box.addEventListener(ev, manual, { passive: true }));
    st.box.addEventListener("dblclick", () => {
      st.autoFit = true;
      if (st.chart) st.chart.timeScale().fitContent();
    });
  }
  st.send = setTriggerValue;  // the latest render's callback
  st.box.style.height = ((data.spec && data.spec.height) || 420) + "px";
  cpLWC().then((LWC) => {
    cpRender(LWC, st, data);
    st.wrap.dataset.rev = data.rev || "";  // what the chart shows now (pair-view-timeframe), for checks
  }).catch((err) => {
    st.status.textContent = "Chart library could not be loaded (" + err.message + ").";
  });
  return () => {
    clearInterval(st.olderTimer);
    cpHub().unsubscribe(st.token);
    if (st.chart) st.chart.remove();
    delete parentElement.__cpTv;
  };
}
