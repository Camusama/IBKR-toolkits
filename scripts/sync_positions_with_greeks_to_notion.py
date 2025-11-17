#!/usr/bin/env python3
"""Sync IBKR Positions with Greeks to Notion

Fetch IBKR positions with option Greeks and sync to Notion database.
Includes leverage analysis and option Greeks data.

Usage:
    python scripts/sync_positions_with_greeks_to_notion.py [--account ACCOUNT] [--max-records N] [--wait-greeks SECONDS]
"""

import os
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

from ibkr_toolkit.utils.logger import setup_logger
from ibkr_toolkit.utils.greeks_cache import GreeksCache
from ibkr_toolkit.client.ibkr_client import IBKRClient
from ibkr_toolkit.config.settings import Settings
from ibkr_toolkit.models.position import Position, PositionSummary
from ibkr_toolkit.services.notion_page_service import NotionPageService

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

# Load environment variables
env_path = project_root / ".env"
load_dotenv(env_path)


def fetch_greeks(client, options, option_contracts, stocks, wait_seconds=15, logger=None, cache=None):
    """Fetch Greeks for option positions
    
    Args:
        client: IBKRClient instance
        options: List of option Position objects
        option_contracts: List of option contracts
        stocks: List of stock Position objects for underlying price
        wait_seconds: Wait time for Greeks data
        logger: Logger instance
        cache: GreeksCache instance for caching
    
    Returns:
        spread_leverages: List of (value, leverage) tuples
    """
    if not logger:
        logger = setup_logger("fetch_greeks")
    
    spread_leverages = []
    
    if not options:
        return spread_leverages
    
    msg = f"Fetching Greeks for {len(options)} options (waiting {wait_seconds} seconds)..."
    logger.info(msg)
    logger.info("Note: This requires market to be open. If market is closed, will skip Greeks.")
    
    try:
        # First, qualify all contracts
        logger.info("Qualifying option contracts...")
        qualified_contracts = []
        for idx, contract in enumerate(option_contracts):
            try:
                qualified = client.ib.qualifyContracts(contract)
                if qualified:
                    qualified_contracts.append(qualified[0])
                else:
                    qualified_contracts.append(contract)
            except Exception as e:
                logger.warning(f"  Error qualifying contract {idx}: {e}")
                qualified_contracts.append(contract)
        
        # Request delayed market data (free, works when market is open)
        client.ib.reqMarketDataType(3)  # 3 = delayed data
        logger.info("Using delayed market data mode (free, 15-20 min delay)")
        
        # Subscribe to market data
        option_tickers = []
        for idx, contract in enumerate(qualified_contracts):
            try:
                ticker = client.ib.reqMktData(
                    contract,
                    genericTickList="106",  # Request option Greeks
                    snapshot=False,
                    regulatorySnapshot=False
                )
                option_tickers.append((ticker, contract))
            except Exception as e:
                logger.warning(f"  Error subscribing to {contract.localSymbol}: {e}")
        
        if not option_tickers:
            logger.warning("No market data subscriptions successful")
            return spread_leverages
        
        logger.info(f"Subscribed to {len(option_tickers)} options")
        
        # Wait for data
        logger.info(f"Waiting {wait_seconds} seconds for market data...")
        client.ib.sleep(wait_seconds)
        
        # Check data status and wait longer if needed
        max_retries = 2
        for retry in range(max_retries):
            data_count = 0
            greeks_count = 0
            
            for ticker, _ in option_tickers:
                if hasattr(ticker, 'last') and ticker.last and not str(ticker.last) == 'nan':
                    data_count += 1
                
                # Check if Greeks are available
                has_greeks = False
                if hasattr(ticker, 'modelGreeks') and ticker.modelGreeks:
                    if hasattr(ticker.modelGreeks, 'delta') and ticker.modelGreeks.delta is not None:
                        has_greeks = True
                elif hasattr(ticker, 'greeks') and ticker.greeks:
                    if hasattr(ticker.greeks, 'delta') and ticker.greeks.delta is not None:
                        has_greeks = True
                elif hasattr(ticker, 'delta') and ticker.delta is not None:
                    has_greeks = True
                
                if has_greeks:
                    greeks_count += 1
            
            logger.info(f"Received data for {data_count}/{len(option_tickers)} options, Greeks: {greeks_count}/{len(option_tickers)}")
            
            # If we have good Greeks coverage, break
            if greeks_count >= len(option_tickers) * 0.75:
                break
            
            # Otherwise wait more (but not on last retry)
            if retry < max_retries - 1:
                logger.info(f"Greeks coverage low, waiting additional 10 seconds (retry {retry + 1}/{max_retries})...")
                client.ib.sleep(10)
        
        # Update positions with Greeks
        successful_greeks = 0
        failed_options = []
        
        for i, (pos, (ticker, contract)) in enumerate(zip(options, option_tickers)):
            try:
                greeks_found = False
                
                # Try modelGreeks
                if hasattr(ticker, 'modelGreeks') and ticker.modelGreeks:
                    model_greeks = ticker.modelGreeks
                    if hasattr(model_greeks, 'delta') and model_greeks.delta is not None:
                        pos.delta = model_greeks.delta
                        pos.gamma = getattr(model_greeks, 'gamma', None)
                        pos.theta = getattr(model_greeks, 'theta', None)
                        pos.vega = getattr(model_greeks, 'vega', None)
                        greeks_found = True
                
                # Try greeks attribute
                if not greeks_found and hasattr(ticker, 'greeks') and ticker.greeks:
                    greeks = ticker.greeks
                    if hasattr(greeks, 'delta') and greeks.delta is not None:
                        pos.delta = greeks.delta
                        pos.gamma = getattr(greeks, 'gamma', None)
                        pos.theta = getattr(greeks, 'theta', None)
                        pos.vega = getattr(greeks, 'vega', None)
                        greeks_found = True
                
                # Try direct attributes
                if not greeks_found and hasattr(ticker, 'delta') and ticker.delta is not None:
                    pos.delta = ticker.delta
                    pos.gamma = getattr(ticker, 'gamma', None) if hasattr(ticker, 'gamma') else None
                    pos.theta = getattr(ticker, 'theta', None) if hasattr(ticker, 'theta') else None
                    pos.vega = getattr(ticker, 'vega', None) if hasattr(ticker, 'vega') else None
                    greeks_found = True
                
                if greeks_found:
                    successful_greeks += 1
                    logger.info(f"  ✓ {pos.local_symbol}: δ={pos.delta:.4f}")
                else:
                    failed_options.append(pos.local_symbol)
                    logger.warning(f"  ✗ {pos.local_symbol}: No Greeks data")
                    
            except Exception as e:
                failed_options.append(pos.local_symbol if hasattr(pos, 'local_symbol') else f"Option {i}")
                logger.warning(f"  ✗ Error updating Greeks for {pos.local_symbol}: {e}")
        
        # Cancel subscriptions
        for ticker, contract in option_tickers:
            try:
                client.ib.cancelMktData(contract)
            except:
                pass
        
        if successful_greeks > 0:
            logger.info(f"✓ Successfully fetched Greeks for {successful_greeks}/{len(options)} options")
            
            # If some options failed, try to supplement from cache
            if failed_options and cache:
                logger.info(f"Attempting to load missing Greeks from cache for {len(failed_options)} options...")
                failed_positions = [opt for opt in options if opt.local_symbol in failed_options]
                if cache.load_greeks(failed_positions):
                    # Count how many were recovered from cache
                    recovered = sum(1 for opt in failed_positions if opt.delta is not None)
                    if recovered > 0:
                        successful_greeks += recovered
                        logger.info(f"✓ Recovered {recovered} Greeks from cache")
                        for opt in failed_positions:
                            if opt.delta is not None:
                                logger.info(f"  ✓ {opt.local_symbol}: δ={opt.delta:.4f} (from cache)")
            
            # Save all options with Greeks to cache
            if cache:
                cache.save_greeks(options)
        else:
            logger.warning("⚠️  No Greeks data available (market closed or data unavailable)")
            
            # Try to load from cache
            if cache:
                logger.info("Attempting to load Greeks from cache...")
                if cache.load_greeks(options):
                    # Successfully loaded from cache, continue with calculation
                    successful_greeks = sum(1 for opt in options if opt.delta is not None)
                    logger.info(f"✓ Using cached Greeks for {successful_greeks}/{len(options)} options")
                else:
                    logger.warning("Failed to load Greeks from cache")
                    return spread_leverages
            else:
                return spread_leverages
        
        # Calculate leverages
        logger.info("Calculating leverage for option positions...")
        
        # Group options for spread analysis
        option_groups = {}
        for opt in options:
            key = (opt.symbol, opt.expiry, opt.right)
            if key not in option_groups:
                option_groups[key] = []
            option_groups[key].append(opt)
        
        for (symbol, expiry, right), opts in option_groups.items():
            # Get underlying price
            underlying_price = None
            for stock in stocks:
                if stock.symbol == symbol:
                    underlying_price = stock.market_price
                    break
            
            if not underlying_price:
                continue
            
            opts.sort(key=lambda x: x.strike if x.strike else 0)
            
            if len(opts) > 1:
                # Smart pairing
                paired, unpaired = pair_options(opts)
                
                # Calculate leverage for spreads
                for pair in paired:
                    pair_value = sum(o.market_value for o in pair)
                    
                    if all(o.delta for o in pair):
                        sorted_pair = sorted(pair, key=lambda x: x.position, reverse=True)
                        long_leg = sorted_pair[0]
                        short_leg = sorted_pair[1]
                        spread_delta_per_unit = long_leg.delta - short_leg.delta
                        
                        if pair_value != 0:
                            num_spreads = abs(pair[0].position)
                            value_per_spread = abs(pair_value) / num_spreads
                            leverage = (underlying_price * spread_delta_per_unit * 100) / value_per_spread
                            spread_leverages.append((abs(pair_value), leverage))
                
                # Calculate leverage for unpaired
                for opt in unpaired:
                    if opt.delta and opt.market_value != 0:
                        opt_delta = opt.delta * opt.position
                        leverage = (abs(opt_delta) * underlying_price * 100) / abs(opt.market_value)
                        spread_leverages.append((abs(opt.market_value), leverage))
            else:
                # Single position
                opt = opts[0]
                if opt.delta and opt.market_value != 0:
                    opt_delta = opt.delta * opt.position
                    leverage = (abs(opt_delta) * underlying_price * 100) / abs(opt.market_value)
                    spread_leverages.append((abs(opt.market_value), leverage))
        
        logger.info(f"Calculated leverage for {len(spread_leverages)} option positions/spreads")
        
    except Exception as e:
        logger.warning(f"Failed to fetch Greeks: {e}", exc_info=True)
    
    return spread_leverages


