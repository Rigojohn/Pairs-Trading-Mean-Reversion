// Shared by the dashboard's JavaScript components (ui/tv.py prepends it to each module).
//
// 1. cpLWC(): TradingView Lightweight Charts, served by Streamlit's static file serving
//    (copied there on start-up), with the CDN as a fallback.
// 2. cpHub(): one connection to Binance USDⓈ-M public market data for the whole page.
//    Binance routes the streams by type: /public carries order books and best bid/ask,
//    /market carries trades, candles and mark price / funding. The hub opens both, keeps the
//    latest state per symbol, reconnects with backoff and notifies subscribers at most
//    10 times a second. Nothing here reaches the bot: it is a view of the exchange.

const CP_LWC_LOCAL = "app/static/lightweight-charts.standalone.production.js";
const CP_LWC_CDN = "https://unpkg.com/lightweight-charts@__LWC_VERSION__/dist/lightweight-charts.standalone.production.js";
const CP_WS = "wss://fstream.binance.com";
const CP_ROUTES = { public: ["depth20@100ms", "bookTicker"], market: ["aggTrade", "markPrice@1s"] };

function cpLoadScript(src, globalName) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = () => (window[globalName] ? resolve(window[globalName]) : reject(new Error("no " + globalName + " in " + src)));
    s.onerror = () => reject(new Error("could not load " + src));
    document.head.appendChild(s);
  });
}

function cpLWC() {
  if (window.LightweightCharts) return Promise.resolve(window.LightweightCharts);
  if (!window.__cpLWC) {
    const local = new URL(CP_LWC_LOCAL, window.location.origin + "/").href;
    window.__cpLWC = cpLoadScript(local, "LightweightCharts").catch(() => cpLoadScript(CP_LWC_CDN, "LightweightCharts"));
    window.__cpLWC.catch(() => { window.__cpLWC = null; });
  }
  return window.__cpLWC;
}

class CpMarketHub {
  constructor() {
    this.subs = new Map(); // token -> {symbols, klines, fn}
    this.socks = {};       // route -> {ws, url, backoff, status}
    this.state = { book: {}, bbo: {}, trades: [], mark: {}, kline: {} };
    this.msgs = 0; this.rate = 0; this.lastMsg = 0; this.lag = null;
    this.timer = null;
    this.reqId = 0;
    this.seeded = new Set();
    setInterval(() => { this.rate = this.msgs; this.msgs = 0; this.notify(); }, 1000);
  }

  subscribe(token, symbols, klines, fn) {
    this.subs.set(token, { symbols, klines: klines || [], fn });
    this.reconfigure();
  }

  unsubscribe(token) {
    this.subs.delete(token);
    // Streamlit remounts elements on some reruns: wait a little before closing the sockets.
    setTimeout(() => this.reconfigure(), 3000);
  }

  wanted() {
    const syms = new Set(), kl = new Set();
    for (const s of this.subs.values()) {
      s.symbols.forEach((x) => syms.add(x.toLowerCase()));
      s.klines.forEach(([sym, tf]) => kl.add(sym.toLowerCase() + "@kline_" + tf));
    }
    const out = {};
    for (const route of Object.keys(CP_ROUTES)) {
      const streams = [];
      for (const s of [...syms].sort()) CP_ROUTES[route].forEach((k) => streams.push(s + "@" + k));
      if (route === "market") [...kl].sort().forEach((k) => streams.push(k));
      out[route] = streams;
    }
    return out;
  }

