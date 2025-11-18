"""Notion Page Service

Service for syncing portfolio data to Notion pages.
"""

from datetime import datetime
from typing import Optional

from notion_client import Client
from ..utils.logger import setup_logger


class NotionPageService:
    """Service for managing portfolio pages in Notion"""
    
    def __init__(self, api_key: str, database_id: str):
        """Initialize Notion service
        
        Args:
            api_key: Notion API key
            database_id: Notion database ID
        """
        self.client = Client(auth=api_key)
        self.database_id = database_id
        self.logger = setup_logger("notion_page_service")
    
    def sync_portfolio(
        self,
        stocks: list,
        options: list,
        others: list,
        account_metrics: dict,
        summary,
        max_records: int = 5,
        spread_leverages: list = None,
        option_leverage: float = None,
        account_leverage: float = None,
        option_exposure: float = None,
        stock_exposure: float = None,
        cash_equivalent: float = None,
        true_exposure: float = None,
        total_cash: float = None,
        cash_percentage: float = None
    ) -> Optional[str]:
        """Sync portfolio data to Notion
        
        Args:
            stocks: List of stock positions
            options: List of option positions
            others: List of other positions
            account_metrics: Account metrics dictionary
            summary: PositionSummary object
            max_records: Maximum number of records to keep
        
        Returns:
            URL of created page or None if failed
        """
        try:
            # Clean up old records
            self._cleanup_old_records(max_records)
            
            # Create new page
            page_url = self._create_portfolio_page(
                stocks, options, others, account_metrics, summary,
                spread_leverages, option_leverage, account_leverage,
                option_exposure, stock_exposure, cash_equivalent, true_exposure,
                total_cash, cash_percentage
            )
            
            return page_url
            
        except Exception as e:
            self.logger.error(f"Failed to sync to Notion: {e}", exc_info=True)
            return None
    
    def _cleanup_old_records(self, max_records: int):
        """Delete old records to keep only recent ones
        
        Args:
            max_records: Maximum number of records to keep (including new one to be created)
        """
        try:
            # Search for pages in the database
            # Using search API as a workaround for databases.query compatibility
            response = self.client.search(**{
                "filter": {
                    "value": "page",
                    "property": "object"
                },
                "sort": {
                    "direction": "descending",
                    "timestamp": "last_edited_time"
                }
            })
            
            # Filter pages that belong to our database
            all_pages = response.get("results", [])
            pages = [
                page for page in all_pages 
                if page.get("parent", {}).get("database_id") == self.database_id
            ]
            self.logger.info(f"Found {len(pages)} existing records")
            
            # Sort by created time (newest first)
            pages.sort(
                key=lambda x: x.get("created_time", ""),
                reverse=True
            )
            
            # Calculate how many to delete
            # We want to keep (max_records - 1) records, then add the new one
            # Total will be max_records
            if len(pages) >= max_records:
                # Need to delete to make room
                keep_count = max_records - 1
                pages_to_delete = pages[keep_count:]
                
                self.logger.info(
                    f"Keeping {keep_count} newest records, "
                    f"deleting {len(pages_to_delete)} old records..."
                )
                
                deleted_count = 0
                for page in pages_to_delete:
                    try:
                        # Get page title for logging
                        title = "Unknown"
                        if "properties" in page and "Name" in page["properties"]:
                            title_prop = page["properties"]["Name"]
                            if "title" in title_prop and len(title_prop["title"]) > 0:
                                title = title_prop["title"][0]["plain_text"]
                        
                        self.client.pages.update(
                            page_id=page["id"],
                            archived=True
                        )
                        deleted_count += 1
                        self.logger.info(f"  ✓ Deleted: {title[:40]}...")
                    except Exception as e:
                        self.logger.warning(
                            f"  ✗ Failed to delete {page['id'][:8]}...: {e}"
                        )
                
                self.logger.info(f"Successfully deleted {deleted_count} records")
            else:
                self.logger.info(
                    f"No cleanup needed ({len(pages)} < {max_records})"
                )
        
        except Exception as e:
            self.logger.warning(f"Failed to cleanup old records: {e}", exc_info=True)
    
    def _create_portfolio_page(
        self,
        stocks: list,
        options: list,
        others: list,
        account_metrics: dict,
        summary,
        spread_leverages: list = None,
        option_leverage: float = None,
        account_leverage: float = None,
        option_exposure: float = None,
        stock_exposure: float = None,
        cash_equivalent: float = None,
        true_exposure: float = None,
        total_cash: float = None,
        cash_percentage: float = None
    ) -> str:
        """Create a new portfolio page in Notion
        
        Args:
            stocks: List of stock positions
            options: List of option positions
            others: List of other positions
            account_metrics: Account metrics
            summary: PositionSummary object
        
        Returns:
            URL of created page
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Format content blocks
        content_blocks = self._format_content_blocks(
            stocks, options, others, account_metrics, summary,
            spread_leverages, option_leverage, account_leverage,
            option_exposure, stock_exposure, cash_equivalent, true_exposure,
            total_cash, cash_percentage
        )
        
        # Create page
        new_page = self.client.pages.create(
            parent={"database_id": self.database_id},
            properties={
                "Name": {
                    "title": [{
                        "text": {"content": f"IBKR Portfolio - {timestamp}"}
                    }]
                }
            },
            children=content_blocks
        )
        
        return new_page["url"]
    
    def _format_content_blocks(
        self,
        stocks: list,
        options: list,
        others: list,
        account_metrics: dict,
        summary,
        spread_leverages: list = None,
        option_leverage: float = None,
        account_leverage: float = None,
        option_exposure: float = None,
        stock_exposure: float = None,
        cash_equivalent: float = None,
        true_exposure: float = None,
        total_cash: float = None,
        cash_percentage: float = None
    ) -> list:
        """Format portfolio data as Notion blocks
        
        Returns:
            List of Notion block objects
        """
        blocks = []
        
        # Header
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        blocks.append({
            "object": "block",
            "type": "heading_1",
            "heading_1": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": f"Portfolio Snapshot - {timestamp}"}
                }]
            }
        })
        
        # Summary section
        blocks.extend(self._format_summary_section(summary))
        
        # Stock positions
        if stocks:
            blocks.extend(self._format_stock_section(stocks))
        
        # Option positions
        if options:
            blocks.extend(self._format_option_section(
                options, stocks, option_leverage))
        
        # Leverage analysis
        if account_leverage is not None:
            blocks.extend(self._format_leverage_section(
                option_exposure, stock_exposure, cash_equivalent,
                true_exposure, account_leverage, total_cash, cash_percentage
            ))
        
        # Other positions
        if others:
            blocks.extend(self._format_other_section(others))
        
        # Account metrics
        if account_metrics:
            blocks.extend(self._format_metrics_section(account_metrics))
        
        return blocks
    
    def _format_summary_section(self, summary) -> list:
        """Format summary section"""
        blocks = []
        
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": "📊 Summary"}
                }]
            }
        })
        
        summary_text = f"""Total Positions: {summary.total_positions}
