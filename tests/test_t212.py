import base64
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from t212 import cli
from t212.analysis import analyse
from t212.client import ApiError, Trading212Client
from t212.orders import OrderError, build_order, estimate_value, pre_trade_checks

AAPL = {"ticker": "AAPL_US_EQ", "name": "Apple", "currencyCode": "USD", "type": "STOCK", "shortName": "AAPL"}
VOD = {"ticker": "VODl_EQ", "name": "Vodafone", "currencyCode": "GBX", "type": "STOCK", "shortName": "VOD"}

SUMMARY = {
    "id": 1, "currency": "GBP", "totalValue": 1000.0,
    "cash": {"availableToTrade": 200.0, "reservedForOrders": 0.0, "inPies": 0.0},
    "investments": {"currentValue": 800.0, "totalCost": 700.0,
                    "unrealizedProfitLoss": 100.0, "realizedProfitLoss": 5.0},
}
AAPL_POS = {
    "instrument": {"ticker": "AAPL_US_EQ", "name": "Apple", "currency": "USD"},
    "quantity": 4.0, "quantityAvailableForTrading": 3.0, "quantityInPies": 1.0,
    "averagePricePaid": 150.0, "currentPrice": 200.0,
    # 4 * 200 USD = 800 USD -> 600 GBP, so fx 0.75
    "walletImpact": {"currency": "GBP", "currentValue": 600.0, "totalCost": 500.0,
                     "unrealizedProfitLoss": 100.0, "fxImpact": -3.0},
}
VOD_POS = {
    "instrument": {"ticker": "VODl_EQ", "name": "Vodafone", "currency": "GBX"},
    "quantity": 200.0, "quantityAvailableForTrading": 200.0, "currentPrice": 100.0,
    "averagePricePaid": 110.0,
    "walletImpact": {"currency": "GBP", "currentValue": 200.0, "totalCost": 220.0,
                     "unrealizedProfitLoss": -20.0, "fxImpact": 0.0},
}


class ClientTests(unittest.TestCase):
    def test_basic_auth_header(self):
        c = Trading212Client("key", "secret")
        self.assertEqual(c._auth, "Basic " + base64.b64encode(b"key:secret").decode())
        self.assertEqual(c.base_url, "https://demo.trading212.com")

    def test_legacy_key_without_secret(self):
        self.assertEqual(Trading212Client("legacy")._auth, "legacy")

    def test_from_env_defaults_to_demo(self):
        c = Trading212Client.from_env({"T212_API_KEY": "k", "T212_API_SECRET": "s"})
        self.assertEqual(c.env, "demo")
        c = Trading212Client.from_env({"T212_API_KEY": "k", "T212_ENV": "LIVE"})
        self.assertEqual(c.base_url, "https://live.trading212.com")

    def test_bad_env_rejected(self):
        with self.assertRaises(ValueError):
            Trading212Client("k", env="prod")

    def test_get_retries_on_429(self):
        c = Trading212Client("k", "s")
        calls = []

        def send(method, path, body=None):
            calls.append(method)
            if len(calls) < 3:
                raise ApiError(429, "rate limited")
            return {"ok": True}

        with mock.patch.object(c, "_send", side_effect=send), mock.patch("time.sleep"):
            self.assertEqual(c.get("/orders"), {"ok": True})
        self.assertEqual(len(calls), 3)

    def test_order_post_is_never_retried(self):
        c = Trading212Client("k", "s")
        with mock.patch.object(c, "_send", side_effect=ApiError(429, "x")) as send, \
                mock.patch("time.sleep"):
            with self.assertRaises(ApiError):
                c.place_order("market", {"ticker": "AAPL_US_EQ", "quantity": 1})
        self.assertEqual(send.call_count, 1)

    def test_pagination_follows_next_page_path(self):
        c = Trading212Client("k", "s")
        pages = {
            "/api/v0/equity/history/orders?limit=50": {"items": [1, 2], "nextPagePath": "/p2"},
            "/p2": {"items": [3], "nextPagePath": None},
        }
        with mock.patch.object(c, "_send", side_effect=lambda m, p, b=None: pages[p]):
            self.assertEqual(c.order_history(), [1, 2, 3])
            self.assertEqual(c.order_history(max_items=2), [1, 2])


