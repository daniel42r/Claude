"""Command-line interface: `python3 -m t212 <command>`."""

import argparse
import datetime as dt
import json
import os
import sys
import time

from .analysis import analyse
from .client import ApiError, Trading212Client
from .orders import OrderError, build_order, estimate_value, pre_trade_checks

CACHE_DIR = os.environ.get("T212_CACHE_DIR", ".t212-cache")
ORDER_LOG = os.environ.get("T212_ORDER_LOG", "t212-orders.jsonl")
INSTRUMENT_CACHE_TTL = 12 * 3600

EXIT_API_ERROR = 1
EXIT_REFUSED = 2


# -- output helpers ----------------------------------------------------------

def _fmt(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    return str(v)


def print_table(rows, cols):
    """rows: list of dicts; cols: list of (key, header)."""
    if not rows:
        print("(none)")
        return
    cells = [[_fmt(r.get(k)) for k, _ in cols] for r in rows]
    widths = [max(len(h), *(len(c[i]) for c in cells)) for i, (_, h) in enumerate(cols)]
    print("  ".join(h.ljust(w) for (_, h), w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for c in cells:
        print("  ".join(x.ljust(w) for x, w in zip(c, widths)))


def emit(args, data, human):
    if args.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        human(data)


# -- instrument metadata (cached: the endpoint allows 1 request / 50s) -------

def load_instruments(client, refresh=False):
    path = os.path.join(CACHE_DIR, f"instruments-{client.env}.json")
    if not refresh and os.path.exists(path) and time.time() - os.path.getmtime(path) < INSTRUMENT_CACHE_TTL:
        with open(path) as f:
            return json.load(f)
    data = client.instruments()
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def search_instruments(instruments, query, limit=15):
    q = query.strip().lower()

    def rank(i):
        t = (i.get("ticker") or "").lower()
        s = (i.get("shortName") or "").lower()
        n = (i.get("name") or "").lower()
        isin = (i.get("isin") or "").lower()
        if q in (t, s, isin):
            return 0
        if t.startswith(q + "_") or s.startswith(q):
            return 1
        if n.startswith(q):
            return 2
        if q in n or q in t:
            return 3
        return None

    hits = [(r, i) for i in instruments if (r := rank(i)) is not None]
    hits.sort(key=lambda x: (x[0], x[1].get("type") != "STOCK", x[1].get("ticker", "")))
    return [i for _, i in hits[:limit]]


# -- read-only commands -------------------------------------------------------

def cmd_summary(client, args):
    def human(s):
        cash = s.get("cash") or {}
        inv = s.get("investments") or {}
        ccy = s.get("currency", "")
        print(f"Account {s.get('id')} ({client.env.upper()}) - values in {ccy}")
        print(f"  Total value          {_fmt(s.get('totalValue'))}")
        print(f"  Cash available       {_fmt(cash.get('availableToTrade'))}")
        print(f"  Cash reserved/orders {_fmt(cash.get('reservedForOrders'))}")
        print(f"  Cash in pies         {_fmt(cash.get('inPies'))}")
        print(f"  Investments value    {_fmt(inv.get('currentValue'))}")
        print(f"  Investments cost     {_fmt(inv.get('totalCost'))}")
        print(f"  Unrealised P/L       {_fmt(inv.get('unrealizedProfitLoss'))}")
        print(f"  Realised P/L         {_fmt(inv.get('realizedProfitLoss'))}")
    emit(args, client.account_summary(), human)


def cmd_positions(client, args):
    from .analysis import position_rows
    rows = position_rows(client.positions(args.ticker))
    rows.sort(key=lambda r: r["value"], reverse=True)
    emit(args, rows, lambda rs: print_table(rs, [
        ("ticker", "Ticker"), ("name", "Name"), ("quantity", "Qty"),
        ("averagePricePaid", "Avg price"), ("currentPrice", "Price"),
        ("instrumentCurrency", "Ccy"), ("value", "Value"),
        ("unrealizedPnl", "P/L"), ("unrealizedPnlPct", "P/L %"),
    ]))


def cmd_analyse(client, args):
    summary = client.account_summary()
    report = analyse(summary, client.positions())

    def human(r):
        ccy = r["currency"]
        print(f"Portfolio analysis ({client.env.upper()}) - values in {ccy}")
        print(f"  Total value      {_fmt(r['totalValue'])}")
        print(f"  Free cash        {_fmt(r['cash']['availableToTrade'])} ({_fmt(r['cash']['pctOfTotal'], 1)}%)")
        print(f"  Invested         {_fmt(r['investments']['currentValue'])} across {r['positionCount']} positions")
        print(f"  Unrealised P/L   {_fmt(r['investments']['unrealizedPnl'])}")
        print(f"  Realised P/L     {_fmt(r['investments']['realizedPnl'])}")
        print(f"  Top-5 weight     {_fmt(r['top5WeightPct'], 1)}%")
        print("\nExposure by instrument currency:")
        for k, v in r["exposureByInstrumentCurrency"].items():
            print(f"  {k:<5} {_fmt(v['value']):>14}  {_fmt(v['pctOfInvested'], 1)}%")
        print("\nPositions:")
        print_table(r["positions"], [
            ("ticker", "Ticker"), ("name", "Name"), ("value", "Value"),
            ("weightPct", "Weight %"), ("unrealizedPnl", "P/L"), ("unrealizedPnlPct", "P/L %"),
        ])
        if r["warnings"]:
            print("\nFlags:")
            for w in r["warnings"]:
                print(f"  ! {w}")
    emit(args, report, human)


def cmd_orders(client, args):
    data = client.pending_order(args.id) if args.id else client.pending_orders()
    rows = data if isinstance(data, list) else [data]
    emit(args, data, lambda _: print_table(rows, [
        ("id", "ID"), ("ticker", "Ticker"), ("side", "Side"), ("type", "Type"),
        ("quantity", "Qty"), ("filledQuantity", "Filled"), ("limitPrice", "Limit"),
        ("stopPrice", "Stop"), ("status", "Status"), ("timeInForce", "Validity"),
        ("createdAt", "Created"),
    ]))


def cmd_history(client, args):
    n = None if args.all else args.limit
    if args.kind == "orders":
        items = client.order_history(args.ticker, max_items=n)
        rows = []
        for h in items:
            o, f = h.get("order") or {}, h.get("fill") or {}
            rows.append({
                "date": f.get("filledAt") or o.get("createdAt"), "ticker": o.get("ticker"),
                "side": o.get("side"), "type": o.get("type"), "status": o.get("status"),
                "qty": f.get("quantity", o.get("quantity")), "price": f.get("price"),
                "net": (f.get("walletImpact") or {}).get("netValue"),
                "realisedPnl": (f.get("walletImpact") or {}).get("realisedProfitLoss"),
            })
        cols = [("date", "Date"), ("ticker", "Ticker"), ("side", "Side"), ("type", "Type"),
                ("status", "Status"), ("qty", "Qty"), ("price", "Price"), ("net", "Net"),
                ("realisedPnl", "Realised P/L")]
    elif args.kind == "dividends":
        items = client.dividends(args.ticker, max_items=n)
        rows = items
        cols = [("paidOn", "Paid on"), ("ticker", "Ticker"), ("quantity", "Qty"),
                ("grossAmountPerShare", "Gross/share"), ("amount", "Amount"), ("type", "Type")]
    else:
        items = client.transactions(max_items=n)
        rows = items
        cols = [("dateTime", "Date"), ("type", "Type"), ("amount", "Amount"), ("currency", "Ccy")]
    emit(args, items, lambda _: print_table(rows, cols))


def cmd_search(client, args):
    hits = search_instruments(load_instruments(client, args.refresh), args.query, args.limit)
    emit(args, hits, lambda rs: print_table(rs, [
        ("ticker", "Ticker"), ("shortName", "Symbol"), ("name", "Name"),
        ("type", "Type"), ("currencyCode", "Ccy"), ("isin", "ISIN"),
    ]))


def cmd_pies(client, args):
    data = client.pie(args.id) if args.id else client.pies()
    emit(args, data, lambda d: print(json.dumps(d, indent=2, default=str)))


# -- trading commands ---------------------------------------------------------

def live_trading_allowed(client):
    if client.env != "live":
        return True
    return os.environ.get("T212_ALLOW_LIVE_TRADING", "").strip().lower() in ("1", "true", "yes")


def _max_order_value():
    raw = os.environ.get("T212_MAX_ORDER_VALUE", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise SystemExit(f"T212_MAX_ORDER_VALUE must be a number, got {raw!r}")


def log_order(entry):
    entry = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), **entry}
    with open(ORDER_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def cmd_trade(client, args):
    side = args.command
    try:
        order_type, payload = build_order(
            side, args.ticker, args.quantity, args.limit, args.stop,
            "GOOD_TILL_CANCEL" if args.gtc else "DAY", args.extended_hours,
        )
    except OrderError as e:
        print(f"Invalid order: {e}", file=sys.stderr)
        return EXIT_REFUSED

    instruments = load_instruments(client)
    inst = next((i for i in instruments if i.get("ticker") == args.ticker), None)
    if inst is None:
        print(f"Unknown ticker {args.ticker!r}. Find the exact ticker with: "
              f"python3 -m t212 search <name>", file=sys.stderr)
        return EXIT_REFUSED

    summary = client.account_summary()
    account_ccy = summary.get("currency")
    free_cash = (summary.get("cash") or {}).get("availableToTrade")
    held = client.positions(args.ticker)
    position = held[0] if held else None

    est, basis = estimate_value(args.quantity, args.limit, args.stop, position,
                                inst.get("currencyCode"), account_ccy)
    problems, warnings = pre_trade_checks(side, args.quantity, est, position, free_cash, _max_order_value())
    if not live_trading_allowed(client):
        problems.append("live trading is disabled; set T212_ALLOW_LIVE_TRADING=yes to enable it")

    preview = {
        "environment": client.env.upper(),
        "side": side.upper(),
        "orderType": order_type.upper(),
        "ticker": args.ticker,
        "name": inst.get("name"),
        "instrumentCurrency": inst.get("currencyCode"),
        "quantity": args.quantity,
        "limitPrice": args.limit,
        "stopPrice": args.stop,
        "timeValidity": payload.get("timeValidity"),
        "extendedHours": payload.get("extendedHours"),
        "estimatedValue": est,
        "estimateBasis": basis,
        "accountCurrency": account_ccy,
        "freeCash": free_cash,
        "currentlyHeld": (position or {}).get("quantity", 0),
        "payload": payload,
        "warnings": warnings,
        "blockingProblems": problems,
    }

    if not args.json:
        banner = "LIVE - REAL MONEY" if client.env == "live" else "DEMO - paper money"
        print(f"=== Order preview [{banner}] ===")
        print(f"  {side.upper()} {args.quantity:g} x {args.ticker} ({inst.get('name')})")
        print(f"  Type        {order_type.upper()}"
              + (f"  limit {args.limit:g}" if args.limit is not None else "")
              + (f"  stop {args.stop:g}" if args.stop is not None else "")
              + (f"  {payload['timeValidity']}" if "timeValidity" in payload else "")
              + ("  extended hours" if payload.get("extendedHours") else ""))
        est_txt = f"{est:,.2f} {account_ccy} ({basis})" if est is not None else f"unknown - {basis}"
        print(f"  Est. value  {est_txt}")
        print(f"  Free cash   {_fmt(free_cash)} {account_ccy}")
        print(f"  Holding     {_fmt(float((position or {}).get('quantity', 0)), 4)}")
        if order_type == "market":
            print("  Note        market orders can fill at a different price (slippage)")
        for w in warnings:
            print(f"  WARNING     {w}")
        for p in problems:
            print(f"  BLOCKED     {p}")

    if problems:
        if args.json:
            print(json.dumps({"status": "refused", **preview}, indent=2, default=str))
        else:
            print("\nNot sent.")
        return EXIT_REFUSED

    if not args.confirm:
        if args.json:
            print(json.dumps({"status": "dry_run", **preview}, indent=2, default=str))
        else:
            print("\nDRY RUN - nothing was sent. Re-run with --confirm to place this order.")
        return 0

    try:
        result = client.place_order(order_type, payload)
    except ApiError as e:
        log_order({"env": client.env, "action": "place", "type": order_type,
                   "payload": payload, "error": str(e), "body": e.body})
        raise
    log_order({"env": client.env, "action": "place", "type": order_type,
               "payload": payload, "response": result})
    if args.json:
        print(json.dumps({"status": "sent", **preview, "response": result}, indent=2, default=str))
    else:
        print(f"\nSent. Order id {result.get('id')} status {result.get('status')}")
    return 0


def cmd_cancel(client, args):
    order = client.pending_order(args.id)
    print(f"Cancel [{client.env.upper()}] order {order.get('id')}: {order.get('side')} "
          f"{order.get('quantity')} {order.get('ticker')} {order.get('type')} ({order.get('status')})")
    if not live_trading_allowed(client):
        print("BLOCKED: live trading is disabled; set T212_ALLOW_LIVE_TRADING=yes to enable it")
        return EXIT_REFUSED
    if not args.confirm:
        print("DRY RUN - nothing was sent. Re-run with --confirm to cancel.")
        return 0
    try:
        client.cancel_order(args.id)
    except ApiError as e:
        log_order({"env": client.env, "action": "cancel", "orderId": args.id, "error": str(e)})
        raise
    log_order({"env": client.env, "action": "cancel", "orderId": args.id})
    print("Cancellation requested.")
    return 0


# -- entry point ---------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="python3 -m t212", description="Trading 212 account tool")
    p.add_argument("--json", action="store_true", help="machine-readable JSON output")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("summary", help="cash and account totals")
    sp = sub.add_parser("positions", help="open positions")
    sp.add_argument("--ticker")
    sub.add_parser("analyse", aliases=["analyze"], help="portfolio breakdown, weights, P/L, flags")
    sp = sub.add_parser("orders", help="pending orders")
    sp.add_argument("id", nargs="?", type=int)
    sp = sub.add_parser("history", help="past orders, dividends or transactions")
    sp.add_argument("kind", choices=["orders", "dividends", "transactions"])
    sp.add_argument("--ticker")
    sp.add_argument("--limit", type=int, default=50, help="max items (default 50)")
    sp.add_argument("--all", action="store_true", help="fetch every page")
    sp = sub.add_parser("search", help="find an instrument's ticker")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=15)
    sp.add_argument("--refresh", action="store_true", help="re-download instrument list")
    sp = sub.add_parser("pies", help="list pies, or show one")
    sp.add_argument("id", nargs="?", type=int)

    for side in ("buy", "sell"):
        sp = sub.add_parser(side, help=f"{side} shares (dry run unless --confirm)")
        sp.add_argument("ticker", help="exact Trading 212 ticker, e.g. AAPL_US_EQ")
        sp.add_argument("quantity", type=float, help="number of shares (positive)")
        sp.add_argument("--limit", type=float, help="limit price (instrument currency)")
        sp.add_argument("--stop", type=float, help="stop price (instrument currency)")
        sp.add_argument("--gtc", action="store_true", help="good till cancelled (default: DAY)")
        sp.add_argument("--extended-hours", action="store_true", help="market orders only")
        sp.add_argument("--confirm", action="store_true", help="actually send the order")
    sp = sub.add_parser("cancel", help="cancel a pending order (dry run unless --confirm)")
    sp.add_argument("id", type=int)
    sp.add_argument("--confirm", action="store_true")
    return p


COMMANDS = {
    "summary": cmd_summary, "positions": cmd_positions, "analyse": cmd_analyse,
    "analyze": cmd_analyse, "orders": cmd_orders, "history": cmd_history,
    "search": cmd_search, "pies": cmd_pies, "buy": cmd_trade, "sell": cmd_trade,
    "cancel": cmd_cancel,
}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        client = Trading212Client.from_env()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return EXIT_API_ERROR
    try:
        return COMMANDS[args.command](client, args) or 0
    except ApiError as e:
        print(f"Trading 212 API error: {e}", file=sys.stderr)
        return EXIT_API_ERROR
