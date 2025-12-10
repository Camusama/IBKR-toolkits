"""
Demo 10: Grid Trading Strategy (网格交易策略)

策略原理：
1. 在当前价格上下设置多层网格
2. 价格下跌触及下层网格时买入
3. 价格上涨触及上层网格时卖出
4. 自动在震荡市场中低买高卖
"""
import asyncio
import os
import math
import logging
from datetime import datetime
from typing import Optional, Dict, List
from dataclasses import dataclass, field

from ib_async import IB, Stock, MarketOrder

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ========== 配置 ==========
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "20"))

SYMBOL = os.getenv("GRID_SYMBOL", "AAPL")
EXCHANGE = os.getenv("GRID_EXCHANGE", "SMART")
CURRENCY = os.getenv("GRID_CURRENCY", "USD")

GRID_SIZE = float(os.getenv("GRID_SIZE", "0.02"))  # 网格间距 2%
GRID_LEVELS = int(os.getenv("GRID_LEVELS", "5"))  # 上下各5层
SHARES_PER_GRID = int(os.getenv("GRID_SHARES", "10"))  # 每格股数
CHECK_INTERVAL_SEC = int(os.getenv("GRID_CHECK_INTERVAL", "30"))
FALLBACK_PRICE = float(os.getenv("GRID_FALLBACK_PRICE", "280"))
MAX_TOTAL_SHARES = int(os.getenv("GRID_MAX_SHARES", "200"))
STOP_LOSS_PCT = float(os.getenv("GRID_STOP_LOSS", "0.15"))

