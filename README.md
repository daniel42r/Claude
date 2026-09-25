# Claude + Trading 212

A small command-line tool that lets Claude read your Trading 212 account, analyse it, and
place orders **after you approve each one**. It uses the official
[Trading 212 Public API](https://docs.trading212.com/api) (beta) and needs nothing beyond
Python 3.9+.

> The Trading 212 API only works with **Invest** and **Stocks ISA** accounts. Orders are
> placed by share quantity, in your account's primary currency.

## 1. Create an API key

In the Trading 212 app, open **Settings → API (Beta)** and generate a key. You get an
**API key** and an **API secret**. See
[Trading 212's guide](https://helpcentre.trading212.com/hc/en-us/articles/14584770928157-Trading-212-API-key).

- **Start with a Practice (demo) account key.** Keys are tied to the account mode they
  were created in: a Practice key only works with `T212_ENV=demo`, and a real-money key
  only works with `T212_ENV=live`.
- **Give the key only the permissions you need.** For analysis only, choose account,
  portfolio, orders read, history and metadata (plus pies read if you use pies). Add
  **orders execute** only if you want Claude to trade.
- **IP restriction:** cloud sessions don't have a fixed IP address, so a key restricted to
  specific IPs will fail with `401` there.

## 2. Add the credentials to your Claude Code environment

Don't paste keys into the chat or commit them. Add them as environment variables:

| Variable | Value |
|---|---|
| `T212_API_KEY` | your API key |
| `T212_API_SECRET` | your API secret |
| `T212_ENV` | `demo` (default) or `live` |
| `T212_ALLOW_LIVE_TRADING` | *(optional)* `yes` to allow orders on `live`. Leave unset for read-only live use. |
| `T212_MAX_ORDER_VALUE` | *(optional)* for example `500`: refuse any order whose estimated value is above this amount (account currency) |

In Claude Code on the web, open the cloud environment menu in the session's title bar and
choose **Edit**. Add the variables there, then start a **new session**, because variables
are read when a session starts. Run locally, export them in your shell instead.

## 3. Use it

Ask Claude things like:

- "Analyse my Trading 212 portfolio: concentration, currency exposure, winners and losers."
- "How much have I received in dividends this year?"
- "Buy 2 shares of Apple with a limit at $210, good till cancelled."

Or run the commands yourself:

```bash
python3 -m t212 summary
python3 -m t212 analyse
python3 -m t212 positions
python3 -m t212 history orders --limit 20
python3 -m t212 history dividends --all
python3 -m t212 search "apple"
python3 -m t212 buy AAPL_US_EQ 2 --limit 210 --gtc      # preview only
python3 -m t212 buy AAPL_US_EQ 2 --limit 210 --gtc --confirm
python3 -m t212 sell VODl_EQ 100                         # market sell preview
python3 -m t212 orders
python3 -m t212 cancel 123456789 --confirm
```

Add `--json` before the command for machine-readable output.

## Safety design

Placing an order takes several deliberate steps:

1. **Dry run by default.** `buy`, `sell` and `cancel` only print a preview unless you add
   `--confirm`.
2. **Demo by default.** Nothing touches real money unless `T212_ENV=live`.
3. **Live trading lock.** On `live`, orders and cancels are refused unless you set
   `T212_ALLOW_LIVE_TRADING=yes`.
4. **Pre-trade checks.** The tool refuses an order when:
   - the ticker doesn't exist,
   - a sell is for more shares than you hold or can trade (for example, shares locked in pies),
   - or the order is over `T212_MAX_ORDER_VALUE`.

   With a cap set, a market buy of a stock you don't own is also refused, because the API
   has no price feed to check it against. Use a limit order instead.
5. **No automatic retries on orders.** The beta API can create duplicate orders if a
   request is repeated, so order requests are sent exactly once.
6. **Claude asks first.** [`CLAUDE.md`](CLAUDE.md) tells Claude to show you the preview and
   get your explicit approval of each specific order before sending it.
   [`.claude/settings.json`](.claude/settings.json) also makes Claude Code prompt you
   before any `buy`/`sell`/`cancel` command runs.
7. **Order log.** Every order sent is appended to `t212-orders.jsonl`, which is gitignored.
   In a cloud session this file disappears with the container, so Trading 212's own
   history remains the source of truth.

Claude's analysis is information, not financial advice. Investing involves risk, and
you're responsible for the orders you approve.

## Development

```bash
python3 -m unittest discover -s tests -t .
```
