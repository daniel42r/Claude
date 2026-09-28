# Demo trading journal

Goal: grow the Trading 212 demo account from £5,000 to £10,000 (aggressive mandate, see CLAUDE.md).

| Date (UK) | Action | Reasoning | Account value |
|---|---|---|---|
| 2026-09-25 | Mandate set up. Waiting for the user to reset the demo account to £5,000. | - | - |
| 2026-09-28 09:41 | Account reset confirmed: £5,000 cash, no positions. | Start of mandate. | £5,000.00 |
| 2026-09-28 09:43 | Limit buys at web-quoted prices: LQQ3 4 @ 30,400p, 3LUS 7 @ 13,700p (placed, didn't fill), 3SMC 7 @ £148 (rejected: `price-too-far`). Cancelled the two unfilled orders. | Quotes from Yahoo/HL/Investing were stale: real prices were about 12% higher for LQQ3 and about 37% lower for 3SMC. Lesson: probe with a 1-unit market order to find the live price. | - |
| 2026-09-28 09:45 | Bought 1 unit each at market to find prices, then topped up. **LQQ3** (Nasdaq-100 3×) 4 @ avg 33,910p. **3LUS** (S&P 500 3×) 7 @ avg 14,153p. **3SMC** (semiconductors 3×) 10 @ avg £92.71. | Aggressive core: 3× leveraged US growth exposure after the S&P +1.2% / Nasdaq +2.1% week to 25 Sep ([Yahoo Finance](https://finance.yahoo.com/markets/live/stock-market-today-friday-september-25-dow-sp-500-nasdaq-081738529.html)). Semis give the highest beta. US futures were slightly down Monday morning on Iran/Strait of Hormuz tensions ([Bloomberg](https://www.bloomberg.com/news/articles/2026-09-27/stock-market-today-dow-s-p-live-updates)). About 34% kept in cash for the US open. | £5,055.88 |
| 2026-09-28 09:47 | GTC stop sells: LQQ3 4 @ 25,430p, 3LUS 7 @ 10,615p, 3SMC 10 @ £69.50. | About 25% below average cost, roughly an 8% fall in the underlying index. | £5,055.88 |
