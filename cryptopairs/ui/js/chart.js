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

const CP_SHADE = { autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 1 } }) };

function cpChartOptions(spec, tz) {
  const tf = cpTimeFmt(tz);
  return {
    autoSize: true,
    layout: {
      background: { type: "solid", color: "rgba(0,0,0,0)" }, textColor: "#8b93a5", fontSize: 11,
      fontFamily: "Inter, 'Segoe UI', Roboto, sans-serif", attributionLogo: true,
      panes: { separatorColor: "#232b3a", separatorHoverColor: "rgba(240,180,41,0.25)", enableResize: true },
    },
    grid: { vertLines: { color: "#151b27" }, horzLines: { color: "#151b27" } },
    rightPriceScale: { borderColor: "#232b3a", scaleMargins: { top: 0.08, bottom: 0.08 } },
    timeScale: { borderColor: "#232b3a", timeVisible: true, secondsVisible: !!spec.seconds, rightOffset: 6,
                 shiftVisibleRangeOnNewBar: true, tickMarkFormatter: tf.tick },
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
      if (d.c && d.c[i] && out[i].value !== undefined) {
        if (s.type === "Area") Object.assign(out[i], { topColor: d.c[i], bottomColor: d.c[i], lineColor: "rgba(0,0,0,0)" });
        else out[i].color = d.c[i];
      }
    }
  }
  return out;
}

function cpSeriesOptions(s) {
  const o = Object.assign({}, s.options || {});
  if (s.role === "shade") Object.assign(o, CP_SHADE, { priceScaleId: "shade", lastValueVisible: false, priceLineVisible: false });
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

function cpRender(LWC, st, data) {
  const spec = data.spec;
  if (!st.chart) {
    st.chart = LWC.createChart(st.box, cpChartOptions(spec, data.tz));
    st.chart.subscribeCrosshairMove((param) => cpLegend(st, param.time ? param : null));
  }
  const ro = spec.right_offset === undefined ? 6 : spec.right_offset;
  if (st.secondsVisible !== !!spec.seconds || st.rightOffset !== ro) {
    st.chart.applyOptions({ timeScale: { secondsVisible: !!spec.seconds, rightOffset: ro } });
  }
  st.secondsVisible = !!spec.seconds;
  st.rightOffset = ro;
  const wanted = new Set();
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
      const api = st.chart.addSeries(LWC[s.type + "Series"], cpSeriesOptions(s), pane);
      e = { api, type: s.type, pane, hash: null, ohash: null, lhash: null, mhash: null, lines: [], markers: null,
            lastTime: null, live: null };
      st.series.set(s.id, e);
    }
    const oh = JSON.stringify([s.options || {}, s.margins || null, s.include || null]);
    if (oh !== e.ohash) {
      e.api.applyOptions(cpSeriesOptions(s));
      if (s.margins) e.api.priceScale().applyOptions({ scaleMargins: s.margins });
      e.ohash = oh;
    }
    if (s.hash !== e.hash) {
      const rows = cpRows(s);
      e.api.setData(rows);
      e.hash = s.hash;
      e.lastTime = rows.length ? rows[rows.length - 1].time : null;
      e.liveValue = null;
    }
    const lh = JSON.stringify(s.lines || []);
    if (lh !== e.lhash) {
      e.lines.forEach((l) => e.api.removePriceLine(l));
      e.lines = (s.lines || []).map((l) => e.api.createPriceLine(Object.assign(
        { lineWidth: 1, lineStyle: 2, axisLabelVisible: true }, l)));
      e.lhash = lh;
    }
    const mh = JSON.stringify(s.markers || []);
    if (mh !== e.mhash) {
      if (!e.markers) e.markers = LWC.createSeriesMarkers(e.api, []);
      e.markers.setMarkers(s.markers || []);
      e.mhash = mh;
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
  const panes = st.chart.panes();
  (spec.panes || []).forEach((f, i) => { if (panes[i]) panes[i].setStretchFactor(f); });
  if (st.rev !== data.rev) {
    st.rev = data.rev;
    st.autoFit = true;
    const n = Math.max(0, ...[...st.series.values()].map((e) => (e.api.data() || []).length));
    if (spec.visible_bars && n > spec.visible_bars) {
      st.chart.timeScale().setVisibleLogicalRange({ from: n - spec.visible_bars, to: n + 6 });
    } else {
      st.chart.timeScale().fitContent();
    }
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
}

export default function (component) {
  const { data, parentElement } = component;
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
    st.wrap.append(st.legend, st.box, st.status);
    parentElement.appendChild(st.wrap);
    // With "fit", the chart shows everything until the user zooms or pans; a double-click shows everything again.
    const manual = () => { if (st.fitMode) st.autoFit = false; };
    ["wheel", "mousedown", "touchstart"].forEach((ev) => st.box.addEventListener(ev, manual, { passive: true }));
    st.box.addEventListener("dblclick", () => {
      st.autoFit = true;
      if (st.chart) st.chart.timeScale().fitContent();
    });
  }
  st.box.style.height = ((data.spec && data.spec.height) || 420) + "px";
  cpLWC().then((LWC) => cpRender(LWC, st, data)).catch((err) => {
    st.status.textContent = "Chart library could not be loaded (" + err.message + ").";
  });
  return () => {
    cpHub().unsubscribe(st.token);
    if (st.chart) st.chart.remove();
    delete parentElement.__cpTv;
  };
}
