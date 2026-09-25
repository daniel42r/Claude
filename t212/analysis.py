"""Portfolio analysis over raw Trading 212 API responses (pure functions)."""

CONCENTRATION_WARN = 0.20  # single position above this share of the account


def _num(x):
    return float(x) if isinstance(x, (int, float)) else 0.0


def position_rows(positions):
    rows = []
    for p in positions:
        inst = p.get("instrument") or {}
        wallet = p.get("walletImpact") or {}
        cost = _num(wallet.get("totalCost"))
        pnl = _num(wallet.get("unrealizedProfitLoss"))
        rows.append({
            "ticker": inst.get("ticker") or p.get("ticker"),
            "name": inst.get("name"),
            "instrumentCurrency": inst.get("currency"),
            "quantity": _num(p.get("quantity")),
            "quantityAvailableForTrading": _num(p.get("quantityAvailableForTrading")),
            "quantityInPies": _num(p.get("quantityInPies")),
            "averagePricePaid": _num(p.get("averagePricePaid")),
            "currentPrice": _num(p.get("currentPrice")),
            # walletImpact values are in the account's primary currency
            "value": _num(wallet.get("currentValue")),
            "cost": cost,
            "unrealizedPnl": pnl,
            "unrealizedPnlPct": (pnl / cost * 100) if cost else None,
            "fxImpact": _num(wallet.get("fxImpact")),
            "openedAt": p.get("createdAt"),
        })
    return rows


def analyse(summary, positions):
    cash = summary.get("cash") or {}
    inv = summary.get("investments") or {}
    rows = position_rows(positions)
    invested = sum(r["value"] for r in rows)
    total = _num(summary.get("totalValue")) or (invested + _num(cash.get("availableToTrade")))

    for r in rows:
        r["weightPct"] = (r["value"] / total * 100) if total else None
    rows.sort(key=lambda r: r["value"], reverse=True)

    by_ccy = {}
    for r in rows:
        ccy = r["instrumentCurrency"] or "?"
        by_ccy[ccy] = by_ccy.get(ccy, 0.0) + r["value"]

    free_cash = _num(cash.get("availableToTrade"))
    warnings = []
    for r in rows:
        if r["weightPct"] is not None and r["weightPct"] > CONCENTRATION_WARN * 100:
            warnings.append(f"{r['ticker']} is {r['weightPct']:.1f}% of the account")
    top5 = sum(r["weightPct"] or 0 for r in rows[:5])
    if len(rows) > 5 and top5 > 60:
        warnings.append(f"top 5 positions are {top5:.1f}% of the account")

    with_pct = [r for r in rows if r["unrealizedPnlPct"] is not None]
    by_pct = sorted(with_pct, key=lambda r: r["unrealizedPnlPct"], reverse=True)

    return {
        "currency": summary.get("currency"),
        "accountId": summary.get("id"),
        "totalValue": total,
        "cash": {
            "availableToTrade": free_cash,
            "reservedForOrders": _num(cash.get("reservedForOrders")),
            "inPies": _num(cash.get("inPies")),
            "pctOfTotal": (free_cash / total * 100) if total else None,
        },
        "investments": {
            "currentValue": _num(inv.get("currentValue")) or invested,
            "totalCost": _num(inv.get("totalCost")),
            "unrealizedPnl": _num(inv.get("unrealizedProfitLoss")),
            "realizedPnl": _num(inv.get("realizedProfitLoss")),
        },
        "positionCount": len(rows),
        "top5WeightPct": top5,
        "exposureByInstrumentCurrency": {
            k: {"value": v, "pctOfInvested": (v / invested * 100) if invested else None}
            for k, v in sorted(by_ccy.items(), key=lambda kv: kv[1], reverse=True)
        },
        "bestPerformers": [(r["ticker"], r["unrealizedPnlPct"]) for r in by_pct[:3]],
        "worstPerformers": [(r["ticker"], r["unrealizedPnlPct"]) for r in by_pct[::-1][:3]],
        "warnings": warnings,
        "positions": rows,
    }
