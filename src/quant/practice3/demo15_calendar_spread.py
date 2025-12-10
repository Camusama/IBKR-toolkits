"""
Demo 15: Calendar Spread (日历价差)

================================================================================
📌 策略原理
================================================================================
Calendar Spread = 卖出近期期权 + 买入远期期权（同行权价）

核心逻辑：近期期权时间衰减更快，赚取 Theta 差

================================================================================
📌 参数说明
================================================================================
CAL_FRONT_IDX=1   # 近期到期日索引
CAL_BACK_IDX=3    # 远期到期日索引

================================================================================
"""
import asyncio
import os
import math
import logging
from datetime import datetime
from typing import Optional, Tuple
from dataclasses import dataclass, field

from ib_async import IB, Stock, Option

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "25"))

SYMBOL = os.getenv("CAL_SYMBOL", "AAPL")
EXCHANGE = os.getenv("CAL_EXCHANGE", "SMART")
CURRENCY = os.getenv("CAL_CURRENCY", "USD")

NUM_CONTRACTS = int(os.getenv("CAL_CONTRACTS", "1"))
FRONT_EXPIRY_IDX = int(os.getenv("CAL_FRONT_IDX", "1"))
BACK_EXPIRY_IDX = int(os.getenv("CAL_BACK_IDX", "3"))
PROFIT_TARGET_PCT = float(os.getenv("CAL_PROFIT_TARGET", "0.30"))
STOP_LOSS_PCT = float(os.getenv("CAL_STOP_LOSS", "0.50"))
CHECK_INTERVAL_SEC = int(os.getenv("CAL_CHECK_INTERVAL", "60"))
FALLBACK_PRICE = float(os.getenv("CAL_FALLBACK_PRICE", "280"))

USE_DELAYED_DATA = os.getenv("CAL_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("CAL_SIMULATION", "true").lower() == "true"

shutdown_requested = False


@dataclass
class CalendarPosition:
    strike: float = 0.0
    front_expiry: str = ""
    back_expiry: str = ""
    contracts: int = 0
    initial_cost: float = 0.0
    current_value: float = 0.0

    def get_days_to_front_expiry(self) -> int:
        if not self.front_expiry:
            return 0
        return (datetime.strptime(self.front_expiry, "%Y%m%d") - datetime.now()).days


@dataclass
class StrategyState:
    position: CalendarPosition = field(default_factory=CalendarPosition)
    start_time: Optional[datetime] = None
    current_price: float = 0.0
    front_option: Optional[Option] = None
    back_option: Optional[Option] = None
    net_theta: float = 0.0

    def get_pnl(self) -> float:
        return self.position.current_value - self.position.initial_cost

    def get_pnl_pct(self) -> float:
        if self.position.initial_cost == 0:
            return 0.0
        return self.get_pnl() / self.position.initial_cost


async def connect_ib() -> IB:
    ib = IB()
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    ib.reqMarketDataType(3 if USE_DELAYED_DATA else 1)
    return ib


async def get_stock_price(ib: IB, stock: Stock) -> float:
    ticker = ib.reqMktData(stock, "", False, False)
    await asyncio.sleep(2)
    price = ticker.last or ticker.close or FALLBACK_PRICE
    ib.cancelMktData(stock)
    return price if price and not math.isnan(price) else FALLBACK_PRICE


async def get_option_with_greeks(ib: IB, option: Option) -> Tuple[float, float]:
    ticker = ib.reqMktData(option, "106", False, False)
    await asyncio.sleep(3)
    price = ticker.last or ticker.close or (
        (ticker.bid or 0) + (ticker.ask or 0)) / 2
    theta = ticker.modelGreeks.theta if ticker.modelGreeks else 0.0
    ib.cancelMktData(option)
    return (price if price and not math.isnan(price) else 0.0, theta or 0.0)


async def find_option(ib: IB, stock: Stock, right: str, strike: float, expiry: str) -> Optional[Option]:
    option = Option(stock.symbol, expiry, strike, right, "SMART")
    try:
        qualified = await ib.qualifyContractsAsync(option)
        return qualified[0] if qualified else None
    except:
        return None


async def get_option_chain_info(ib: IB, stock: Stock) -> Tuple[list, list]:
    chains = await ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)
    if not chains:
        return [], []
    chain = next((c for c in chains if c.exchange == "SMART"), chains[0])
    today = datetime.now().strftime("%Y%m%d")
    return sorted([e for e in chain.expirations if e > today]), sorted(chain.strikes)


