#!/usr/bin/env python3
"""Fetch IBKR positions data with Greeks

Get complete position and market data, including option Greeks when market is open.
This version subscribes to market data to fetch real-time Greeks for options.

Usage:
    python scripts/fetch_positions_with_greeks.py [--account ACCOUNT] [--wait-greeks SECONDS]
"""

from datetime import datetime
from ibkr_toolkit.utils.logger import setup_logger
from ibkr_toolkit.models.position import Position, PositionSummary
from ibkr_toolkit.client.ibkr_client import IBKRClient
from ibkr_toolkit.config.settings import Settings
import sys
import argparse
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Fetch IBKR positions with Greeks data"
    )
    parser.add_argument(
        "--account",
        type=str,
        default=None,
        help="Specify account (optional)"
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=5,
        help="Wait time for account updates (seconds, default: 5)"
    )
    parser.add_argument(
        "--wait-greeks",
        type=int,
        default=15,
        help="Wait time for Greeks data (seconds, default: 15)"
    )

    args = parser.parse_args()
    logger = setup_logger("fetch_positions_with_greeks")

    try:
        settings = Settings.from_env()
        settings.ensure_dirs()

        logger.info("=" * 60)
        logger.info("Fetching IBKR positions with Greeks")
        logger.info("=" * 60)

        # Connect
        logger.info("Step 1/5: Connecting to IBKR...")
        client = IBKRClient(settings)

        if not client.connect_sync():
            logger.error("Connection failed")
            return 1

        try:
            # Get account
            account = args.account or client.get_default_account()
            if not account:
                logger.error("No available account")
                return 1

            logger.info(f"Using account: {account}")

            # Subscribe to account updates
            logger.info(
                f"Step 2/5: Subscribing to account updates (waiting {args.wait} seconds)...")
            client.ib.client.reqAccountUpdates(True, account)
            logger.info(
                f"Waiting {args.wait} seconds for TWS to push complete data...")
            client.ib.sleep(args.wait)
            client.ib.client.reqAccountUpdates(False, account)

            # Get account summary data
            logger.info(
                "Step 3/5: Reading account summary and position data...")

            # Get key account metrics
            summary_data = client.ib.accountSummary(account)
            account_metrics = {}
            for item in summary_data:
                if item.tag in ['AvailableFunds', 'BuyingPower', 'EquityWithLoanValue', 'TotalCashValue', 'CashBalance']:
                    try:
                        account_metrics[item.tag] = float(item.value)
                    except (ValueError, TypeError):
                        account_metrics[item.tag] = 0.0

            # Get position data
            portfolio_items = client.ib.portfolio(account)

            if not portfolio_items:
                logger.warning("No positions found")
                return 0

            logger.info(f"Found {len(portfolio_items)} positions")

            # Convert to Position objects
            positions = []
            stocks = []
            options = []
            option_contracts = []
            others = []

            for item in portfolio_items:
                contract = item.contract
                multiplier = 1
                if contract.secType == 'OPT':
                    multiplier = 100
                elif contract.multiplier:
                    try:
                        multiplier = int(contract.multiplier)
                    except:
                        pass

                # Extract option-specific fields
                strike = None
                expiry = None
                right = None

                if contract.secType == 'OPT':
                    strike = getattr(contract, 'strike', None)
                    expiry = getattr(
                        contract, 'lastTradeDateOrContractMonth', None)
                    right = getattr(contract, 'right', None)

                position = Position(
                    symbol=contract.symbol,
                    contract_type=contract.secType,
                    exchange=contract.exchange or contract.primaryExchange,
                    currency=contract.currency,
                    position=item.position,
                    avg_cost=item.averageCost,
                    market_price=item.marketPrice,
                    market_value=item.marketValue,
                    unrealized_pnl=item.unrealizedPNL,
                    realized_pnl=item.realizedPNL,
                    account=item.account,
                    multiplier=multiplier,
                    local_symbol=contract.localSymbol,
                    strike=strike,
                    expiry=expiry,
                    right=right,
                    update_time=datetime.now()
                )
                positions.append(position)

                # Categorize positions
                if contract.secType == 'STK':
                    stocks.append(position)
                elif contract.secType == 'OPT':
                    options.append(position)
                    option_contracts.append(contract)
                else:
                    others.append(position)

            # Step 4: Fetch Greeks for options
            if options:
                logger.info(
                    f"Step 4/5: Fetching Greeks for {len(options)} options "
                    f"(waiting {args.wait_greeks} seconds)..."
                )
                logger.info(
                    "Note: This requires market to be open and active market data subscription")

                # First, qualify all contracts to ensure they have complete information
                logger.info("Qualifying option contracts...")
                qualified_contracts = []
                for idx, contract in enumerate(option_contracts):
                    logger.info(
                        f"  [{idx+1}/{len(option_contracts)}] Qualifying: {contract.localSymbol}")
                    try:
                        # Ensure contract is fully qualified
                        qualified = client.ib.qualifyContracts(contract)
                        if qualified:
                            qualified_contracts.append(qualified[0])
                            logger.info(
                                f"    ✓ Qualified: conId={qualified[0].conId}")
                        else:
                            qualified_contracts.append(contract)
                            logger.warning(
                                f"    ⚠ Could not qualify, using original contract")
                    except Exception as e:
                        logger.warning(
                            f"    ⚠ Error qualifying: {e}, using original contract")
                        qualified_contracts.append(contract)

                # Subscribe to market data for all options
                logger.info("")
                logger.info(
                    "Subscribing to market data (will use delayed data if real-time not available)...")

                # Request delayed market data (free for all accounts, 15-20 min delay)
                # This is necessary when real-time option data is not subscribed
                client.ib.reqMarketDataType(3)  # 3 = delayed data
                logger.info(
                    "Using delayed market data mode (free, 15-20 min delay)")

                option_tickers = []
                for idx, contract in enumerate(qualified_contracts):
                    logger.info(
                        f"  [{idx+1}/{len(qualified_contracts)}] Subscribing: {contract.localSymbol}"
                    )

                    # Request market data with Greeks
                    # genericTickList 106 = Option Implied Volatility and Greeks
                    ticker = client.ib.reqMktData(
                        contract,
                        genericTickList="106",  # Request option Greeks
                        snapshot=False,
                        regulatorySnapshot=False
                    )
                    option_tickers.append((ticker, contract))

                logger.info(
                    f"All {len(qualified_contracts)} subscriptions requested")

                # Wait for initial data
                logger.info("")
                logger.info(
                    f"Waiting {args.wait_greeks} seconds for market data to arrive...")
                client.ib.sleep(args.wait_greeks)

                # Check intermediate status
                logger.info("Checking data status...")
                data_count = 0
                for ticker, _ in option_tickers:
                    if hasattr(ticker, 'last') and ticker.last and not str(ticker.last) == 'nan':
                        data_count += 1
                logger.info(
                    f"  Received data for {data_count}/{len(option_tickers)} options so far")

                # Wait a bit more
                if data_count < len(option_tickers):
                    logger.info("Waiting additional 5 seconds...")
                    client.ib.sleep(5)

                # Update positions with Greeks
                logger.info("")
                logger.info("Analyzing received market data:")
                successful_greeks = 0
                for i, (pos, (ticker, contract)) in enumerate(zip(options, option_tickers)):
                    logger.info(
                        f"  [{i+1}/{len(options)}] {contract.localSymbol}")

                    # Check what data is available
                    has_model_greeks = hasattr(
                        ticker, 'modelGreeks') and ticker.modelGreeks
                    has_greeks = hasattr(ticker, 'greeks') and ticker.greeks
                    has_last = hasattr(ticker, 'last') and ticker.last
                    has_bid = hasattr(ticker, 'bid') and ticker.bid
                    has_ask = hasattr(ticker, 'ask') and ticker.ask

                    logger.info(f"    Market data: last={ticker.last if has_last else 'N/A'}, "
                                f"bid={ticker.bid if has_bid else 'N/A'}, "
                                f"ask={ticker.ask if has_ask else 'N/A'}")
                    logger.info(f"    Has modelGreeks: {has_model_greeks}")
                    logger.info(f"    Has greeks: {has_greeks}")

                    if has_model_greeks:
                        logger.info(
                            f"    modelGreeks content: {ticker.modelGreeks}")
                    if has_greeks:
                        logger.info(f"    greeks content: {ticker.greeks}")

                    greeks_found = False

                    # Method 1: Try modelGreeks (most common)
                    if has_model_greeks:
                        model_greeks = ticker.modelGreeks
                        if hasattr(model_greeks, 'delta') and model_greeks.delta is not None:
                            pos.delta = model_greeks.delta
                            pos.gamma = getattr(model_greeks, 'gamma', None)
                            pos.theta = getattr(model_greeks, 'theta', None)
                            pos.vega = getattr(model_greeks, 'vega', None)
                            greeks_found = True
                            logger.info(f"    ✓ Got Greeks from modelGreeks")

                    # Method 2: Try greeks attribute
                    if not greeks_found and has_greeks:
                        greeks = ticker.greeks
                        if hasattr(greeks, 'delta') and greeks.delta is not None:
                            pos.delta = greeks.delta
                            pos.gamma = getattr(greeks, 'gamma', None)
                            pos.theta = getattr(greeks, 'theta', None)
                            pos.vega = getattr(greeks, 'vega', None)
                            greeks_found = True
                            logger.info(
                                f"    ✓ Got Greeks from greeks attribute")

                    # Method 3: Try direct attributes
                    if not greeks_found:
                        if hasattr(ticker, 'delta') and ticker.delta is not None:
                            pos.delta = ticker.delta
                            pos.gamma = getattr(ticker, 'gamma', None) if hasattr(
                                ticker, 'gamma') else None
                            pos.theta = getattr(ticker, 'theta', None) if hasattr(
                                ticker, 'theta') else None
                            pos.vega = getattr(ticker, 'vega', None) if hasattr(
                                ticker, 'vega') else None
                            greeks_found = True
                            logger.info(
                                f"    ✓ Got Greeks from direct attributes")

                    if greeks_found:
                        successful_greeks += 1
                        gamma_str = f"{pos.gamma:.4f}" if pos.gamma is not None else "N/A"
                        theta_str = f"{pos.theta:.4f}" if pos.theta is not None else "N/A"
                        vega_str = f"{pos.vega:.4f}" if pos.vega is not None else "N/A"
                        logger.info(
                            f"    Greeks: Δ={pos.delta:.4f}, γ={gamma_str}, "
                            f"θ={theta_str}, ν={vega_str}"
                        )
                    else:
                        logger.info(f"    ✗ No Greeks data available")
                        logger.info(f"    Ticker details: {ticker}")

                logger.info("")

                # Cancel subscriptions
                logger.info("Cancelling market data subscriptions...")
                for ticker, contract in option_tickers:
                    client.ib.cancelMktData(contract)

                if successful_greeks > 0:
                    logger.info(
                        f"✓ Successfully fetched Greeks for {successful_greeks}/{len(options)} options")
                    logger.info(
                        "Note: Using delayed market data (15-20 min delay)")
                else:
                    logger.warning(
                        "⚠️  No Greeks data available. Possible reasons:"
                    )
                    logger.warning(
                        "   1. Market is closed (Greeks only available during market hours)")
                    logger.warning(
                        "   2. Network/connection issues with market data feed")
                    logger.warning(
                        "   3. Try increasing --wait-greeks time (try 30-60 seconds)")
                    logger.warning("")
                    logger.warning(
                        "💡 For real-time Greeks during market hours:")
                    logger.warning(
                        "   - Subscribe to US Option Add-On Streaming Bundle")
                    logger.warning(
                        "   - Go to Account > Market Data Subscriptions in IBKR portal")
            else:
                logger.info("Step 4/5: No options to fetch Greeks for")

            # Display categorized positions
            logger.info("")
            logger.info("Step 5/5: Displaying positions...")

            if stocks:
                logger.info("")
                logger.info(f"Stock Positions ({len(stocks)}):")
                for pos in stocks:
                    logger.info(
                        f"  {pos.symbol}: "
                        f"${pos.market_price:.2f} × {pos.position} = "
                        f"${pos.market_value:,.2f} (P&L: ${pos.unrealized_pnl:,.2f})"
                    )

            # Store leverage info for overall calculation
            spread_leverages = []  # List of (value, leverage) tuples

            if options:
                logger.info("")
                logger.info(f"Option Positions ({len(options)}):")

                # Group options by symbol, expiry, and right
                option_groups = {}
                for opt in options:
                    key = (opt.symbol, opt.expiry, opt.right)
                    if key not in option_groups:
                        option_groups[key] = []
                    option_groups[key].append(opt)

                # Display options grouped by strategy
                for (symbol, expiry, right), opts in sorted(option_groups.items()):
                    right_str = "Call" if right == "C" else "Put" if right == "P" else right
                    logger.info(f"  {symbol} {right_str} (Exp: {expiry}):")

                    # Sort by strike
                    opts.sort(key=lambda x: x.strike if x.strike else 0)

                    if len(opts) > 1:
                        # Smart pairing
                        paired = []
                        unpaired = []
                        used_indices = set()

                        for i, opt1 in enumerate(opts):
                            if i in used_indices:
                                continue

                            found_pair = False
                            for j, opt2 in enumerate(opts[i+1:], start=i+1):
                                if j in used_indices:
                                    continue

                                if (abs(opt1.position) == abs(opt2.position) and
                                        opt1.position * opt2.position < 0):
                                    paired.append([opt1, opt2])
                                    used_indices.add(i)
                                    used_indices.add(j)
                                    found_pair = True
                                    break

                            if not found_pair:
                                unpaired.append(opt1)

                        # Display paired spreads
                        for pair_idx, pair in enumerate(paired, 1):
                            pair.sort(key=lambda x: x.strike)

                            logger.info(f"    Spread #{pair_idx}:")

                            # Calculate pair totals
                            pair_value = sum(o.market_value for o in pair)
                            pair_pnl = sum(o.unrealized_pnl for o in pair)
                            # Net delta = sum of (delta × position) for all legs
                            pair_delta = sum(
                                (o.delta or 0) * o.position for o in pair)

                            # For leverage calculation, get the delta difference per spread
                            # This is the directional delta of one spread unit
                            spread_delta_per_unit = None
                            if all(o.delta for o in pair):
                                # Sort to get long and short legs
                                sorted_pair = sorted(
                                    pair, key=lambda x: x.position, reverse=True)
                                long_leg = sorted_pair[0]  # positive position
                                short_leg = sorted_pair[1]  # negative position
                                # Delta per spread unit = long_delta - short_delta
                                spread_delta_per_unit = long_leg.delta - short_leg.delta

                            # Determine strategy
                            long_leg = pair[0] if pair[0].position > 0 else pair[1]
                            short_leg = pair[1] if pair[0].position > 0 else pair[0]

                            if long_leg.strike < short_leg.strike:
                                strategy = "Bull Spread" if right == "C" else "Bear Spread"
                            else:
                                strategy = "Bear Spread" if right == "C" else "Bull Spread"

                            # Display legs
                            for opt in pair:
                                qty_sign = "+" if opt.position > 0 else ""
                                delta_str = f", Δ={opt.delta:.3f}" if opt.delta else ""
                                logger.info(
                                    f"      ${opt.strike:.0f}: "
                                    f"{qty_sign}{opt.position:.0f} @ ${opt.market_price:.2f} = "
                                    f"${opt.market_value:,.2f}{delta_str}"
                                )

                            # Display spread summary with leverage
                            # Show per-spread delta instead of total delta for spreads
                            if spread_delta_per_unit:
                                num_spreads = abs(pair[0].position)
                                delta_str = f", Δ={spread_delta_per_unit:.2f} (×{num_spreads:.0f}={pair_delta:.2f})"
                            else:
                                delta_str = f", Δ={pair_delta:.2f}" if any(
                                    o.delta for o in pair) else ""

                            # Calculate actual leverage = (underlying_price × delta_per_spread × 100) / value_per_spread
                            leverage_str = ""
                            actual_leverage = None
                            if spread_delta_per_unit and pair_value != 0:
                                # Get underlying price from stock positions
                                underlying_price = None
                                for stock in stocks:
                                    if stock.symbol == symbol:
                                        underlying_price = stock.market_price
                                        break

                                if underlying_price:
                                    # Number of spread units
                                    num_spreads = abs(pair[0].position)
                                    # Value per spread unit
                                    value_per_spread = abs(
                                        pair_value) / num_spreads
                                    # Leverage = (Underlying Price × Delta per spread × 100) / Value per spread
                                    actual_leverage = (
                                        underlying_price * spread_delta_per_unit * 100) / value_per_spread
                                    leverage_str = f", Lev={actual_leverage:.2f}x"

                                    # Store for overall leverage calculation
                                    spread_leverages.append(
                                        (abs(pair_value), actual_leverage))

                            logger.info(
                                f"      → [{strategy}] "
                                f"Value=${pair_value:,.2f}, "
                                f"P&L=${pair_pnl:,.2f}{delta_str}{leverage_str}"
                            )

                        # Display unpaired options
                        if unpaired:
                            logger.info(f"    Individual positions:")
                            for opt in unpaired:
                                delta_str = f", Δ={opt.delta:.3f}" if opt.delta else ""

                                # Calculate leverage for individual position
                                leverage_str = ""
                                if opt.delta and opt.market_value != 0:
                                    underlying_price = None
                                    for stock in stocks:
                                        if stock.symbol == symbol:
                                            underlying_price = stock.market_price
                                            break
                                    if underlying_price:
                                        opt_delta = opt.delta * opt.position
                                        actual_leverage = (
                                            abs(opt_delta) * underlying_price * 100) / abs(opt.market_value)
                                        leverage_str = f", Lev={actual_leverage:.2f}x"

                                        # Store for overall leverage calculation
                                        spread_leverages.append(
                                            (abs(opt.market_value), actual_leverage))

                                logger.info(
                                    f"      ${opt.strike:.0f}: "
                                    f"{opt.position:.0f} @ ${opt.market_price:.2f} = "
                                    f"${opt.market_value:,.2f} "
                                    f"(P&L: ${opt.unrealized_pnl:,.2f}{delta_str}{leverage_str})"
                                )
                    else:
                        # Single option position
                        opt = opts[0]
                        delta_str = f", Δ={opt.delta:.3f}" if opt.delta else ""

                        # Calculate leverage for single position
                        leverage_str = ""
                        if opt.delta and opt.market_value != 0:
                            underlying_price = None
                            for stock in stocks:
                                if stock.symbol == symbol:
                                    underlying_price = stock.market_price
                                    break
                            if underlying_price:
                                opt_delta = opt.delta * opt.position
                                actual_leverage = (
                                    abs(opt_delta) * underlying_price * 100) / abs(opt.market_value)
                                leverage_str = f", Lev={actual_leverage:.2f}x"

                                # Store for overall leverage calculation
                                spread_leverages.append(
                                    (abs(opt.market_value), actual_leverage))

                        logger.info(
                            f"    ${opt.strike:.0f}: "
                            f"{opt.position:.0f} @ ${opt.market_price:.2f} = "
                            f"${opt.market_value:,.2f} "
                            f"(P&L: ${opt.unrealized_pnl:,.2f}{delta_str}{leverage_str})"
                        )

            if others:
                logger.info("")
                logger.info(f"Other Positions ({len(others)}):")
                for pos in others:
                    logger.info(
                        f"  {pos.symbol} ({pos.contract_type}): "
                        f"${pos.market_price:.2f} × {pos.position} = "
                        f"${pos.market_value:,.2f} (P&L: ${pos.unrealized_pnl:,.2f})"
                    )

            # Generate summary
            logger.info("")
            total_market_value = sum(p.market_value for p in positions)
            total_unrealized_pnl = sum(p.unrealized_pnl for p in positions)
            total_realized_pnl = sum(p.realized_pnl for p in positions)

            summary = PositionSummary(
                total_positions=len(positions),
                total_market_value=total_market_value,
                total_unrealized_pnl=total_unrealized_pnl,
                total_realized_pnl=total_realized_pnl,
                total_pnl=total_unrealized_pnl + total_realized_pnl,
                positions=positions,
                update_time=datetime.now(),
                net_deposits=settings.net_deposits
            )

            logger.info("=" * 60)
            logger.info("POSITION SUMMARY")
            logger.info("=" * 60)
            logger.info(f"Total positions: {summary.total_positions}")
            logger.info("")

            # Stock summary
            if stocks:
                stock_value = sum(s.market_value for s in stocks)
                stock_pnl = sum(s.unrealized_pnl for s in stocks)
                logger.info(f"Stocks ({len(stocks)} positions):")
                logger.info(f"  Market value: ${stock_value:,.2f}")
                logger.info(f"  Unrealized P&L: ${stock_pnl:,.2f}")
                logger.info("")

            # Option summary
            if options:
                option_value = sum(o.market_value for o in options)
                option_pnl = sum(o.unrealized_pnl for o in options)
                has_greeks = any(o.delta is not None for o in options)

                logger.info(f"Options ({len(options)} positions):")
                logger.info(f"  Market value: ${option_value:,.2f}")
                logger.info(f"  Unrealized P&L: ${option_pnl:,.2f}")
                if has_greeks:
                    greeks_count = sum(
                        1 for o in options if o.delta is not None)
                    logger.info(
                        f"  ✓ Greeks available for {greeks_count}/{len(options)} options")

                    # Calculate total delta and leverage
                    total_delta = sum(
                        (o.delta or 0) * o.position for o in options)
                    logger.info(f"  Total Delta: {total_delta:.2f}")

                    # Calculate overall leverage as weighted average
                    # Overall Leverage = Sum(Value × Leverage) / Total Value
                    if spread_leverages and option_value != 0:
                        total_weighted_exposure = sum(
                            value * leverage for value, leverage in spread_leverages)
                        overall_leverage = total_weighted_exposure / \
                            abs(option_value)
                        logger.info(
                            f"  Overall Leverage: {overall_leverage:.2f}x")
                else:
                    logger.info(
                        f"  ⚠️  Greeks unavailable (market closed or no subscription)")
                logger.info("")

            # Total summary
            logger.info("Total Portfolio:")
            logger.info(f"  Market value: ${summary.total_market_value:,.2f}")
            logger.info(
                f"  Unrealized P&L: ${summary.total_unrealized_pnl:,.2f}")
            logger.info(f"  Realized P&L: ${summary.total_realized_pnl:,.2f}")
            logger.info(f"  Total P&L: ${summary.total_pnl:,.2f}")
            logger.info(f"  P&L percentage: {summary.total_pnl_percent:.2f}%")

            # Calculate account leverage
            if spread_leverages and options:
                # Money market funds (cash equivalents, not leveraged exposure)
                money_market_symbols = {
                    'SGOV', 'BOXX', 'USFR', 'TFLO', 'BIL', 'SHV'}

                # Option effective exposure
                option_value = sum(abs(o.market_value) for o in options)
                option_exposure = sum(
                    value * leverage for value, leverage in spread_leverages)

                # Stock exposure (excluding money market funds)
                stock_exposure = sum(
                    abs(s.market_value) for s in stocks
                    if s.symbol not in money_market_symbols
                )

                # Cash equivalent value (money market funds)
                cash_equivalent = sum(
                    abs(s.market_value) for s in stocks
                    if s.symbol in money_market_symbols
                )

                # Get cash balance from account metrics
                cash_balance = account_metrics.get('TotalCashValue', 0.0)
                if cash_balance == 0.0:
                    cash_balance = account_metrics.get('CashBalance', 0.0)

                # Total cash = cash equivalent (money market funds) + cash balance
                total_cash = cash_equivalent + cash_balance

                # True exposure = option exposure + stock exposure (excluding cash)
                true_exposure = option_exposure + stock_exposure

                # Account leverage = true exposure / total portfolio value
                if summary.total_market_value != 0:
                    account_leverage = true_exposure / \
                        abs(summary.total_market_value)
                    cash_percentage = (
                        total_cash / abs(summary.total_market_value)) * 100

                    logger.info("")
                    logger.info("Account Leverage Analysis:")
                    logger.info(
                        f"  Option effective exposure: ${option_exposure:,.2f}")
                    logger.info(
                        f"  Stock exposure (ex-cash): ${stock_exposure:,.2f}")
                    logger.info(f"  Cash equivalents: ${cash_equivalent:,.2f}")
                    logger.info(f"  True exposure: ${true_exposure:,.2f}")
                    logger.info(f"  Account Leverage: {account_leverage:.2f}x")
                    logger.info("")
                    logger.info(
                        f"  Total Cash: ${total_cash:,.2f} ({cash_percentage:.1f}% of portfolio)")

            logger.info("=" * 60)

            # Display account metrics
            if account_metrics:
                logger.info("")
                logger.info("=" * 60)
                logger.info("ACCOUNT METRICS")
                logger.info("=" * 60)
                if 'EquityWithLoanValue' in account_metrics:
                    logger.info(
                        f"Equity with loan value: ${account_metrics['EquityWithLoanValue']:,.2f}")
                if 'AvailableFunds' in account_metrics:
                    logger.info(
                        f"Available funds: ${account_metrics['AvailableFunds']:,.2f}")
                if 'BuyingPower' in account_metrics:
                    logger.info(
                        f"Buying power: ${account_metrics['BuyingPower']:,.2f}")
                logger.info("=" * 60)

            # Display account total return
            if summary.net_deposits is not None:
                logger.info("")
                logger.info("=" * 60)
                logger.info("ACCOUNT PERFORMANCE")
                logger.info("=" * 60)
                logger.info(f"Net deposits: ${summary.net_deposits:,.2f}")
                logger.info(
                    f"Total return: ${summary.account_total_return:,.2f}")
                logger.info(
                    f"Total return %: {summary.account_total_return_percent:.2f}%")
                logger.info("=" * 60)
            else:
                logger.info("")
                logger.info(
                    "💡 Tip: Set NET_DEPOSITS in .env to track account total return")

            return 0

        finally:
            client.disconnect_sync()
            logger.info("Disconnected")

    except Exception as e:
        logger.error(f"Execution failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
