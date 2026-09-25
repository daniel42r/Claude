"""Minimal Trading 212 Public API client (stdlib only).

API reference: https://docs.trading212.com/api
"""

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URLS = {
    "demo": "https://demo.trading212.com",
    "live": "https://live.trading212.com",
}
API_PREFIX = "/api/v0/equity"


class ApiError(Exception):
    def __init__(self, status, message, body=None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.body = body


# Hints for the error codes documented in the OpenAPI spec.
_STATUS_HINTS = {
    401: "bad API key/secret, or the key is restricted to other IP addresses",
    403: "the API key is missing the permission (scope) this call needs",
    408: "request timed out on Trading 212's side",
    429: "rate limited",
}


class Trading212Client:
    def __init__(self, api_key, api_secret=None, env="demo", timeout=30, max_get_retries=3):
        if env not in BASE_URLS:
            raise ValueError(f"env must be one of {sorted(BASE_URLS)}, got {env!r}")
        if not api_key:
            raise ValueError("an API key is required")
        self.env = env
        self.base_url = BASE_URLS[env]
        self.timeout = timeout
        self.max_get_retries = max_get_retries
        if api_secret:
            token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
            self._auth = f"Basic {token}"
        else:
            # Older keys were a single value sent as-is in the Authorization header.
            self._auth = api_key

    @classmethod
    def from_env(cls, environ=None):
        environ = os.environ if environ is None else environ
        key = environ.get("T212_API_KEY")
        if not key:
            raise ValueError(
                "T212_API_KEY is not set. Add T212_API_KEY and T212_API_SECRET "
                "as environment variables (see README.md)."
            )
        return cls(
            api_key=key,
            api_secret=environ.get("T212_API_SECRET"),
            env=environ.get("T212_ENV", "demo").strip().lower(),
        )

    # -- transport ---------------------------------------------------------

    def _send(self, method, path, body=None):
        url = path if path.startswith("http") else self.base_url + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", self._auth)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace").strip()
            try:
                parsed = json.loads(raw) if raw else None
            except ValueError:
                parsed = raw
            reset = e.headers.get("x-ratelimit-reset") if e.headers else None
            err = ApiError(e.code, _describe(e.code, parsed), parsed)
            err.ratelimit_reset = reset
            raise err from None

    def request(self, method, path, body=None):
        # Order placement is NOT idempotent in the beta API, so only GETs are
        # retried; a retried POST could place a duplicate order.
        attempts = self.max_get_retries + 1 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                return self._send(method, path, body)
            except ApiError as e:
                if e.status != 429 or attempt == attempts - 1:
                    raise
                time.sleep(_backoff_seconds(getattr(e, "ratelimit_reset", None), attempt))

    def get(self, path):
        return self.request("GET", API_PREFIX + path)

    def paginate(self, path, limit=50, max_items=None):
        """Follow nextPagePath until exhausted or max_items reached."""
        sep = "&" if "?" in path else "?"
        next_path = f"{API_PREFIX}{path}{sep}limit={limit}"
        items = []
        while next_path:
            page = self.request("GET", next_path)
            items.extend(page.get("items", []))
            if max_items is not None and len(items) >= max_items:
                return items[:max_items]
            next_path = page.get("nextPagePath")
        return items

    # -- read-only endpoints ----------------------------------------------

    def account_summary(self):
        return self.get("/account/summary")

    def positions(self, ticker=None):
        q = f"?ticker={urllib.parse.quote(ticker)}" if ticker else ""
        return self.get(f"/positions{q}")

    def pending_orders(self):
        return self.get("/orders")

    def pending_order(self, order_id):
        return self.get(f"/orders/{int(order_id)}")

    def instruments(self):
        return self.get("/metadata/instruments")

    def exchanges(self):
        return self.get("/metadata/exchanges")

    def pies(self):
        return self.get("/pies")

    def pie(self, pie_id):
        return self.get(f"/pies/{int(pie_id)}")

    def order_history(self, ticker=None, max_items=None):
        q = f"?ticker={urllib.parse.quote(ticker)}" if ticker else ""
        return self.paginate(f"/history/orders{q}", max_items=max_items)

    def dividends(self, ticker=None, max_items=None):
        q = f"?ticker={urllib.parse.quote(ticker)}" if ticker else ""
        return self.paginate(f"/history/dividends{q}", max_items=max_items)

    def transactions(self, max_items=None):
        return self.paginate("/history/transactions", max_items=max_items)

    # -- trading endpoints -------------------------------------------------

    def place_order(self, order_type, payload):
        path = {
            "market": "/orders/market",
            "limit": "/orders/limit",
            "stop": "/orders/stop",
            "stop_limit": "/orders/stop_limit",
        }[order_type]
        return self.request("POST", API_PREFIX + path, payload)

    def cancel_order(self, order_id):
        return self.request("DELETE", f"{API_PREFIX}/orders/{int(order_id)}")


def _describe(status, parsed):
    detail = ""
    if isinstance(parsed, dict):
        detail = parsed.get("message") or parsed.get("code") or parsed.get("type") or ""
        if not detail:
            detail = json.dumps(parsed)
    elif parsed:
        detail = str(parsed)[:300]
    hint = _STATUS_HINTS.get(status, "")
    return " - ".join(x for x in (hint, detail) if x) or "request failed"


def _backoff_seconds(reset_header, attempt):
    if reset_header:
        try:
            wait = float(reset_header) - time.time()
            if 0 < wait <= 90:
                return wait + 0.5
        except ValueError:
            pass
    return min(2 ** (attempt + 1), 30)