def print_status(state: StrategyState, reason: str = ""):
    pos = state.position
    print("\n" + "=" * 60)
    print(f"📅 Calendar Spread 状态 {'(' + reason + ')' if reason else ''}")
    print("=" * 60)
    print(f"股价: ${state.current_price:.2f} | 行权价: ${pos.strike:.2f}")
    print(f"近期到期: {pos.front_expiry} ({pos.get_days_to_front_expiry()}天)")
    print(f"远期到期: {pos.back_expiry}")
    print(f"净 Theta: ${state.net_theta:+.3f}/天")
    print(f"盈亏: ${state.get_pnl():+.2f} ({state.get_pnl_pct():+.1%})")
    print("=" * 60)


async def build_calendar(ib: IB, stock: Stock, state: StrategyState):
    price = await get_stock_price(ib, stock)
    state.current_price = price

    expiries, strikes = await get_option_chain_info(ib, stock)
    if len(expiries) < BACK_EXPIRY_IDX + 1:
        raise RuntimeError("期权链到期日不足")

    atm_strike = min(strikes, key=lambda x: abs(x - price))
    front_expiry, back_expiry = expiries[FRONT_EXPIRY_IDX], expiries[BACK_EXPIRY_IDX]

    state.front_option = await find_option(ib, stock, "C", atm_strike, front_expiry)
    state.back_option = await find_option(ib, stock, "C", atm_strike, back_expiry)

    if not state.front_option or not state.back_option:
        raise RuntimeError("无法获取期权合约")

    front_price, front_theta = await get_option_with_greeks(ib, state.front_option)
    back_price, back_theta = await get_option_with_greeks(ib, state.back_option)

    state.net_theta = -front_theta + back_theta
    net_cost = (back_price - front_price) * 100 * NUM_CONTRACTS

    if SIMULATION_MODE:
        logger.info(
            f"[模拟] Calendar: 卖近期 ${front_price:.2f}, 买远期 ${back_price:.2f}")

    state.position = CalendarPosition(
        strike=atm_strike, front_expiry=front_expiry, back_expiry=back_expiry,
        contracts=NUM_CONTRACTS, initial_cost=net_cost, current_value=net_cost)


async def update_position_value(ib: IB, state: StrategyState):
    if not state.front_option or not state.back_option:
        return
    front_price, front_theta = await get_option_with_greeks(ib, state.front_option)
    back_price, back_theta = await get_option_with_greeks(ib, state.back_option)
    state.net_theta = -front_theta + back_theta
    state.position.current_value = (
        back_price - front_price) * 100 * NUM_CONTRACTS


async def run_calendar(ib: IB):
    global shutdown_requested
    logger.info("📅 启动 Calendar Spread 策略")

    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]

    state = StrategyState(start_time=datetime.now())
    await build_calendar(ib, stock, state)
    print_status(state, "建仓")

    try:
        while not shutdown_requested:
            await asyncio.sleep(CHECK_INTERVAL_SEC)
            state.current_price = await get_stock_price(ib, stock)
            await update_position_value(ib, state)

            pnl_pct = state.get_pnl_pct()
            logger.info(
                f"股价: ${state.current_price:.2f} | P&L: {pnl_pct:+.1%}")

            if state.position.get_days_to_front_expiry() <= 1:
                break
            if pnl_pct >= PROFIT_TARGET_PCT or pnl_pct <= -STOP_LOSS_PCT:
                break
    except KeyboardInterrupt:
        pass

    print_status(state, "结束")


def handle_shutdown(signum, frame):
    global shutdown_requested
    shutdown_requested = True


async def main():
    import signal
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    ib = await connect_ib()
    try:
        await run_calendar(ib)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    print("📅 Calendar Spread - 利用时间衰减差异获利")
    asyncio.run(main())
