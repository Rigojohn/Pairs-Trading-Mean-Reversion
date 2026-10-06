// Carries the sidebar watchlist's clicks to Python. The watchlist is redrawn by a fragment that reruns
// every second, and Streamlit drops a fragment's trigger while the page is running (the click then took
// 20-40 s or never arrived). This component sits outside every fragment, so its trigger is an ordinary
// app rerun that interrupts the running page, like a button does.
// It also keeps every pair's live-panel data (fit, position, symbols) for the watchlist: a click hands it to
// the ticker, books and tape (js/market.js, event "cp-pair"), which switch at once.
export default function (component) {
  const { data, setTriggerValue, parentElement } = component;
  window.__cpPick = (id) => setTriggerValue("pick", id);
  window.__cpMd = (data && data.md) || {};
  if (parentElement) parentElement.style.display = "none";
}