def pair_options(opts):
    """Pair options with opposite positions"""
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
    
    return paired, unpaired


def main():
    """Main function"""
    import argparse
    parser = argparse.ArgumentParser(
        description="Sync IBKR positions with Greeks to Notion"
    )
    parser.add_argument(
        "--account",
        type=str,
        default=None,
        help="Specify account (optional)"
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=5,
        help="Maximum number of records to keep (default: 5)"
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
    logger = setup_logger("sync_positions_with_greeks_to_notion")
    
    try:
        # Get Notion credentials
        api_key = os.getenv("NOTION_API_KEY")
        database_id = os.getenv("NOTION_NOTES_DATABASE_ID")
        
        if not api_key or not database_id:
            logger.error("NOTION_API_KEY or NOTION_NOTES_DATABASE_ID not found in .env")
            return 1
        
        logger.info("=" * 60)
        logger.info("Syncing IBKR Positions with Greeks to Notion")
        logger.info("=" * 60)
        
        # Initialize settings
        settings = Settings.from_env()
        settings.ensure_dirs()
        
        # Step 1: Connect to IBKR
        logger.info("Step 1/4: Connecting to IBKR...")
        client = IBKRClient(settings)
        
        if not client.connect_sync():
            logger.error("Failed to connect to IBKR")
            return 1
        
        try:
            # Get account
            account = args.account or client.get_default_account()
            if not account:
                logger.error("No available account")
                return 1
            
            logger.info(f"Using account: {account}")
            
            # Step 2: Fetch positions
            logger.info(f"Step 2/4: Fetching positions (waiting {args.wait} seconds)...")
            
            # Subscribe to account updates
            client.ib.client.reqAccountUpdates(True, account)
            client.ib.sleep(args.wait)
            client.ib.client.reqAccountUpdates(False, account)
            
            # Get account summary
            summary_data = client.ib.accountSummary(account)
            account_metrics = {}
            for item in summary_data:
                if item.tag in ['AvailableFunds', 'BuyingPower', 'EquityWithLoanValue', 'TotalCashValue', 'CashBalance']:
                    try:
                        account_metrics[item.tag] = float(item.value)
                    except (ValueError, TypeError):
                        account_metrics[item.tag] = 0.0
            
            # Get positions
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
                
                # Extract option fields
                strike = None
                expiry = None
                right = None
                
                if contract.secType == 'OPT':
                    strike = getattr(contract, 'strike', None)
                    expiry = getattr(contract, 'lastTradeDateOrContractMonth', None)
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
                
                # Categorize
                if contract.secType == 'STK':
                    stocks.append(position)
                elif contract.secType == 'OPT':
                    options.append(position)
                    option_contracts.append(contract)
                else:
                    others.append(position)
            
            # Step 3: Fetch Greeks (if options exist)
            spread_leverages = []
            if options:
                logger.info("Step 3/4: Fetching Greeks and calculating leverage...")
                
                # Initialize Greeks cache
                cache = GreeksCache(settings.data_dir)
                
                # Show cache info if available
                cache_info = cache.get_cache_info()
                if cache_info:
                    logger.info(
                        f"Greeks cache available: {cache_info['option_count']} options, "
                        f"age: {cache_info['age_hours']:.1f} hours"
                    )
                
                spread_leverages = fetch_greeks(
                    client, options, option_contracts, stocks,
                    wait_seconds=args.wait_greeks, logger=logger, cache=cache
                )
            else:
                logger.info("Step 3/4: No options to fetch Greeks for")
            
        finally:
            client.disconnect_sync()
            logger.info("Disconnected from IBKR")
        
        # Calculate summary
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
        
        # Calculate leverages
        option_leverage = None
        account_leverage = None
        option_exposure = None
        stock_exposure = None
        cash_equivalent = None
        true_exposure = None
        total_cash = None
        cash_percentage = None
        
        if spread_leverages and options:
            money_market_symbols = {'SGOV', 'BOXX', 'USFR', 'TFLO', 'BIL', 'SHV'}
            
            # Option exposure
            option_value = sum(abs(o.market_value) for o in options)
            option_exposure = sum(value * leverage for value, leverage in spread_leverages)
            
            # Option overall leverage
            if option_value != 0:
                option_leverage = option_exposure / abs(option_value)
            
            # Stock exposure (excluding money market funds)
            stock_exposure = sum(
                abs(s.market_value) for s in stocks
                if s.symbol not in money_market_symbols
            )
            
            # Cash equivalent (money market funds)
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
            
            # Cash percentage
            if summary.total_market_value != 0:
                cash_percentage = (total_cash / abs(summary.total_market_value)) * 100
            
            # True exposure
            true_exposure = option_exposure + stock_exposure
            
            # Account leverage
            if summary.total_market_value != 0:
                account_leverage = true_exposure / abs(summary.total_market_value)
        
        # Step 4: Sync to Notion
        logger.info(f"Step 4/4: Syncing to Notion (keep last {args.max_records} records)...")
        notion_service = NotionPageService(api_key, database_id)
        
        page_url = notion_service.sync_portfolio(
            stocks=stocks,
            options=options,
            others=others,
            account_metrics=account_metrics,
            summary=summary,
            max_records=args.max_records,
            spread_leverages=spread_leverages,
            option_leverage=option_leverage,
            account_leverage=account_leverage,
            option_exposure=option_exposure,
            stock_exposure=stock_exposure,
            cash_equivalent=cash_equivalent,
            true_exposure=true_exposure,
            total_cash=total_cash,
            cash_percentage=cash_percentage
        )
        
        if page_url is None:
            logger.error("Failed to sync to Notion")
            return 1
        
        # Success
        logger.info("")
        logger.info("=" * 60)
        logger.info("✅ SUCCESS")
        logger.info("=" * 60)
        logger.info(f"Page created: {page_url}")
        logger.info(f"Total positions: {summary.total_positions}")
        logger.info(f"Market value: ${summary.total_market_value:,.2f}")
        logger.info(f"Total P&L: ${summary.total_pnl:,.2f} ({summary.total_pnl_percent:.2f}%)")
        
        if summary.net_deposits is not None:
            logger.info(f"Total return: ${summary.account_total_return:,.2f} ({summary.account_total_return_percent:.2f}%)")
        
        if account_leverage is not None:
            logger.info(f"Account Leverage: {account_leverage:.2f}x")
        
        logger.info("=" * 60)
        
        return 0
        
    except Exception as e:
        logger.error(f"Execution failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