class OrderBuildTests(unittest.TestCase):
    def test_market_buy(self):
        t, p = build_order("buy", "AAPL_US_EQ", 2)
        self.assertEqual(t, "market")
        self.assertEqual(p, {"ticker": "AAPL_US_EQ", "quantity": 2, "extendedHours": False})

    def test_sell_uses_negative_quantity(self):
        _, p = build_order("sell", "AAPL_US_EQ", 1.5, limit_price=210)
        self.assertEqual(p["quantity"], -1.5)
        self.assertEqual(p["limitPrice"], 210)
        self.assertEqual(p["timeValidity"], "DAY")

    def test_order_type_from_prices(self):
        self.assertEqual(build_order("buy", "X", 1, stop_price=5)[0], "stop")
        t, p = build_order("sell", "X", 1, limit_price=4, stop_price=5, time_validity="GOOD_TILL_CANCEL")
        self.assertEqual(t, "stop_limit")
        self.assertEqual(p["timeValidity"], "GOOD_TILL_CANCEL")

    def test_invalid_orders(self):
        for kwargs in ({"quantity": 0}, {"quantity": -1}, {"quantity": 1, "limit_price": -2},
                       {"quantity": 1, "limit_price": 2, "extended_hours": True}):
            with self.assertRaises(OrderError):
                build_order("buy", "X", **kwargs)
        with self.assertRaises(OrderError):
            build_order("short", "X", 1)


class EstimateAndCheckTests(unittest.TestCase):
    def test_estimate_from_held_position_uses_implied_fx(self):
        v, basis = estimate_value(2, None, None, AAPL_POS, "USD", "GBP")
        self.assertAlmostEqual(v, 2 * 200 * 0.75)
        self.assertIn("held", basis)

    def test_estimate_limit_price_in_pence(self):
        v, _ = estimate_value(100, 120, None, None, "GBX", "GBP")
        self.assertAlmostEqual(v, 120.0)

    def test_estimate_unknown_for_unheld_market_order(self):
        v, reason = estimate_value(1, None, None, None, "USD", "GBP")
        self.assertIsNone(v)
        self.assertIn("no price", reason)

    def test_sell_more_than_available_blocked(self):
        problems, _ = pre_trade_checks("sell", 4, 100, AAPL_POS, 200, None)
        self.assertTrue(any("exceeds" in p for p in problems))
        problems, _ = pre_trade_checks("sell", 3, 100, AAPL_POS, 200, None)
        self.assertEqual(problems, [])

    def test_sell_not_held_blocked(self):
        problems, _ = pre_trade_checks("sell", 1, None, None, 200, None)
        self.assertTrue(problems)

    def test_max_order_value(self):
        self.assertTrue(pre_trade_checks("buy", 1, 600, None, 1000, 500)[0])
        self.assertTrue(pre_trade_checks("buy", 1, None, None, 1000, 500)[0])
        self.assertEqual(pre_trade_checks("buy", 1, 400, None, 1000, 500)[0], [])

    def test_insufficient_cash_is_warning(self):
        problems, warnings = pre_trade_checks("buy", 1, 300, None, 200, None)
        self.assertEqual(problems, [])
        self.assertTrue(warnings)


class AnalysisTests(unittest.TestCase):
    def test_weights_and_flags(self):
        r = analyse(SUMMARY, [VOD_POS, AAPL_POS])
        self.assertEqual([p["ticker"] for p in r["positions"]], ["AAPL_US_EQ", "VODl_EQ"])
        self.assertAlmostEqual(r["positions"][0]["weightPct"], 60.0)
        self.assertAlmostEqual(r["positions"][1]["unrealizedPnlPct"], -20 / 220 * 100)
        self.assertAlmostEqual(r["cash"]["pctOfTotal"], 20.0)
        self.assertEqual(set(r["exposureByInstrumentCurrency"]), {"USD", "GBX"})
        self.assertTrue(any("AAPL_US_EQ" in w for w in r["warnings"]))
        self.assertEqual(r["bestPerformers"][0][0], "AAPL_US_EQ")
        self.assertEqual(r["worstPerformers"][0][0], "VODl_EQ")

    def test_empty_account(self):
        r = analyse({"currency": "GBP", "totalValue": 0, "cash": {}}, [])
        self.assertEqual(r["positionCount"], 0)
        self.assertEqual(r["warnings"], [])


