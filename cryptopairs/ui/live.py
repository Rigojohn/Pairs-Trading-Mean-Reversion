"""Charts that update in place, like a trading screen.

``st.plotly_chart`` rebuilds the whole chart whenever its data changes (its element id
includes the figure), so a page refreshing every few seconds blinks and loses the
user's zoom. This component is mounted once per ``key``; later reruns only hand it the
new figure, and the browser applies it with ``Plotly.react``: no redraw from scratch,
zoom/pan and legend clicks kept (``layout.uirevision``).

plotly.js comes from the installed plotly package, served by Streamlit's static file
serving (``server.enableStaticServing``) at ``app/static/plotly.min.js``.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import plotly
import plotly.graph_objects as go
import streamlit as st

STATIC = Path(__file__).resolve().parents[1] / "static"  # next to dashboard.py, as Streamlit requires
PLOTLY_JS = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"

JS = r"""
const LOCAL = "app/static/plotly.min.js";
const CDN = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@__VERSION__/plotly.min.js";

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = () => (window.Plotly ? resolve(window.Plotly) : reject(new Error("no Plotly in " + src)));
    s.onerror = () => reject(new Error("could not load " + src));
    document.head.appendChild(s);
  });
}

function plotly() {
  if (window.Plotly) return Promise.resolve(window.Plotly);
  if (!window.__cpPlotly) {
    const local = new URL(LOCAL, window.location.origin + "/").href;
    window.__cpPlotly = loadScript(local).catch(() => loadScript(CDN));
    window.__cpPlotly.catch(() => { window.__cpPlotly = null; });
  }
  return window.__cpPlotly;
}

export default function (component) {
  const { data, parentElement } = component;
  let el = parentElement.querySelector(".cp-chart");
  if (!el) {
    el = document.createElement("div");
    el.className = "cp-chart";
    parentElement.appendChild(el);
  }
  const fig = JSON.parse(data.fig);
  const layout = fig.layout || {};
  el.style.height = (layout.height || 400) + "px";  // reserve the space at once: no jump while loading
  delete layout.width;
  layout.autosize = true;
  layout.uirevision = data.rev;  // same value on every update = keep zoom, pan and legend state
  const config = { displaylogo: false, responsive: true, displayModeBar: "hover",
                   modeBarButtonsToRemove: ["lasso2d", "select2d"] };
  plotly().then((Plotly) => {
    if (el.__cpReady) {
      Plotly.react(el, fig.data, layout, config);
    } else {
      el.__cpReady = true;
      Plotly.newPlot(el, fig.data, layout, config);
    }
  }).catch((err) => {
    el.textContent = "Chart library could not be loaded (" + err.message + ").";
  });
  return () => {
    if (window.Plotly && el.__cpReady) window.Plotly.purge(el);
  };
}
"""

CSS = ".cp-chart { width: 100%; }"

# A badge that ticks every second in the browser: how old the bot's data is and when the page last
# synced. If the page stops receiving updates (server restarted, network, sleeping laptop), it turns red
# and reloads the page by itself instead of silently showing frozen numbers.
WATCHDOG_JS = r"""
function fmt(s) { return s < 90 ? s.toFixed(0) + "s" : s < 5400 ? (s / 60).toFixed(0) + " min" : (s / 3600).toFixed(1) + " h"; }

export default function (component) {
  const { data, parentElement } = component;
  const W = window.__cpWatch || (window.__cpWatch = {});
  const token = parentElement.__cpToken || (parentElement.__cpToken = Math.random());
  W.owner = token;
  W.last = Date.now();
  W.offset = data.server_ms - Date.now();  // server clock vs this browser's clock
  W.bot = data.bot_ms;
  W.every = data.every;
  W.reloadAfter = Math.max(45, 10 * data.every);
  let el = document.getElementById("cp-watchdog");
  if (!el) {
    el = document.createElement("div");
    el.id = "cp-watchdog";
    document.body.appendChild(el);
  }
  const lost = () => W.every > 0 && (Date.now() - W.last) / 1000 > Math.max(15, 6 * W.every);
  const tick = () => {
    const now = Date.now();
    const page = (now - W.last) / 1000;
    const bot = W.bot ? Math.max(0, (now + W.offset - W.bot) / 1000) : null;
    let cls, text;
    if (W.every === 0) {
      cls = "mute"; text = "❚❚ live updates paused";
    } else if (lost()) {
      cls = "bad"; text = "● connection lost " + fmt(page) + " · reloading in " + Math.max(0, Math.ceil(W.reloadAfter - page)) + "s";
      if (page > W.reloadAfter && !W.reloading) { W.reloading = true; location.reload(); }
    } else if (bot === null || bot > 30) {
      cls = "warn"; text = "● bot data " + (bot === null ? "missing" : fmt(bot) + " old") + " · is the bot running?";
    } else {
      cls = "ok"; text = "● LIVE · prices " + fmt(bot) + " old · page " + fmt(page) + " ago";
    }
    el.className = cls;
    el.textContent = text;
  };
  if (!W.timer) W.timer = setInterval(tick, 1000);
  if (!W.onVisible) {  // a tab left in the background may have missed updates: catch up when it is shown
    W.onVisible = () => { if (!document.hidden && lost()) location.reload(); };
    document.addEventListener("visibilitychange", W.onVisible);
  }
  tick();
  return () => {
    if (W.owner !== token) return;  // another page's badge already took over
    clearInterval(W.timer);
    W.timer = null;
    const e = document.getElementById("cp-watchdog");
    if (e) e.remove();
  };
}
"""

_renderers: dict[tuple[int, str], tuple[object, Callable]] = {}


def _renderer(name: str = "chart") -> Callable:
    """A mount command, registered once per Streamlit runtime (tests start several)."""
    from streamlit.components.v2.get_bidi_component_manager import get_bidi_component_manager

    manager = get_bidi_component_manager()
    entry = _renderers.get((id(manager), name))
    if entry is None or entry[0] is not manager:  # keeping the manager stops its id being reused
        if name == "chart":
            mount = st.components.v2.component(
                "cryptopairs_live_chart",
                js=JS.replace("__VERSION__", plotly.offline.get_plotlyjs_version()),
                css=CSS,
                isolate_styles=False,  # plotly.js styles its hover labels and toolbar from the page's <head>
            )
        else:
            mount = st.components.v2.component("cryptopairs_watchdog", js=WATCHDOG_JS, isolate_styles=False)
        entry = _renderers[(id(manager), name)] = (manager, mount)
    return entry[1]


def ensure_plotly_js() -> None:
    """Copy plotly.js next to the app for static serving (once, or after a plotly upgrade)."""
    target = STATIC / "plotly.min.js"
    if not target.exists() or target.stat().st_size != PLOTLY_JS.stat().st_size:
        STATIC.mkdir(exist_ok=True)
        shutil.copyfile(PLOTLY_JS, target)


def chart(fig: go.Figure, key: str, rev: str | None = None) -> None:
    """Show ``fig``; reruns with the same ``key`` update it in place.

    ``rev`` resets the user's zoom when it changes (e.g. another pair or time window).
    """
    _renderer("chart")(key=key, data={"fig": fig.to_json(), "rev": rev or key}, width="stretch")


def watchdog(bot_ms: int | None, every: int, server_ms: int) -> None:
    """The live badge (top right): ticks in the browser, flags and recovers a page that stopped updating."""
    _renderer("watchdog")(key="watchdog", data={"bot_ms": bot_ms, "every": every, "server_ms": server_ms},
                          width="content", height=0)
