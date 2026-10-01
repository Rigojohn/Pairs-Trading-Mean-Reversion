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
  const config = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d"] };
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

_renderers: dict[int, tuple[object, Callable]] = {}


def _renderer() -> Callable:
    """The mount command, registered once per Streamlit runtime (tests start several)."""
    from streamlit.components.v2.get_bidi_component_manager import get_bidi_component_manager

    manager = get_bidi_component_manager()
    entry = _renderers.get(id(manager))
    if entry is None or entry[0] is not manager:  # keeping the manager stops its id being reused
        mount = st.components.v2.component(
            "cryptopairs_live_chart",
            js=JS.replace("__VERSION__", plotly.offline.get_plotlyjs_version()),
            css=CSS,
            isolate_styles=False,  # plotly.js styles its hover labels and toolbar from the page's <head>
        )
        entry = _renderers[id(manager)] = (manager, mount)
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
    _renderer()(key=key, data={"fig": fig.to_json(), "rev": rev or key}, width="stretch")