class FakeClient:
    def __init__(self, env="demo", positions=None):
        self.env = env
        self._positions = positions if positions is not None else [AAPL_POS]
        self.placed = []
        self.cancelled = []

    def instruments(self):
        return [AAPL, VOD]

    def account_summary(self):
        return SUMMARY

    def positions(self, ticker=None):
        return [p for p in self._positions if ticker in (None, p["instrument"]["ticker"])]

    def place_order(self, order_type, payload):
        self.placed.append((order_type, payload))
        return {"id": 99, "status": "NEW"}

    def pending_order(self, order_id):
        return {"id": order_id, "side": "BUY", "quantity": 1, "ticker": "AAPL_US_EQ",
                "type": "LIMIT", "status": "NEW"}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)


class CliTradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patches = [
            mock.patch.object(cli, "CACHE_DIR", os.path.join(self.tmp.name, "cache")),
            mock.patch.object(cli, "ORDER_LOG", os.path.join(self.tmp.name, "orders.jsonl")),
            mock.patch.dict(os.environ, {}, clear=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        for k in ("T212_ALLOW_LIVE_TRADING", "T212_MAX_ORDER_VALUE"):
            os.environ.pop(k, None)

    def run_cli(self, client, argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.Trading212Client, "from_env", return_value=client), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_by_default(self):
        c = FakeClient()
        code, out, _ = self.run_cli(c, ["buy", "AAPL_US_EQ", "1"])
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out)
        self.assertEqual(c.placed, [])

    def test_confirm_places_and_logs(self):
        c = FakeClient()
        code, out, _ = self.run_cli(c, ["sell", "AAPL_US_EQ", "2", "--limit", "250", "--confirm"])
        self.assertEqual(code, 0)
        self.assertEqual(c.placed, [("limit", {"ticker": "AAPL_US_EQ", "quantity": -2.0,
                                               "timeValidity": "DAY", "limitPrice": 250.0})])
        with open(cli.ORDER_LOG) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["response"]["id"], 99)

    def test_live_trading_locked_without_opt_in(self):
        c = FakeClient(env="live")
        code, out, _ = self.run_cli(c, ["buy", "AAPL_US_EQ", "1", "--confirm"])
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("LIVE", out)
        self.assertEqual(c.placed, [])
        code, _, _ = self.run_cli(c, ["cancel", "5", "--confirm"])
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(c.cancelled, [])

    def test_live_trading_with_opt_in(self):
        os.environ["T212_ALLOW_LIVE_TRADING"] = "yes"
        c = FakeClient(env="live")
        code, _, _ = self.run_cli(c, ["buy", "AAPL_US_EQ", "1", "--confirm"])
        self.assertEqual(code, 0)
        self.assertEqual(len(c.placed), 1)

    def test_unknown_ticker_refused(self):
        c = FakeClient()
        code, _, err = self.run_cli(c, ["buy", "AAPL", "1", "--confirm"])
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("search", err)
        self.assertEqual(c.placed, [])

    def test_oversell_refused_even_with_confirm(self):
        c = FakeClient()
        code, out, _ = self.run_cli(c, ["sell", "AAPL_US_EQ", "10", "--confirm"])
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("BLOCKED", out)
        self.assertEqual(c.placed, [])

    def test_max_order_value_refuses(self):
        os.environ["T212_MAX_ORDER_VALUE"] = "100"
        c = FakeClient()
        code, _, _ = self.run_cli(c, ["buy", "AAPL_US_EQ", "1", "--confirm"])  # ~150 GBP
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(c.placed, [])

    def test_json_preview(self):
        c = FakeClient()
        code, out, _ = self.run_cli(c, ["--json", "buy", "VODl_EQ", "50", "--limit", "100"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["status"], "dry_run")
        self.assertAlmostEqual(data["estimatedValue"], 50.0)

    def test_search(self):
        hits = cli.search_instruments([AAPL, VOD], "aapl")
        self.assertEqual(hits[0]["ticker"], "AAPL_US_EQ")
        self.assertEqual(cli.search_instruments([AAPL, VOD], "voda")[0]["ticker"], "VODl_EQ")


if __name__ == "__main__":
    unittest.main()
