"""Order construction and pre-trade safety checks (pure functions)."""

TIME_VALIDITY = ("DAY", "GOOD_TILL_CANCEL")


class OrderError(ValueError):
    """The order is malformed."""


def order_type_for(limit_price, stop_price):
    if limit_price is not None and stop_price is not None:
        return "stop_limit"
    if limit_price is not None:
        return "limit"
    if stop_price is not None:
        return "stop"
    return "market"


def build_order(side, ticker, quantity, limit_price=None, stop_price=None,
                time_validity="DAY", extended_hours=False):
    """Return (order_type, payload). The API encodes sells as negative quantity."""
    if side not in ("buy", "sell"):
        raise OrderError(f"side must be buy or sell, got {side!r}")
    if not ticker:
        raise OrderError("ticker is required")
    if quantity is None or quantity <= 0:
        raise OrderError("quantity must be a positive number (use `sell` to sell)")
    for label, price in (("limit", limit_price), ("stop", stop_price)):
        if price is not None and price <= 0:
            raise OrderError(f"{label} price must be positive")
    if time_validity not in TIME_VALIDITY:
        raise OrderError(f"time validity must be one of {TIME_VALIDITY}")

    order_type = order_type_for(limit_price, stop_price)
    if extended_hours and order_type != "market":
        raise OrderError("extended hours is only supported for market orders")
    signed_qty = quantity if side == "buy" else -quantity

    payload = {"ticker": ticker, "quantity": signed_qty}
    if order_type == "market":
        payload["extendedHours"] = bool(extended_hours)
    else:
        payload["timeValidity"] = time_validity
    if limit_price is not None:
        payload["limitPrice"] = limit_price
    if stop_price is not None:
        payload["stopPrice"] = stop_price
    return order_type, payload


def _fx_to_account(instrument_ccy, account_ccy, position):
    """Multiplier from one unit of instrument currency to account currency."""
    if position:
        qty = position.get("quantity") or 0
        price = position.get("currentPrice") or 0
        value = (position.get("walletImpact") or {}).get("currentValue")
        if qty and price and value:
            return value / (qty * price)
    if instrument_ccy and account_ccy:
        if instrument_ccy == account_ccy:
            return 1.0
        if instrument_ccy == "GBX" and account_ccy == "GBP":
            return 0.01
    return None


def estimate_value(quantity, limit_price, stop_price, position, instrument_ccy, account_ccy):
    """Estimate order notional in account currency.

    Returns (value, basis) or (None, reason). The public API has no quote
    endpoint, so market orders on instruments you don't hold can't be priced.
    """
    fx = _fx_to_account(instrument_ccy, account_ccy, position)
    ref = limit_price if limit_price is not None else stop_price
    if ref is not None:
        if fx is None:
            return None, f"cannot convert {instrument_ccy} to {account_ccy}"
        label = "limit" if limit_price is not None else "stop"
        return abs(quantity) * ref * fx, f"{label} price"
    if position and fx is not None and position.get("currentPrice"):
        return abs(quantity) * position["currentPrice"] * fx, "last price of held position"
    return None, "no price available (market order on an instrument not held)"


def pre_trade_checks(side, quantity, est_value, position, free_cash, max_order_value):
    """Return (blocking_problems, warnings)."""
    problems, warnings = [], []
    if side == "sell":
        available = (position or {}).get("quantityAvailableForTrading") or 0
        if not position:
            problems.append("you do not hold this instrument")
        elif quantity > available + 1e-9:
            problems.append(f"sell quantity {quantity:g} exceeds the {available:g} shares available to trade")
    if max_order_value is not None:
        if est_value is None:
            problems.append(
                f"T212_MAX_ORDER_VALUE={max_order_value:g} is set but this order's value can't be "
                "estimated; use a limit order (--limit PRICE) so it can be checked"
            )
        elif est_value > max_order_value:
            problems.append(f"estimated value {est_value:,.2f} exceeds T212_MAX_ORDER_VALUE={max_order_value:,.2f}")
    if side == "buy" and est_value is not None and free_cash is not None and est_value > free_cash:
        warnings.append(f"estimated value {est_value:,.2f} is more than free cash {free_cash:,.2f}")
    return problems, warnings
