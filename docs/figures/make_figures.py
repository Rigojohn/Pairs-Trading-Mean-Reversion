"""Figures for the README, drawn from the walk-forward output (runs/walkforward_b/).

    python docs/figures/make_figures.py          # after: python -m cryptopairs --profile kalman_b walkforward run

Writes docs/figures/walkforward.png: NAV of both selection rules, the drawdown of the
rule in use (Sharpe), and how many pairs the funnel kept before each month.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / "runs" / "walkforward_b"
OUT = Path(__file__).resolve().parent / "walkforward.png"

INK, MUTED, ACCENT, LOSS = "#1f2933", "#8a94a6", "#1f5fa8", "#b23a3a"


def nav(variant: str) -> pd.Series:
    df = pd.read_csv(WF / f"equity_{variant}.csv")
    s = pd.Series(df["equity"].to_numpy(), index=pd.to_datetime(df["ts"], unit="ms"))
    return s / s.iloc[0]


def pairs_per_month() -> pd.Series:
    counts = {}
    for fold in sorted((WF / "folds").iterdir()):
        chosen = fold / "chosen_sharpe.csv"
        counts[pd.Timestamp(fold.name + "-01")] = sum(1 for _ in chosen.open()) - 1 if chosen.exists() else 0
    return pd.Series(counts)


def main() -> None:
    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": ["Liberation Serif", "Times New Roman", "DejaVu Serif"],
        "font.size": 10,
        "axes.edgecolor": MUTED,
        "axes.labelcolor": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })
    sharpe, sortino = nav("sharpe"), nav("sortino_dd")
    drawdown = sharpe / sharpe.cummax() - 1
    pairs = pairs_per_month()

    fig, (ax_nav, ax_dd, ax_n) = plt.subplots(
        3, 1, figsize=(10, 6.4), sharex=True, gridspec_kw={"height_ratios": [3, 1.3, 1]}, facecolor="white"
    )
    ax_nav.axhline(1, color=MUTED, lw=0.8, ls=":")
    ax_nav.plot(sortino.index, sortino, color=MUTED, lw=1.1, ls="--", label="Sortino + drawdown rule (rejected)")
    ax_nav.plot(sharpe.index, sharpe, color=ACCENT, lw=1.6, label="Sharpe rule (in use)")
    ax_nav.set_ylabel("NAV (start = 1)")
    ax_nav.set_ylim(0.58, None)
    ax_nav.legend(frameon=False, loc="upper left")
    ax_nav.set_title(
        "Walk-forward of bot B's recipe: 48 monthly out-of-sample folds, Oct 2022 – Sep 2026",
        loc="left", color=INK, fontsize=11,
    )
    trough = drawdown.idxmin()
    ax_nav.annotate(
        f"trough {trough:%d %b %Y}\n{drawdown.min():.1%} from peak".replace("-", "−"),
        xy=(trough, sharpe[trough]), xytext=(trough + pd.Timedelta(days=45), 0.615),
        color=LOSS, fontsize=9, arrowprops={"arrowstyle": "-", "color": LOSS, "lw": 0.8},
    )

    ax_dd.fill_between(drawdown.index, drawdown, 0, color=LOSS, alpha=0.25, lw=0)
    ax_dd.plot(drawdown.index, drawdown, color=LOSS, lw=0.9)
    ax_dd.set_ylabel("Drawdown")
    ax_dd.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))

    ax_n.bar(pairs.index, pairs.to_numpy(), width=25, align="edge", color=MUTED)
    ax_n.set_ylabel("Pairs kept")
    ax_n.set_ylim(0, 110)
    ax_n.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
    ax_n.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

    for ax in (ax_nav, ax_dd, ax_n):
        ax.grid(axis="y", color="#e4e7eb", lw=0.6)
        ax.set_axisbelow(True)
    fig.align_ylabels()
    fig.tight_layout()
    fig.savefig(OUT, dpi=200)
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
