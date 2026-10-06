// Smart live refresh (ui/tv.py pulse()): a live part of a page reruns when the bot has written new
// data, not on a fixed timer. The dashboard's server writes the bot database's version to a small
// static file (ui/pulse.py); this polls it (one request per file and interval for the whole page, a
// static file: no Python runs) and asks for a rerun of its fragment only when the version is newer
// than the one the fragment was drawn with. Streamlit drops a fragment's rerun request while another
// one is running, so the fragments of a page are asked one at a time: the next as soon as the previous
// one has been drawn with the new version (or after 2.5 s, when a request was lost).

function cpPulseHub() {
  if (!window.__cpPulse) window.__cpPulse = { feeds: new Map() };
  return window.__cpPulse;
}

function cpPulseFeed(url, everyMs) {
  const hub = cpPulseHub();
  let f = hub.feeds.get(url);
  if (!f) {
    f = { url, everyMs, v: null, listeners: new Set(), busy: false, timer: null, inflight: null, inflightAt: 0 };
    f.kick = () => {
      if (f.v === null) return;
      const w = f.inflight;
      if (w && f.listeners.has(w) && w.drawn < f.v && Date.now() - f.inflightAt < 2500) return;  // still running
      f.inflight = null;
      for (const st of f.listeners) {
        if (st.drawn < f.v) { f.inflight = st; f.inflightAt = Date.now(); st.send("tick", f.v); return; }
      }
    };
    hub.feeds.set(url, f);
  }
  if (!f.timer || f.everyMs !== everyMs) {
    clearInterval(f.timer);
    f.everyMs = everyMs;
    f.timer = setInterval(() => cpPulsePoll(f), everyMs);
  }
  return f;
}

async function cpPulsePoll(f) {
  if (!f.listeners.size) { clearInterval(f.timer); f.timer = null; cpPulseHub().feeds.delete(f.url); return; }
  if (f.busy) return;
  f.busy = true;
  try {
    const r = await fetch(f.url + "?t=" + Date.now(), { cache: "no-store" });
    if (r.ok) {
      const v = Number(JSON.parse(await r.text()).v);
      if (isFinite(v)) { f.v = v; f.kick(); }
    }
  } catch (e) { /* the next poll tries again */ } finally { f.busy = false; }
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  if (parentElement) parentElement.style.display = "none";
  let st = parentElement.__cpPulse;
  if (!st) st = parentElement.__cpPulse = {};
  st.drawn = Number(data.v);
  st.send = setTriggerValue;
  const feed = cpPulseFeed(new URL(data.url, window.location.origin + "/").href, Math.max(250, data.every_ms || 1000));
  if (st.feed && st.feed !== feed) st.feed.listeners.delete(st);
  st.feed = feed;
  feed.listeners.add(st);
  feed.kick();  // this fragment is drawn: ask the next one
  return () => {
    feed.listeners.delete(st);
    delete parentElement.__cpPulse;
  };
}
