# Troubleshooting Guide

## Issue: Script Hangs When Fetching Positions

### Problem
Scripts using `client.ib.reqAccountUpdates(account)` would hang indefinitely and never return.

### Root Cause
The high-level API `IB.reqAccountUpdates()` is a **blocking method** that internally calls `_run()` and waits until timeout or completion. In certain network or TWS configurations, this can cause the script to hang indefinitely.

### Solution
Use the **low-level API** instead:

**Before (Hangs):**
```python
client.ib.reqAccountUpdates(account)  # Blocking call, can hang
time.sleep(5)
```

**After (Works):**
```python
# Subscribe using low-level API (non-blocking)
client.ib.client.reqAccountUpdates(True, account)

# Wait for data using ib.sleep() which processes events
client.ib.sleep(5)

# Unsubscribe
client.ib.client.reqAccountUpdates(False, account)
```

### Key Differences

1. **`client.ib.reqAccountUpdates(account)`** - High-level API
   - Blocking
   - Waits for completion
   - Can hang indefinitely

2. **`client.ib.client.reqAccountUpdates(True, account)`** - Low-level API
   - Non-blocking
   - Just starts the subscription
   - Returns immediately

3. **`client.ib.sleep(seconds)`** - Event processing sleep
   - Processes event queue while sleeping
   - Allows TWS to push data
   - Returns after specified time

### Updated Scripts
- `scripts/fetch_positions_with_greeks.py` - Enhanced with Greeks data and caching

### Testing
```bash
# Should complete in 20-25 seconds (includes Greeks fetching)
uv run scripts/fetch_positions_with_greeks.py

# With custom wait time
uv run scripts/fetch_positions_with_greeks.py --wait 3 --wait-greeks 10
```

### Additional Notes
- Default wait time is 5 seconds
- Increase wait time if you have many positions: `--wait 10`
- The script will automatically unsubscribe after fetching data
- All data export formats (CSV, JSON, Excel) work correctly

