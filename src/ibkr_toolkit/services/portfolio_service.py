"""持仓服务模块

提供持仓数据的获取和处理功能。
"""

from datetime import datetime
from typing import Optional
from ..client.ibkr_client import IBKRClient
from ..models.position import Position, PositionSummary
from ..utils.logger import setup_logger


class PortfolioService:
    """持仓服务类"""

    def __init__(self, client: IBKRClient):
        """初始化服务

        Args:
            client: IBKR 客户端实例
        """
        self.client = client
        self.logger = setup_logger("portfolio_service")

    def get_positions(self, account: Optional[str] = None) -> list[Position]:
        """获取所有持仓
        
        优先使用 portfolio() API（如果已通过 reqAccountUpdates 订阅，会有完整的市场数据），
        如果没有数据则回退到 positions() API（只有基础持仓信息）。

        Args:
            account: 指定账户

        Returns:
            持仓列表
        """
        if not self.client.is_connected:
            self.logger.error("客户端未连接")
            return []

        try:
            # 优先尝试 portfolio() API（包含市场价格和盈亏）
            portfolio_items = self.client.get_portfolio_items(account=account)
            
            if portfolio_items:
                self.logger.info(f"使用 portfolio() API 获取到 {len(portfolio_items)} 个投资组合项")
                positions = []
                for item in portfolio_items:
                    position = self._convert_portfolio_to_position(item)
                    if position:
                        positions.append(position)
                
                self.logger.info(f"成功转换 {len(positions)} 个持仓")
                return positions
            
            # 如果 portfolio() 没有数据，尝试 positions() API（只有基础信息）
            self.logger.info("portfolio() 无数据，尝试 positions() API")
            raw_positions = self.client.get_positions(account=account)
            
            if raw_positions:
                self.logger.info(f"使用 positions() API 获取到 {len(raw_positions)} 个持仓")
                self.logger.warning("⚠️ positions() API 只包含成本价，不包含市场价格和盈亏")
                self.logger.warning("   建议先调用 client.ib.reqAccountUpdates(account) 订阅账户更新")
                positions = []
                for item in raw_positions:
                    position = self._convert_position_to_position(item)
                    if position:
                        positions.append(position)
                
                self.logger.info(f"成功转换 {len(positions)} 个持仓")
                return positions
            
            self.logger.warning("两个 API 都未返回数据")
            return []

        except Exception as e:
            self.logger.error(f"获取持仓失败: {e}")
            return []

    def get_position_summary(self, account: Optional[str] = None) -> Optional[PositionSummary]:
        """获取持仓汇总

        Args:
            account: 指定账户

        Returns:
            持仓汇总对象
        """
        positions = self.get_positions(account=account)

        if not positions:
            self.logger.warning("没有持仓数据")
            return None

        # 计算汇总数据
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
            update_time=datetime.now()
        )

        self.logger.info(
            f"持仓汇总: {summary.total_positions} 个, "
            f"总市值: {summary.total_market_value:.2f}, "
            f"总盈亏: {summary.total_pnl:.2f}"
        )

        return summary

    def _convert_position_to_position(self, ib_position) -> Optional[Position]:
        """将 IB 的 Position 对象转换为我们的 Position 对象

        Args:
            ib_position: IB Position 对象（来自 positions() API）

        Returns:
            Position 对象
        """
        try:
            contract = ib_position.contract
            
            # positions() API 返回的数据较少，市场价格需要单独请求
            # 这里我们先用平均成本作为市场价格的估算
            avg_cost = ib_position.avgCost
            position_size = ib_position.position
            
            # 计算基本值（如果没有市场价格，使用成本价）
            market_value = abs(position_size * avg_cost)
            
            position = Position(
                symbol=contract.symbol,
                contract_type=contract.secType,
                exchange=contract.exchange or contract.primaryExchange,
                currency=contract.currency,
                position=position_size,
                avg_cost=avg_cost,
                market_price=avg_cost,  # 使用成本价作为市场价（待更新）
                market_value=market_value,
                unrealized_pnl=0.0,  # positions() API 不提供盈亏信息
                realized_pnl=0.0,
                account=ib_position.account,
                multiplier=int(contract.multiplier) if contract.multiplier else 1,
                local_symbol=contract.localSymbol,
                update_time=datetime.now()
            )

            return position

        except Exception as e:
            self.logger.error(f"转换 Position 数据失败: {e}", exc_info=True)
            return None
    
    def _convert_portfolio_to_position(self, portfolio_item) -> Optional[Position]:
        """将 IB 的 PortfolioItem 转换为我们的 Position 对象

        Args:
            portfolio_item: IB PortfolioItem 对象（来自 portfolio() API）

        Returns:
            Position 对象
        """
        try:
            contract = portfolio_item.contract

            position = Position(
                symbol=contract.symbol,
                contract_type=contract.secType,
                exchange=contract.exchange or contract.primaryExchange,
                currency=contract.currency,
                position=portfolio_item.position,
                avg_cost=portfolio_item.averageCost,
                market_price=portfolio_item.marketPrice,
                market_value=portfolio_item.marketValue,
                unrealized_pnl=portfolio_item.unrealizedPNL,
                realized_pnl=portfolio_item.realizedPNL,
                account=portfolio_item.account,
                multiplier=int(
                    contract.multiplier) if contract.multiplier else 1,
                local_symbol=contract.localSymbol,
                update_time=datetime.now()
            )

            return position

        except Exception as e:
            self.logger.error(f"转换 PortfolioItem 数据失败: {e}", exc_info=True)
            return None
