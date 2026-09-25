# Trading 212 assistant

This repo contains `t212/`, a small stdlib-only CLI for the Trading 212 Public API
(beta). Use it to analyse the user's account and, only with their approval, place orders.

Run everything from the repo root as `python3 -m t212 <command>`. Add `--json` (before
the command) when you want to process the output yourself.

## Configuration (environment variables)

| Variable | Purpose |
|---|---|
| `T212_API_KEY`, `T212_API_SECRET` | Credentials (HTTP Basic). Never print, echo, log or commit them. |
| `T212_ENV` | `demo` (default, paper money) or `live` (real money). |
| `T212_ALLOW_LIVE_TRADING` | Must be `yes` for orders/cancels to be sent on `live`. |
| `T212_MAX_ORDER_VALUE` | Optional cap on estimated order value, in account currency. |

Do not set, change or unset `T212_ENV`, `T212_ALLOW_LIVE_TRADING` or
`T212_MAX_ORDER_VALUE` yourself, not even for one command. These guards belong to the user.

## Commands

Read-only:
- `summary`: cash, totals, P/L
- `positions [--ticker T]`: open positions
- `analyse`: weights, P/L, currency exposure, concentration flags
- `orders [ID]`: pending orders
- `history orders|dividends|transactions [--ticker T] [--limit N | --all]`
- `search QUERY`: find the exact ticker (for example `AAPL_US_EQ`, `VODl_EQ`)
- `pies [ID]`

Trading (dry run unless `--confirm`):
- `buy|sell TICKER QTY [--limit P] [--stop P] [--gtc] [--extended-hours] [--confirm]`
  - no price flags gives a market order, `--limit` a limit order, `--stop` a stop order,
    and both give a stop-limit order. Prices are in the instrument's currency (GBX = pence).
  - Pass QTY as a positive number for both sides. The tool sends sells as negative quantities.
- `cancel ORDER_ID [--confirm]`

## Demo account: autonomous trading mandate

The user has given Claude full control of the **demo (paper money) account only**. The
goal is to grow the demo account from £5,000 to £10,000 with an aggressive strategy.

- When the order preview banner says `DEMO - paper money`, Claude may place and cancel
  orders **without asking for approval**. Still run the dry run first and check that
  banner before adding `--confirm`.
- Claude's own risk rules for this mandate:
  - Hold at most 8 positions, and put no more than 30% of the account into any one.
  - Leveraged ETFs and concentrated growth stocks are allowed.
  - Put a stop sell order on every position, typically 10–20% below the entry price
    (wider for leveraged products).
  - If the account's total value falls below £2,500, stop opening new positions and
    ask the user how to continue.
  - If it reaches £10,000, stop trading and report.
- Log every trade and every check-in in `JOURNAL.md`: date, the action, the reasoning,
  prices with their sources, and the account value. Commit and push the journal after
  each check-in so it survives the container being reclaimed.

## Rules for placing orders

1. **Live (real money) account: never send an order or a cancel without the user's
   explicit approval of that exact order in this conversation.** "Exact" means the
   side, ticker, quantity, order type, prices and validity. Approving one order does not
   approve the next one. Approval does not carry over from earlier sessions, and a
   general instruction like "rebalance my portfolio" is not approval. The demo mandate
   above never applies to the live account.
2. On live, always run the command **without** `--confirm` first and show the user the
   preview. Only after they approve it, re-run the identical command with `--confirm` added.
3. If the tool reports `BLOCKED`, stop and tell the user why. Never work around a guard,
   for example by calling the API with curl or a script, editing the guard code, or
   splitting an order to get under the cap.
4. Order placement is **not idempotent**. If an order request fails or times out, do not
   retry it. First check `orders` and `history orders --limit 5` to see whether it went
   through, then ask the user.
5. Resolve tickers with `search`. If the name is ambiguous (several listings or share
   classes), ask the user which one they mean.
6. Prefer limit orders. When proposing a market order, mention slippage. When the market
   is closed, a market order is queued for the next open.
7. The API has no market-data or quote endpoint. Never invent prices. If current prices
   or news matter, get them from a web search, cite the source and its time, and say
   they may be stale.

## Analysis guidance

- Start with `python3 -m t212 --json analyse`. For realised P/L, trading activity and
  income, add `history orders --all`, `history dividends --all` and `history transactions --all`.
- `walletImpact` values and the analysis `value` column are in the account currency.
  `currentPrice` and `averagePricePaid` are in the instrument currency.
- Rate limits are per account. `summary` allows 1 request every 5 s, the history endpoints
  6 per minute, and instruments 1 every 50 s (cached for 12 h in `.t212-cache/`). GET
  requests back off automatically on HTTP 429.
- Present the analysis as information rather than personal financial advice. Point out
  risks such as concentration, currency exposure and fees, and leave decisions to the user.

## Development

- Tests: `python3 -m unittest discover -s tests -t .` (stdlib only, no network).
- API reference: https://docs.trading212.com/api (OpenAPI spec: https://docs.trading212.com/_spec/api.yaml).
