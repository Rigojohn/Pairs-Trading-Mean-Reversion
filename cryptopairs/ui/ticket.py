"""Order-ticket previews: what a manual open or close would trade and cost, before it is sent.

The numbers follow the bot: the same sizing (``target_quantities`` + ``round_to_lots`` with
Binance's lot rules), the same executable Z (``executable_zscores``) and fit in use, and
fills estimated by walking the live order book. The bot sizes again with its own quotes
when it executes, so this is a close estimate, not a quote.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from cryptopairs.core.signals import executable_zscores
from cryptopairs.core.sizing import LotRules, SizingError, floor_to_step, round_to_lots, target_quantities


NO_BOOK = ("No order book from Binance on the dashboard's side right now, so there is no cost estimate. "
           "The order can still be sent: the bot sizes and prices it with its own live quotes.")


def vwap(levels: list, qty: float) -> tuple[float, float]:
    """Average price to trade ``qty`` (unsigned) against book levels [[price, size], ...].

    Returns (average price, quantity the shown levels can fill).
    """
    left, cost = abs(qty), 0.0
    for px, size in levels:
        take = min(left, float(size))
        cost += take * float(px)
        left -= take
        if left <= 1e-12:
            break
    filled = abs(qty) - max(left, 0.0)
    return (cost / filled if filled > 0 else math.nan), filled


@dataclass
class Leg:
    symbol: str
    label: str
    qty: float  # signed: + buy, - sell
    mid: float
    price: float  # estimated average fill
    fee: float
    maker: bool = False
    depth_ok: bool = True

    @property
    def notional(self) -> float:
        return abs(self.qty) * self.mid

    @property
    def slip_bps(self) -> float:
        """Fill vs mid, in bps against us (positive = cost)."""
        if not (self.mid > 0 and math.isfinite(self.price)):
            return math.nan
        return 1e4 * (self.price / self.mid - 1) * (1 if self.qty > 0 else -1)


@dataclass
class Preview:
    legs: list[Leg] = field(default_factory=list)
    error: str | None = None  # the bot would refuse this order: do not send it
    warning: str | None = None  # no estimate possible (e.g. no order book), but the order itself is fine
    notes: list[str] = field(default_factory=list)
    z_buy: float = math.nan
    z_sell: float = math.nan
    z: float = math.nan  # the Z this order trades at (buy Z for long, sell Z for short)
    signal: bool = False
    stop_level: float = math.nan
    catastrophe_level: float = math.nan
    funding_next: float = math.nan  # USDT paid at the next settlement (negative = received)
    funding_day: float = math.nan
    realized: float = math.nan  # close: PnL realized by this order, after its fees
    remaining: tuple[float, float] = (0.0, 0.0)

    @property
    def gross(self) -> float:
        return sum(l.notional for l in self.legs)

    @property
    def fees(self) -> float:
        return sum(l.fee for l in self.legs)

    @property
    def cost(self) -> float:
        """Fees plus the price paid away from mid, in USDT."""
        return self.fees + sum(abs(l.qty) * abs(l.price - l.mid) for l in self.legs if math.isfinite(l.price))

    @property
    def cost_bps(self) -> float:
        return 1e4 * self.cost / self.gross if self.gross else math.nan

    @property
    def hedge_ratio(self) -> float:
        if len(self.legs) != 2 or not self.legs[0].notional:
            return math.nan
        return self.legs[1].notional / self.legs[0].notional


def lot_rules(inst: dict | None) -> LotRules | None:
    if not inst or not inst.get("step"):
        return None
    return LotRules(float(inst["step"]), float(inst.get("min_qty") or 0.0), float(inst.get("min_notional") or 0.0))


def _best(book: dict) -> tuple[float, float]:
    return float(book["bids"][0][0]), float(book["asks"][0][0])


def _legs(pair: dict, books: dict, insts: dict | None, qa: float, qb: float, execution: str,
          taker_fee: float, maker_fee: float) -> list[Leg]:
    """Price both legs: walk the book for market orders; the larger leg at the touch for maker-first."""
    out = []
    sides = [(pair["symbol_a"], pair["label_a"], qa), (pair["symbol_b"], pair["label_b"], qb)]
    bigger = max(range(2), key=lambda i: abs(sides[i][2]) * sum(_best(books[sides[i][0]])) / 2)
    for i, (sym, label, q) in enumerate(sides):
        bid, ask = _best(books[sym])
        mid = (bid + ask) / 2
        maker = execution == "maker" and i == bigger
        if maker:  # resting at the best price on our side
            px, filled = (bid if q > 0 else ask), abs(q)
        else:
            px, filled = vwap(books[sym]["asks"] if q > 0 else books[sym]["bids"], q)
        fee = abs(q) * (px if math.isfinite(px) else mid) * (maker_fee if maker else taker_fee)
        out.append(Leg(sym, label, q, mid, px, fee, maker, filled >= abs(q) * (1 - 1e-9)))
    return out


def _funding(legs: list[Leg], insts: dict | None) -> tuple[float, float]:
    """USDT paid at the next settlement and per day (longs pay a positive rate)."""
    if not insts:
        return math.nan, math.nan
    nxt = day = 0.0
    for leg in legs:
        inst = insts.get(leg.symbol) or {}
        rate = inst.get("funding_rate")
        if rate is None:
            return math.nan, math.nan
        paid = leg.qty * leg.mid * float(rate)
        nxt += paid
        day += paid * 24.0 / float(inst.get("funding_hours") or 8.0)
    return nxt, day


def preview_open(pair: dict, side: int, leverage: float, execution: str, books: dict | None, insts: dict | None,
                 taker_fee: float, maker_fee: float, stop_pct: float, catastrophe_pct: float | None,
                 max_hedge_error: float = 0.05) -> Preview:
    """``pair``: symbol_a/b, label_a/b, equity (pair cash), alpha, beta, sigma, z_entry, z_exit."""
    p = Preview()
    if not books or pair["symbol_a"] not in books or pair["symbol_b"] not in books:
        p.warning = NO_BOOK
        return p
    if pair.get("beta") is None or not pair.get("sigma"):
        p.error = "the pair has no spread fit yet (it appears after the bot evaluates a bar)"
        return p
    (bid_a, ask_a), (bid_b, ask_b) = _best(books[pair["symbol_a"]]), _best(books[pair["symbol_b"]])
    mid_a, mid_b = (bid_a + ask_a) / 2, (bid_b + ask_b) / 2
    p.z_buy, p.z_sell = executable_zscores(math.log(bid_a), math.log(ask_a), math.log(bid_b), math.log(ask_b),
                                           pair["alpha"], pair["beta"], pair["sigma"])
    p.z = p.z_buy if side > 0 else p.z_sell
    ze, zx = pair["z_entry"], pair["z_exit"]
    p.signal = (side > 0 and p.z < -ze) or (side < 0 and p.z > ze)
    try:
        qa, qb = target_quantities(pair["equity"], leverage, pair["beta"], mid_a, mid_b)
        la, lb = lot_rules((insts or {}).get(pair["symbol_a"])), lot_rules((insts or {}).get(pair["symbol_b"]))
        if la and lb:
            qa, qb = round_to_lots(qa, qb, mid_a, mid_b, la, lb, pair["beta"], max_hedge_error)
    except SizingError as exc:
        p.error = f"cannot be sized: {exc}"
        return p
    sa, sb = (qa, -qb) if side > 0 else (-qa, qb)
    p.legs = _legs(pair, books, insts, sa, sb, execution, taker_fee, maker_fee)
    eq = pair["equity"] - p.cost  # equity right after the fills, at mid
    p.stop_level = (1 - stop_pct) * eq if stop_pct else math.nan
    p.catastrophe_level = (1 - catastrophe_pct) * eq if catastrophe_pct else math.nan
    p.funding_next, p.funding_day = _funding(p.legs, insts)
    if not p.signal:
        opposite = (side > 0 and p.z > ze) or (side < 0 and p.z < -ze)
        p.notes.append(f"Against the model: Z {p.z:+.2f} signals the {'SHORT' if side > 0 else 'LONG'} side."
                       if opposite else f"No entry signal now (Z {p.z:+.2f}, threshold ±{ze:g}): a manual call.")
    if (side > 0 and p.z > -zx) or (side < 0 and p.z < zx):
        p.notes.append(f"Z is already past the exit level {'-' if side > 0 else '+'}{zx:g}: the strategy only exits "
                       "after Z crosses it again from the other side, or on a stop.")
    if not all(l.depth_ok for l in p.legs):
        p.notes.append("The order is larger than the 50 book levels shown: the fill estimate is incomplete.")
    if execution == "maker":
        p.notes.append("Maker-first: the larger leg rests post-only at the best price, every fill is hedged at "
                       "market, and whatever is left after the timeout goes at market.")
    return p


def preview_close(pair: dict, fraction: float, execution: str, books: dict | None, insts: dict | None,
                  taker_fee: float, maker_fee: float) -> Preview:
    """``pair``: symbol_a/b, label_a/b, qty_a/b, entry_a/b (the open position), cash."""
    p = Preview()
    if not books or pair["symbol_a"] not in books or pair["symbol_b"] not in books:
        p.warning = NO_BOOK
        return p
    qs = []
    for held, sym in ((pair["qty_a"], pair["symbol_a"]), (pair["qty_b"], pair["symbol_b"])):
        q = abs(held) * fraction
        rules = lot_rules((insts or {}).get(sym))
        if fraction < 0.999 and rules is not None:
            q = floor_to_step(q, rules.step)
            left = abs(held) - q
            if left > 1e-12 and left < max(rules.min_qty, rules.step) - 1e-12:
                p.error = "the rest would be below the exchange minimum; close 100% instead"
                return p
        if not q > 0:
            p.error = "this fraction is smaller than one lot; choose a larger one"
            return p
        qs.append(-math.copysign(q, held))
    p.legs = _legs(pair, books, insts, qs[0], qs[1], execution, taker_fee, maker_fee)
    gross_pnl = sum(-l.qty * (l.price - e) for l, e in zip(p.legs, (pair["entry_a"], pair["entry_b"])))
    p.realized = gross_pnl - p.fees
    p.remaining = (pair["qty_a"] + qs[0], pair["qty_b"] + qs[1])
    if not all(l.depth_ok for l in p.legs):
        p.notes.append("The order is larger than the 50 book levels shown: the fill estimate is incomplete.")
    if fraction < 0.999:
        p.notes.append("The position stays open with the same entry prices and stops; the stop now needs a "
                       "larger move in the spread, because the position is smaller.")
    return p


@dataclass
class AddPreview(Preview):
    gross_now: float = math.nan  # the open position's gross notional at mid
    lev_now: float = math.nan  # gross / pair equity
    lev_after: float = math.nan
    stop_room_now: float = math.nan  # USDT the position can lose before the stop
    stop_room_after: float = math.nan
    stop_pct_now: float = math.nan  # the same as % of the position: the adverse move that reaches the stop
    stop_pct_after: float = math.nan


def preview_add(pair: dict, fraction: float, execution: str, books: dict | None, insts: dict | None,
                taker_fee: float, maker_fee: float, cap: float) -> AddPreview:
    """``pair``: symbol_a/b, label_a/b, qty_a/b, entry_a/b, cash, stop_level (USDT pair equity).
    ``cap``: the highest gross / pair equity an addition may reach (the bot's rule)."""
    p = AddPreview()
    if not books or pair["symbol_a"] not in books or pair["symbol_b"] not in books:
        p.warning = NO_BOOK
        return p
    qs, mids = [], []
    for held, sym in ((pair["qty_a"], pair["symbol_a"]), (pair["qty_b"], pair["symbol_b"])):
        q = abs(held) * fraction
        mid = sum(_best(books[sym])) / 2
        rules = lot_rules((insts or {}).get(sym))
        if rules is not None:
            q = floor_to_step(q, rules.step)
            if q < rules.min_qty or q * mid < rules.min_notional:
                p.error = "this size is below the exchange minimum; choose a larger one"
                return p
        if not q > 0:
            p.error = "this fraction is smaller than one lot; choose a larger one"
            return p
        qs.append(math.copysign(q, held))
        mids.append(mid)
    p.legs = _legs(pair, books, insts, qs[0], qs[1], execution, taker_fee, maker_fee)
    held = (pair["qty_a"], pair["qty_b"])
    equity = pair["cash"] + sum(h * (m - e) for h, m, e in zip(held, mids, (pair["entry_a"], pair["entry_b"])))
    p.gross_now = sum(abs(h) * m for h, m in zip(held, mids))
    gross_after = sum(abs(h + q) * m for h, q, m in zip(held, qs, mids))
    p.lev_now, p.lev_after = p.gross_now / equity, gross_after / (equity - p.cost)
    if p.lev_after > cap * (1 + 1e-9):
        p.error = (f"the position would reach {p.lev_after:.2f}x the pair's equity; the cap for additions is "
                   f"{cap:g}x (risk.manual_add_max_leverage)")
        return p
    p.stop_level = pair["stop_level"]
    p.stop_room_now = equity - p.stop_level
    p.stop_room_after = p.stop_room_now - p.cost
    p.stop_pct_now = 100 * p.stop_room_now / p.gross_now
    p.stop_pct_after = 100 * p.stop_room_after / gross_after
    p.funding_next, p.funding_day = _funding(p.legs, insts)
    if not all(l.depth_ok for l in p.legs):
        p.notes.append("The order is larger than the 50 book levels shown: the fill estimate is incomplete.")
    p.notes.append("The stop and the catastrophe stop keep their USDT levels: on a larger position a smaller move "
                   "against you reaches them. Widen the stop in Exit plan if you want the same room in the spread.")
    p.notes.append("The entry prices become the average of the old and the new fills; it stays one trade.")
    if execution == "maker":
        p.notes.append("Maker-first: the larger leg rests post-only at the best price, every fill is hedged at "
                       "market, and whatever is left after the timeout goes at market.")
    return p