  // A new pair's streams are added to the open connection with a SUBSCRIBE message (and old ones
  // dropped with UNSUBSCRIBE): no new connection, so a pair switch shows its book in ~0.2 s instead
  // of a fresh TLS handshake, and the pair still on screen never loses its feed.
  // A symbol just added gets one REST snapshot (book, best bid/ask, mark/funding, last trades): a quiet
  // market can take a second or two to send its first stream message. The streams overwrite it.
  seed(symbols) {
    const api = "https://fapi.binance.com/fapi/v1/", st = this.state;
    const get = (path) => fetch(api + path).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    for (const S of symbols) {
      if (this.seeded.has(S)) continue;
      this.seeded.add(S);
      get(`depth?symbol=${S}&limit=20`).then((b) => {
        if (b && !st.book[S]) { st.book[S] = { bids: b.bids, asks: b.asks, ts: b.E || Date.now() }; this.notify(); }
      });
      get(`ticker/bookTicker?symbol=${S}`).then((t) => {
        if (t && !st.bbo[S]) {
          st.bbo[S] = { bid: +t.bidPrice, ask: +t.askPrice, bq: +t.bidQty, aq: +t.askQty, ts: Date.now() };
          this.notify();
        }
      });
      get(`premiumIndex?symbol=${S}`).then((m) => {
        if (m && !st.mark[S]) {
          st.mark[S] = { mark: +m.markPrice, index: +m.indexPrice, rate: +m.lastFundingRate, next: m.nextFundingTime, ts: Date.now() };
          this.notify();
        }
      });
      get(`aggTrades?symbol=${S}&limit=30`).then((tr) => {
        if (!tr || st.trades.some((t) => t.s === S)) return;
        tr.forEach((t) => st.trades.push({ s: S, p: +t.p, q: +t.q, sell: !!t.m, T: t.T }));
        st.trades.sort((x, y) => y.T - x.T);
        if (st.trades.length > 80) st.trades.length = 80;
        this.notify();
      });
    }
  }

  reconfigure() {
    const syms = new Set(), keep = new Set();
    for (const s of this.subs.values()) {
      s.symbols.forEach((x) => { syms.add(x.toUpperCase()); keep.add(x.toUpperCase()); });
      s.klines.forEach(([x]) => keep.add(x.toUpperCase()));
    }
    // A symbol no longer on the page stops updating: forget its data, so a later visit never shows it as live.
    const st = this.state;
    for (const S of this.symsNow || []) {
      if (keep.has(S)) continue;
      delete st.book[S]; delete st.bbo[S]; delete st.mark[S]; delete st.kline[S];
      st.trades = st.trades.filter((t) => t.s !== S);
      this.seeded.delete(S);
    }
    this.symsNow = keep;
    this.seed(syms);
    const want = this.wanted();
    for (const route of Object.keys(CP_ROUTES)) {
      const streams = want[route], cur = this.socks[route];
      if (!streams.length) {
        if (cur) { cur.closed = true; try { cur.ws.close(); } catch (e) { /* already closed */ } delete this.socks[route]; }
        continue;
      }
      if (!cur) { this.connect(route, streams, 1000); continue; }
      cur.want = streams;  // a connection still opening applies it on open; a reconnect starts with it
      if (cur.ws.readyState === WebSocket.OPEN) this.sync(cur);
    }
  }

  sync(sock) {
    const want = new Set(sock.want), have = sock.live;
    const add = [...want].filter((x) => !have.has(x)), drop = [...have].filter((x) => !want.has(x));
    const send = (method, params) => {
      for (let i = 0; i < params.length; i += 200) {  // Binance takes a list per message; keep them short
        sock.ws.send(JSON.stringify({ method, params: params.slice(i, i + 200), id: ++this.reqId }));
      }
    };
    if (add.length) send("SUBSCRIBE", add);
    if (drop.length) send("UNSUBSCRIBE", drop);
    sock.live = want;
  }

  connect(route, streams, backoff) {
    const url = CP_WS + "/" + route + "/stream?streams=" + streams.join("/");
    const sock = { backoff, status: "connecting", closed: false, want: streams, live: new Set(streams), ws: new WebSocket(url) };
    this.socks[route] = sock;
    sock.ws.onopen = () => { sock.status = "live"; sock.backoff = 1000; this.sync(sock); this.notify(); };
    sock.ws.onmessage = (ev) => { try { this.ingest(JSON.parse(ev.data)); } catch (e) { /* bad frame */ } };
    sock.ws.onclose = () => {
      if (sock.closed || this.socks[route] !== sock) return;
      sock.status = "reconnecting";
      this.notify();
      setTimeout(() => { if (this.socks[route] === sock && !sock.closed) this.connect(route, sock.want, Math.min(sock.backoff * 2, 30000)); },
                 sock.backoff + Math.random() * 500);
    };
  }

