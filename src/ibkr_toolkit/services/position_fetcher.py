"""Position Fetcher Service

Centralized service for fetching positions from IBKR.
"""

from datetime import datetime
from typing import Optional, Tuple

from ..models.position import Position, PositionSummary
from ..client.ibkr_client import IBKRClient
from ..utils.logger import setup_logger


class PositionFetcher:
    """Service for fetching and processing IBKR positions"""
    
    def __init__(self, client: IBKRClient):
        """Initialize position fetcher
        
        Args:
            client: IBKR client instance
        """
        self.client = client
        self.logger = setup_logger("position_fetcher")
    
    def fetch_all(
        self,
        account: Optional[str] = None,
        wait_seconds: int = 5,
        net_deposits: Optional[float] = None
    ) -> Optional[Tuple]:
        """Fetch all positions from IBKR
        
        Args:
            account: Account ID (optional, uses default if not specified)
            wait_seconds: Seconds to wait for data updates
            net_deposits: Total net deposits for return calculation
        
        Returns:
            Tuple of (positions, stocks, options, others, account_metrics, summary)
            or None if fetch failed
        """
        try:
            # Get account
            account = account or self.client.get_default_account()
            if not account:
                self.logger.error("No available account")
                return None
            
            self.logger.info(f"Using account: {account}")
            
            # Subscribe to account updates
            self.logger.info("Subscribing to account updates...")
            self.client.ib.client.reqAccountUpdates(True, account)
            self.client.ib.sleep(wait_seconds)
            self.client.ib.client.reqAccountUpdates(False, account)
            
            # Get account summary
            self.logger.info("Reading account summary and position data...")
            summary_data = self.client.ib.accountSummary(account)
            account_metrics = self._extract_account_metrics(summary_data)
            
            # Get positions
            portfolio_items = self.client.ib.portfolio(account)
            
            if not portfolio_items:
                self.logger.warning("No positions found")
                return None
            
            self.logger.info(f"Found {len(portfolio_items)} positions")
            
            # Convert and categorize positions
            positions, stocks, options, others = self._process_positions(portfolio_items)
            
            # Generate summary
            summary = self._generate_summary(positions, net_deposits)
            
            return (positions, stocks, options, others, account_metrics, summary)
            
        except Exception as e:
            self.logger.error(f"Failed to fetch positions: {e}", exc_info=True)
            return None
    
    def _extract_account_metrics(self, summary_data) -> dict:
        """Extract key account metrics from summary data
        
        Args:
            summary_data: Account summary from IBKR
        
        Returns:
            Dictionary of account metrics
        """
        account_metrics = {}
        for item in summary_data:
            if item.tag in ['AvailableFunds', 'BuyingPower', 'EquityWithLoanValue']:
                try:
                    account_metrics[item.tag] = float(item.value)
                except (ValueError, TypeError):
                    account_metrics[item.tag] = 0.0
        return account_metrics
    
    def _process_positions(self, portfolio_items) -> Tuple:
        """Process portfolio items into Position objects
        
        Args:
            portfolio_items: Portfolio items from IBKR
        
        Returns:
            Tuple of (positions, stocks, options, others)
        """
        positions = []
        stocks = []
        options = []
        others = []
        
        for item in portfolio_items:
            contract = item.contract
            
            # Calculate multiplier
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
                expiry = getattr(contract, 'lastTradeDateOrContractMonth', None)
                right = getattr(contract, 'right', None)
            
            # Create Position object
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
            else:
                others.append(position)
        
        return (positions, stocks, options, others)
    
    def _generate_summary(
        self,
        positions: list,
        net_deposits: Optional[float] = None
    ) -> PositionSummary:
        """Generate position summary
        
        Args:
            positions: List of Position objects
            net_deposits: Total net deposits
        
        Returns:
            PositionSummary object
        """
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
            net_deposits=net_deposits
        )
        
        return summary