Total Market Value: ${summary.total_market_value:,.2f}
Unrealized P&L: ${summary.total_unrealized_pnl:,.2f}
Realized P&L: ${summary.total_realized_pnl:,.2f}
Total P&L: ${summary.total_pnl:,.2f}
P&L Percentage: {summary.total_pnl_percent:.2f}%"""
        
        if summary.net_deposits is not None:
            summary_text += f"""

Net Deposits: ${summary.net_deposits:,.2f}
Total Return: ${summary.account_total_return:,.2f}
Total Return %: {summary.account_total_return_percent:.2f}%"""
        
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": summary_text}
                }]
            }
        })
        
        return blocks
    
    def _format_stock_section(self, stocks: list) -> list:
        """Format stock positions section"""
        blocks = []
        
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": f"📈 Stocks ({len(stocks)} positions)"}
                }]
            }
        })
        
        stock_value = sum(s.market_value for s in stocks)
        stock_pnl = sum(s.unrealized_pnl for s in stocks)
        
        stock_text = f"Market Value: ${stock_value:,.2f} | P&L: ${stock_pnl:,.2f}\n\n"
        for pos in stocks:
            stock_text += (
                f"• {pos.symbol}: ${pos.market_price:.2f} × {pos.position} = "
                f"${pos.market_value:,.2f} (P&L: ${pos.unrealized_pnl:,.2f})\n"
            )
        
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": stock_text}
                }]
            }
        })
        
        return blocks
    
    def _format_option_section(self, options: list, stocks: list = None, 
                               option_leverage: float = None) -> list:
        """Format option positions section"""
        blocks = []
        
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": f"📊 Options ({len(options)} positions)"}
                }]
            }
        })
        
        option_value = sum(o.market_value for o in options)
        option_pnl = sum(o.unrealized_pnl for o in options)
        has_greeks = any(o.delta is not None for o in options)
        
        option_text = f"Market Value: ${option_value:,.2f} | P&L: ${option_pnl:,.2f}\n"
        if has_greeks:
            total_delta = sum((o.delta or 0) * o.position for o in options)
            option_text += f"Total Delta: {total_delta:.2f}\n"
            if option_leverage is not None:
                option_text += f"Overall Leverage: {option_leverage:.2f}x\n"
        else:
            option_text += "⚠️ Greeks unavailable (market closed)\n"
        option_text += "\n"
        
        # Group and format options
        option_text += self._format_option_positions(options, stocks)
        
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": option_text}
                }]
            }
        })
        
        return blocks
    
    def _format_option_positions(self, options: list, stocks: list = None) -> str:
        """Format option positions with smart pairing"""
        text = ""
        
        # Group options
        option_groups = {}
        for opt in options:
            key = (opt.symbol, opt.expiry, opt.right)
            if key not in option_groups:
                option_groups[key] = []
            option_groups[key].append(opt)
        
        for (symbol, expiry, right), opts in sorted(option_groups.items()):
            right_str = "Call" if right == "C" else "Put" if right == "P" else right
            text += f"\n{symbol} {right_str} (Exp: {expiry}):\n"
            
            opts.sort(key=lambda x: x.strike if x.strike else 0)
            
            # Get underlying price
            underlying_price = None
            if stocks:
                for stock in stocks:
                    if stock.symbol == symbol:
                        underlying_price = stock.market_price
                        break

                # If no exact match, try symbol mappings (for convenience)
                if underlying_price is None:
                    symbol_mappings = {
                        'GOOG': 'GOOGL',
                        'GOOGL': 'GOOG',  # Mutual mapping for convenience
                    }

                    mapped_symbol = symbol_mappings.get(symbol)
                    if mapped_symbol:
                        for stock in stocks:
                            if stock.symbol == mapped_symbol:
                                underlying_price = stock.market_price
                                break
            
            if len(opts) > 1:
                # Smart pairing
                paired, unpaired = self._pair_options(opts)
                
                # Display paired spreads
                for pair_idx, pair in enumerate(paired, 1):
                    text += self._format_option_spread(
                        pair, pair_idx, right, underlying_price)
                
                # Display unpaired
                if unpaired:
                    text += "  Individual:\n"
                    for opt in unpaired:
                        delta_str = f", Δ={opt.delta:.3f}" if opt.delta else ""
                        leverage_str = ""
                        if opt.delta and opt.market_value != 0 and underlying_price:
                            opt_delta = opt.delta * opt.position
                            leverage = (abs(opt_delta) * underlying_price * 100) / abs(opt.market_value)
                            leverage_str = f", Lev={leverage:.2f}x"
                        text += (
                            f"    ${opt.strike:.0f}: {opt.position:.0f} @ "
                            f"${opt.market_price:.2f} = ${opt.market_value:,.2f} "
                            f"(P&L: ${opt.unrealized_pnl:,.2f}{delta_str}{leverage_str})\n"
                        )
            else:
                opt = opts[0]
                delta_str = f", Δ={opt.delta:.3f}" if opt.delta else ""
                leverage_str = ""
                if opt.delta and opt.market_value != 0 and underlying_price:
                    opt_delta = opt.delta * opt.position
                    leverage = (abs(opt_delta) * underlying_price * 100) / abs(opt.market_value)
                    leverage_str = f", Lev={leverage:.2f}x"
                text += (
                    f"  ${opt.strike:.0f}: {opt.position:.0f} @ "
                    f"${opt.market_price:.2f} = ${opt.market_value:,.2f} "
                    f"(P&L: ${opt.unrealized_pnl:,.2f}{delta_str}{leverage_str})\n"
                )
        
        return text
    
    def _pair_options(self, opts: list) -> tuple:
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
    
    def _format_option_spread(self, pair: list, pair_idx: int, right: str, 
                              underlying_price: float = None) -> str:
        """Format an option spread"""
        pair.sort(key=lambda x: x.strike)
        
        pair_value = sum(o.market_value for o in pair)
        pair_pnl = sum(o.unrealized_pnl for o in pair)
        pair_delta = sum((o.delta or 0) * o.position for o in pair)
        
        long_leg = pair[0] if pair[0].position > 0 else pair[1]
        short_leg = pair[1] if pair[0].position > 0 else pair[0]
        
        if long_leg.strike < short_leg.strike:
            strategy = "Bull Spread" if right == "C" else "Bear Spread"
        else:
            strategy = "Bear Spread" if right == "C" else "Bull Spread"
        
        # Calculate spread delta per unit
        spread_delta_per_unit = None
        if all(o.delta for o in pair):
            sorted_pair = sorted(pair, key=lambda x: x.position, reverse=True)
            long = sorted_pair[0]
            short = sorted_pair[1]
            spread_delta_per_unit = long.delta - short.delta
        
        text = f"  Spread #{pair_idx}:\n"
        for opt in pair:
            qty_sign = "+" if opt.position > 0 else ""
            delta_str = f", Δ={opt.delta:.3f}" if opt.delta else ""
            text += (
                f"    ${opt.strike:.0f}: {qty_sign}{opt.position:.0f} @ "
                f"${opt.market_price:.2f} = ${opt.market_value:,.2f}{delta_str}\n"
            )
        
        # Build spread summary
        delta_str = ""
        if spread_delta_per_unit:
            num_spreads = abs(pair[0].position)
            delta_str = f", Δ={spread_delta_per_unit:.2f} (×{num_spreads:.0f}={pair_delta:.2f})"
        elif any(o.delta for o in pair):
            delta_str = f", Δ={pair_delta:.2f}"
        
        leverage_str = ""
        if spread_delta_per_unit and pair_value != 0 and underlying_price:
            num_spreads = abs(pair[0].position)
            value_per_spread = abs(pair_value) / num_spreads
            leverage = (underlying_price * spread_delta_per_unit * 100) / value_per_spread
            leverage_str = f", Lev={leverage:.2f}x"
        
        text += (
            f"    → [{strategy}] Value=${pair_value:,.2f}, "
            f"P&L=${pair_pnl:,.2f}{delta_str}{leverage_str}\n"
        )
        
        return text
    
    def _format_leverage_section(
        self,
        option_exposure: float,
        stock_exposure: float,
        cash_equivalent: float,
        true_exposure: float,
        account_leverage: float,
        total_cash: float = None,
        cash_percentage: float = None
    ) -> list:
        """Format leverage analysis section"""
        blocks = []
        
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": "⚡ Leverage Analysis"}
                }]
            }
        })
        
        leverage_text = (
            f"Option Effective Exposure: ${option_exposure:,.2f}\n"
            f"Stock Exposure (ex-cash): ${stock_exposure:,.2f}\n"
            f"Cash Equivalents: ${cash_equivalent:,.2f}\n"
            f"True Exposure: ${true_exposure:,.2f}\n"
            f"Account Leverage: {account_leverage:.2f}x\n"
        )
        
        # Add total cash information if available
        if total_cash is not None:
            leverage_text += f"\nTotal Cash: ${total_cash:,.2f}"
            if cash_percentage is not None:
                leverage_text += f" ({cash_percentage:.1f}% of portfolio)"
        
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": leverage_text}
                }]
            }
        })
        
        return blocks
    
    def _format_other_section(self, others: list) -> list:
        """Format other positions section"""
        blocks = []
        
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": f"📦 Other Positions ({len(others)})"}
                }]
            }
        })
        
        other_text = ""
        for pos in others:
            other_text += (
                f"• {pos.symbol} ({pos.contract_type}): ${pos.market_price:.2f} × "
                f"{pos.position} = ${pos.market_value:,.2f} "
                f"(P&L: ${pos.unrealized_pnl:,.2f})\n"
            )
        
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": other_text}
                }]
            }
        })
        
        return blocks
    
    def _format_metrics_section(self, account_metrics: dict) -> list:
        """Format account metrics section"""
        blocks = []
        
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": "💰 Account Metrics"}
                }]
            }
        })
        
        metrics_text = ""
        if 'EquityWithLoanValue' in account_metrics:
            metrics_text += (
                f"Equity with Loan Value: "
                f"${account_metrics['EquityWithLoanValue']:,.2f}\n"
            )
        if 'AvailableFunds' in account_metrics:
            metrics_text += (
                f"Available Funds: "
                f"${account_metrics['AvailableFunds']:,.2f}\n"
            )
        if 'BuyingPower' in account_metrics:
            metrics_text += (
                f"Buying Power: "
                f"${account_metrics['BuyingPower']:,.2f}\n"
            )
        
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": metrics_text}
                }]
            }
        })
        
        return blocks

