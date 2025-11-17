"""持仓数据模型"""

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional


@dataclass
class Position:
    """持仓信息数据类"""

    # 基本信息
    symbol: str  # 股票代码
    contract_type: str  # 合约类型（STK, OPT, FUT等）
    exchange: str  # 交易所
    currency: str  # 货币

    # 持仓信息
    position: float  # 持仓数量
    avg_cost: float  # 平均成本
    market_price: float  # 市场价格
    market_value: float  # 市值
    unrealized_pnl: float  # 未实现盈亏
    realized_pnl: float  # 已实现盈亏

    # 可选信息
    account: Optional[str] = None  # 账户
    multiplier: Optional[int] = 1  # 合约乘数
    local_symbol: Optional[str] = None  # 本地代码

    # 期权特有信息
    strike: Optional[float] = None  # 行权价
    expiry: Optional[str] = None  # 到期日
    right: Optional[str] = None  # C=看涨, P=看跌
    delta: Optional[float] = None  # Delta值
    gamma: Optional[float] = None  # Gamma值
    theta: Optional[float] = None  # Theta值
    vega: Optional[float] = None  # Vega值

    # 时间戳
    update_time: Optional[datetime] = None

    def __post_init__(self):
        """初始化后处理"""
        if self.update_time is None:
            self.update_time = datetime.now()

    @property
    def pnl_percent(self) -> float:
        """盈亏百分比"""
        if self.avg_cost and self.position:
            cost_basis = abs(self.avg_cost * self.position)
            if cost_basis > 0:
                return (self.unrealized_pnl / cost_basis) * 100
        return 0.0

    def to_dict(self) -> dict:
        """转换为字典

        Returns:
            包含所有字段的字典
        """
        data = asdict(self)
        data['pnl_percent'] = self.pnl_percent
        if self.update_time:
            data['update_time'] = self.update_time.isoformat()
        return data

    def to_display_dict(self) -> dict:
        """转换为显示用的字典（格式化数值）

        Returns:
            格式化后的字典
        """
        return {
            '代码': self.symbol,
            '类型': self.contract_type,
            '交易所': self.exchange,
            '货币': self.currency,
            '持仓数量': f"{self.position:.2f}",
            '平均成本': f"{self.avg_cost:.2f}",
            '市场价格': f"{self.market_price:.2f}",
            '市值': f"{self.market_value:.2f}",
            '未实现盈亏': f"{self.unrealized_pnl:.2f}",
            '已实现盈亏': f"{self.realized_pnl:.2f}",
            '盈亏比例': f"{self.pnl_percent:.2f}%",
            '更新时间': self.update_time.strftime('%Y-%m-%d %H:%M:%S') if self.update_time else '',
        }


@dataclass
class PositionSummary:
    """持仓汇总数据类"""

    total_positions: int  # 总持仓数
    total_market_value: float  # 总市值
    total_unrealized_pnl: float  # 总未实现盈亏
    total_realized_pnl: float  # 总已实现盈亏
    total_pnl: float  # 总盈亏
    positions: list[Position]  # 持仓列表
    update_time: datetime
    net_deposits: Optional[float] = None  # 总入金金额（可选）

    def __post_init__(self):
        """初始化后处理"""
        if self.update_time is None:
            self.update_time = datetime.now()

    @property
    def total_pnl_percent(self) -> float:
        """总盈亏百分比（基于持仓成本）"""
        total_cost = sum(abs(p.avg_cost * p.position) for p in self.positions)
        if total_cost > 0:
            return (self.total_unrealized_pnl / total_cost) * 100
        return 0.0

    @property
    def account_total_return(self) -> Optional[float]:
        """账户总收益（市值 - 入金）"""
        if self.net_deposits is not None:
            return self.total_market_value - self.net_deposits
        return None

    @property
    def account_total_return_percent(self) -> Optional[float]:
        """账户总收益率（基于入金）"""
        if self.net_deposits is not None and self.net_deposits > 0:
            return (self.account_total_return / self.net_deposits) * 100
        return None

    def to_dict(self) -> dict:
        """转换为字典

        Returns:
            包含汇总信息和持仓列表的字典
        """
        summary_dict = {
            'total_positions': self.total_positions,
            'total_market_value': self.total_market_value,
            'total_unrealized_pnl': self.total_unrealized_pnl,
            'total_realized_pnl': self.total_realized_pnl,
            'total_pnl': self.total_pnl,
            'total_pnl_percent': self.total_pnl_percent,
            'update_time': self.update_time.isoformat(),
        }

        # Add account return metrics if net_deposits is provided
        if self.net_deposits is not None:
            summary_dict['net_deposits'] = self.net_deposits
            summary_dict['account_total_return'] = self.account_total_return
            summary_dict['account_total_return_percent'] = self.account_total_return_percent

        return {
            'summary': summary_dict,
            'positions': [p.to_dict() for p in self.positions]
        }
