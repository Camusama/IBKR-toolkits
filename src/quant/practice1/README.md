# Practice 1: IBKR progressive demos

All code assumes Python 3.10+ with `ib_async`, `pandas`, `yfinance` installed:

```bash
pip install ib_async pandas yfinance
```

Environment defaults (override via env vars):
- `IB_HOST=127.0.0.1`
- `IB_PORT=7497` (paper) or `7497` (live)
- `IB_CLIENT_ID` (pick any int, avoid clashes)
- `IB_SYMBOL` (default `AAPL`), `IB_EXCHANGE=SMART`, `IB_CURRENCY=USD`
- `YF_SYMBOL`, `YF_PERIOD` (e.g., `90d`), `YF_INTERVAL` (e.g., `5m`)

Run each demo from repo root:

```bash
python -m src.quant.practice1.demo1_connect_account
python -m src.quant.practice1.demo2_history_ma_backtest
python -m src.quant.practice1.demo3_offline_yf_backtest
python -m src.quant.practice1.demo4_stream_live_quotes
python -m src.quant.practice1.demo5_paper_order
```

## Demo overview
- `demo1_connect_account`: Connect and print account summary + positions (read-only).
- `demo2_history_ma_backtest`: Pull IB historical bars and run a simple MA crossover backtest.
- `demo3_offline_yf_backtest`: Offline MA crossover backtest using `yfinance`.
- `demo4_stream_live_quotes`: Subscribe to live quotes for a few seconds; prints bid/ask/last updates.
- `demo5_paper_order`: Paper order example (market by default; limit if `IB_ORDER_TYPE=LMT`). Cancels remaining open orders for cleanliness.

## Notes
- Ensure paper gateway is used while testing orders; confirm port/clientId match your gateway/TWS.
- IB historical data has pacing limits and requires data subscriptions.
- Add realistic fees/slippage before using any backtest for decision-making.

