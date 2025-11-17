"""市场数据服务模块

提供市场数据的订阅和更新功能。
"""

import asyncio
from typing import Optional, Dict
from ..client.ibkr_client import IBKRClient
from ..models.position import Position
from ..utils.logger import setup_logger


class MarketDataService:
    """市场数据服务类"""

    def __init__(self, client: IBKRClient):
        """初始化服务

        Args:
            client: IBKR 客户端实例
        """
        self.client = client
        self.logger = setup_logger("market_data_service")

    def update_positions_with_market_data(
        self, 
        raw_positions,  # 原始 IB Position 对象列表
        timeout: int = 10
    ) -> list[Position]:
        """为持仓更新市场数据（价格和盈亏）
        
        使用原始 IB Position 对象（包含完整的 contract 信息）来获取市场数据

        Args:
            raw_positions: 原始 IB Position 对象列表
            timeout: 超时时间（秒）

        Returns:
            更新后的持仓列表
        """
        if not self.client.is_connected:
            self.logger.error("客户端未连接")
            return []

        if not raw_positions:
            return []

        try:
            self.logger.info(f"开始为 {len(raw_positions)} 个持仓获取市场数据...")
            
            # 使用原始 contract（已有 conId）批量请求市场数据
            contracts = [pos.contract for pos in raw_positions]
            
            # 请求市场数据（使用 qualify 确保合约有效）
            self.logger.info("正在验证合约信息...")
            qualified_contracts = self.client.ib.qualifyContracts(*contracts)
            
            # 批量请求市场数据
            self.logger.info("正在请求市场数据...")
            tickers = self.client.ib.reqTickers(*qualified_contracts)
            
            # 等待数据更新
            self.client.ib.sleep(2)
            
            # 转换为 Position 对象
            from ..models.position import Position
            from datetime import datetime
            
            updated_positions = []
            for raw_pos, ticker in zip(raw_positions, tickers):
                contract = raw_pos.contract
                
                # 获取市场价格
                market_price = ticker.marketPrice()
                if not market_price or market_price <= 0:
                    # 如果没有市场价格，使用成本价
                    market_price = raw_pos.avgCost
                    self.logger.warning(
                        f"  ✗ {contract.symbol} {contract.secType}: "
                        f"市场数据不可用，使用成本价"
                    )
                
                # 确定 multiplier
                multiplier = 1
                if contract.secType == 'OPT':
                    multiplier = 100  # 期权默认100
                elif contract.multiplier:
                    try:
                        multiplier = int(contract.multiplier)
                    except:
                        multiplier = 1
                
                # 计算市场价值和盈亏
                # 注意：对于期权，avgCost 可能已经是总价，需要特殊处理
                position_size = raw_pos.position
                avg_cost = raw_pos.avgCost
                
                # 期权的计算逻辑
                if contract.secType == 'OPT':
                    # avgCost 通常是每股价格（需要乘以100）
                    # 或者是总成本（取决于 broker 设置）
                    # 市场价值 = position * price * multiplier
                    market_value = position_size * market_price * multiplier
                    unrealized_pnl = position_size * (market_price - avg_cost) * multiplier
                else:
                    # 股票的计算
                    market_value = position_size * market_price
                    unrealized_pnl = position_size * (market_price - avg_cost)
                
                position = Position(
                    symbol=contract.symbol,
                    contract_type=contract.secType,
                    exchange=contract.exchange or contract.primaryExchange,
                    currency=contract.currency,
                    position=position_size,
                    avg_cost=avg_cost,
                    market_price=market_price,
                    market_value=market_value,
                    unrealized_pnl=unrealized_pnl,
                    realized_pnl=0.0,  # positions() API 不提供
                    account=raw_pos.account,
                    multiplier=multiplier,
                    local_symbol=contract.localSymbol,
                    update_time=datetime.now()
                )
                
                updated_positions.append(position)
                
                self.logger.info(
                    f"  ✓ {contract.symbol} {contract.secType}: "
                    f"${market_price:.2f} × {position_size} = "
                    f"${market_value:,.2f} (P&L: ${unrealized_pnl:,.2f})"
                )
            
            self.logger.info(f"成功更新 {len(updated_positions)} 个持仓的市场数据")
            return updated_positions

        except Exception as e:
            self.logger.error(f"更新市场数据失败: {e}", exc_info=True)
            return []

    def _position_to_contract(self, position: Position):
        """将 Position 对象转换为 Contract 对象

        Args:
            position: Position 对象

        Returns:
            Contract 对象
        """
        from ib_async import Stock, Option, Future, Forex, Index
        
        # 根据合约类型创建对应的 Contract
        if position.contract_type == "STK":
            return Stock(
                symbol=position.symbol,
                exchange="SMART",
                currency=position.currency
            )
        elif position.contract_type == "OPT":
            # 期权需要更多信息，这里简化处理
            return Option(
                symbol=position.symbol,
                exchange="SMART",
                currency=position.currency
            )
        elif position.contract_type == "FUT":
            return Future(
                symbol=position.symbol,
                exchange=position.exchange,
                currency=position.currency
            )
        elif position.contract_type == "CASH":
            return Forex(position.local_symbol or position.symbol)
        else:
            # 默认当作股票处理
            return Stock(
                symbol=position.symbol,
                exchange="SMART",
                currency=position.currency
            )