  status() {
    const s = Object.values(this.socks);
    if (!s.length) return "idle";
    if (s.every((x) => x.status === "live")) return Date.now() - this.lastMsg > 15000 ? "silent" : "live";
    return s.some((x) => x.status === "live") ? "partial" : s[0].status;
  }

  ingest(m) {
    const d = m.data;
    if (!d) return;
    const now = Date.now();
    this.lastMsg = now;
    this.msgs++;
    if (d.E) this.lag = now - d.E; // network latency plus any clock offset of this computer
    const st = this.state;
    switch (d.e) {
      case "depthUpdate":
        st.book[d.s] = { bids: d.b, asks: d.a, ts: d.E || now };
        break;
      case "bookTicker":
        st.bbo[d.s] = { bid: +d.b, ask: +d.a, bq: +d.B, aq: +d.A, ts: now };
        break;
      case "aggTrade":
        st.trades.unshift({ s: d.s, p: +d.p, q: +d.q, sell: !!d.m, T: d.T });
        if (st.trades.length > 80) st.trades.length = 80;
        break;
      case "markPriceUpdate":
        st.mark[d.s] = { mark: +d.p, index: +d.i, rate: +d.r, next: d.T, ts: now };
        break;
      case "kline": {
        const k = d.k;
        (st.kline[d.s] ||= {})[k.i] = { t: k.t, o: +k.o, h: +k.h, l: +k.l, c: +k.c, q: +k.q, x: k.x };
        break;
      }
    }
    if (!this.timer) this.timer = setTimeout(() => { this.timer = null; this.notify(); }, 100);
  }

  notify() {
    for (const s of this.subs.values()) { try { s.fn(this); } catch (e) { console.error("market view", e); } }
  }

  // Executable Z-scores from the best bid/ask, exactly as core/signals.executable_zscores.
  z(a, b, fit) {
    const qa = this.state.bbo[a], qb = this.state.bbo[b];
    if (!qa || !qb || !fit || !(fit.sigma > 0)) return null;
    const now = Date.now();
    if (now - qa.ts > 30000 || now - qb.ts > 30000) return null;
    const zb = (Math.log(qa.ask) - fit.alpha - fit.beta * Math.log(qb.bid)) / fit.sigma;
    const zs = (Math.log(qa.bid) - fit.alpha - fit.beta * Math.log(qb.ask)) / fit.sigma;
    const mid_a = (qa.bid + qa.ask) / 2, mid_b = (qb.bid + qb.ask) / 2;
    const u = Math.log(mid_a) - fit.alpha - fit.beta * Math.log(mid_b);
    return { zb, zs, z: (zb + zs) / 2, u, mid_a, mid_b, ts: Math.max(qa.ts, qb.ts) };
  }
}

function cpHub() {
  return (window.__cpMarket ||= new CpMarketHub());
}

// Times in the dashboard's zone (Europe/Athens by default, DST included); the data is UTC.
function cpTimeFmt(tz) {
  const cache = (window.__cpTimeFmt ||= {});
  if (cache[tz]) return cache[tz];
  const mk = (o) => new Intl.DateTimeFormat("en-GB", Object.assign({ timeZone: tz || "UTC", hourCycle: "h23" }, o));
  const f = {
    full: mk({ weekday: "short", day: "2-digit", month: "short", year: "2-digit", hour: "2-digit", minute: "2-digit" }),
    hm: mk({ hour: "2-digit", minute: "2-digit" }), hms: mk({ hour: "2-digit", minute: "2-digit", second: "2-digit" }),
    day: mk({ day: "numeric", month: "short" }), month: mk({ month: "short" }), year: mk({ year: "numeric" }),
  };
  // Lightweight Charts TickMarkType: 0 year, 1 month, 2 day of month, 3 time, 4 time with seconds
  const tick = [f.year, f.month, f.day, f.hm, f.hms];
  return (cache[tz] = {
    crosshair: (t) => f.full.format(new Date(t * 1000)),
    tick: (t, type) => (tick[type] || f.hm).format(new Date(t * 1000)),
    clock: (ms) => f.hms.format(new Date(ms)),
  });
}

function cpFmt(v, digits) {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  return Number(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function cpCompact(v) {
  if (!isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "k";
  return v.toFixed(a >= 100 ? 0 : a >= 1 ? 2 : 4);
}