USE_DELAYED_DATA = os.getenv("GRID_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("GRID_SIMULATION", "true").lower() == "true"

shutdown_requested = False


@dataclass
class GridLevel:
    price: float
    level: int
    triggered: bool = False
    filled_shares: int = 0


@dataclass
class Position:
    total_shares: int = 0
    total_cost: float = 0.0
    realized_pnl: float = 0.0
    grid_trades: int = 0

    @property
    def avg_price(self) -> float:
        return self.total_cost / self.total_shares if self.total_shares > 0 else 0.0


@dataclass
class StrategyState:
    position: Position = field(default_factory=Position)
    start_time: Optional[datetime] = None
    base_price: float = 0.0
    current_price: float = 0.0
    grids: Dict[int, GridLevel] = field(default_factory=dict)

    def get_unrealized_pnl(self) -> float:
        if self.position.total_shares == 0:
            return 0.0
        return (self.current_price - self.position.avg_price) * self.position.total_shares

    def get_total_pnl(self) -> float:
        return self.position.realized_pnl + self.get_unrealized_pnl()


def create_grids(base_price: float) -> Dict[int, GridLevel]:
    grids = {}
    for i in range(1, GRID_LEVELS + 1):
        grids[-i] = GridLevel(price=base_price *
                              (1 - GRID_SIZE * i), level=-i)  # 买入区
        grids[i] = GridLevel(price=base_price *
                             (1 + GRID_SIZE * i), level=i)   # 卖出区
    return grids


async def connect_ib() -> IB:
    ib = IB()
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    ib.reqMarketDataType(3 if USE_DELAYED_DATA else 1)
    return ib


async def get_stock_price(ib: IB, stock: Stock) -> float:
    ticker = ib.reqMktData(stock, "", False, False)
    await asyncio.sleep(2)
    price = ticker.last or ticker.close or FALLBACK_PRICE
    if price is None or (isinstance(price, float) and math.isnan(price)):
        price = FALLBACK_PRICE
    ib.cancelMktData(stock)
    return price


async def execute_trade(ib: IB, stock: Stock, action: str, qty: int,
                        price: float, state: StrategyState, level: int) -> bool:
    if qty == 0:
        return False

    if SIMULATION_MODE:
        logger.info(f"[模拟] {action} {qty} 股 @ ${price:.2f} (L{level:+d})")
        if action == "BUY":
            state.position.total_shares += qty
            state.position.total_cost += price * qty
        else:
            if state.position.total_shares > 0:
                realized = (price - state.position.avg_price) * qty
                state.position.realized_pnl += realized
                state.position.total_shares -= qty
                state.position.total_cost = state.position.avg_price * \
                    state.position.total_shares if state.position.total_shares > 0 else 0
                logger.info(f"  网格收益: ${realized:+.2f}")
        state.position.grid_trades += 1
        state.grids[level].triggered = True
        return True
    return False


def print_status(state: StrategyState, reason: str = ""):
    pos = state.position
    print("\n" + "=" * 60)
    print(f"📊 网格交易状态 {'(' + reason + ')' if reason else ''}")
    print("=" * 60)
    elapsed = (datetime.now() -
               state.start_time).total_seconds() if state.start_time else 0
    print(
        f"⏰ 运行: {int(elapsed)}s | 💰 价格: ${state.current_price:.2f} | 🎯 基准: ${state.base_price:.2f}")
    print("-" * 60)

    # 网格可视化
    print("【网格状态】")
    for level in range(GRID_LEVELS, -GRID_LEVELS - 1, -1):
        if level == 0:
            print(f"  ➡️  当前价格: ${state.current_price:.2f}")
            continue
        grid = state.grids.get(level)
        if grid:
            status = "✅" if grid.triggered else "⬜"
            action = "卖" if level > 0 else "买"
            print(f"  {status} L{level:+2d}: ${grid.price:.2f} ({action})")

    print("-" * 60)
    print(f"【持仓】{pos.total_shares} 股 | 成本 ${pos.avg_price:.2f}")
    print(
        f"【P&L】已实现: ${pos.realized_pnl:+.2f} | 未实现: ${state.get_unrealized_pnl():+.2f} | 总: ${state.get_total_pnl():+.2f}")
    print("=" * 60)


async def close_all_positions(ib: IB, stock: Stock, state: StrategyState):
    if state.position.total_shares <= 0:
        return
    price = await get_stock_price(ib, stock)
    realized = (price - state.position.avg_price) * state.position.total_shares
    state.position.realized_pnl += realized
    logger.info(
        f"[模拟] 平仓 {state.position.total_shares} 股 @ ${price:.2f}, 盈亏: ${realized:+.2f}")
    state.position.total_shares = 0
    state.position.total_cost = 0


async def run_grid_strategy(ib: IB):
    global shutdown_requested

    logger.info("🚀 启动网格交易策略")
    logger.info(
        f"标的: {SYMBOL} | 网格: {GRID_SIZE:.1%} × {GRID_LEVELS}层 | 每格: {SHARES_PER_GRID}股")
    logger.info("💡 按 Ctrl+C 退出并平仓")

    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]

    base_price = await get_stock_price(ib, stock)

    state = StrategyState()
    state.start_time = datetime.now()
    state.base_price = base_price
    state.current_price = base_price
    state.grids = create_grids(base_price)

    print_status(state, "启动")

    check_count = 0
    exit_reason = "手动退出"

    try:
        while not shutdown_requested:
            await asyncio.sleep(CHECK_INTERVAL_SEC)
            check_count += 1

            price = await get_stock_price(ib, stock)
            state.current_price = price

            logger.info(f"--- 检查 #{check_count} | ${price:.2f} ---")

            # 检查网格触发
            for level, grid in state.grids.items():
                if grid.triggered:
                    continue
                if level < 0 and price <= grid.price and state.position.total_shares < MAX_TOTAL_SHARES:
                    logger.info(f"🟢 买入网格 L{level}")
                    await execute_trade(ib, stock, "BUY", SHARES_PER_GRID, price, state, level)
                    print_status(state, "买入")
                elif level > 0 and price >= grid.price and state.position.total_shares > 0:
                    qty = min(SHARES_PER_GRID, state.position.total_shares)
                    logger.info(f"🔴 卖出网格 L{level}")
                    await execute_trade(ib, stock, "SELL", qty, price, state, level)
                    print_status(state, "卖出")

    except KeyboardInterrupt:
        exit_reason = "用户中断"

    logger.info(f"📤 退出: {exit_reason}")
    await close_all_positions(ib, stock, state)
    print_status(state, "结束")
    print(
        f"\n📋 总结: 运行 {check_count} 次检查, {state.position.grid_trades} 次交易, P&L: ${state.position.realized_pnl:+.2f}")


def handle_shutdown(signum, frame):
    global shutdown_requested
    shutdown_requested = True


async def main():
    import signal
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    ib = await connect_ib()
    try:
        await run_grid_strategy(ib)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    print("🎯 网格交易策略 - 震荡市自动低买高卖\n按 Ctrl+C 退出并平仓\n")
    asyncio.run(main())
