"""
Demo 17: Iron Butterfly (铁蝴蝶策略)

================================================================================
📌 策略原理
================================================================================
Iron Butterfly = 卖出 ATM Straddle + 买入 OTM 保护

结构示意：
  买Put($266) ← 卖Put($280) = 卖Call($280) → 买Call($294)
  └── 下翼 ──┘   └──── ATM Straddle ────┘   └── 上翼 ──┘

与 Iron Condor 的区别：
┌────────────┬────────────────────────────────────┐
│ Iron Condor │ 卖虚值(OTM)，盈利区间宽，权利金少 │
│ Iron Butterfly │ 卖平值(ATM)，盈利区间窄，权利金多 │
└────────────┴────────────────────────────────────┘

================================================================================
📌 参数说明
================================================================================
IB_WING_PCT=0.05  # 翼宽 5%（保护距离）

示例（股价 $280）：
  - 买 Put $266
  - 卖 Put $280（ATM）
  - 卖 Call $280（ATM）
  - 买 Call $294
  - 最大盈利点：$280（股价不动）

================================================================================
📌 使用场景
================================================================================
✅ 适合：
   - 强烈预期股价横盘不动
   - 财报刚过，IV 回落期
   - 想收取更多权利金

❌ 不适合：
   - 预期大涨大跌
   - 临近重大事件

================================================================================
📌 盈亏分析
================================================================================
最大盈利：初始权利金（股价 = ATM 行权价）
最大亏损：翼宽 × 100 - 权利金（股价超出翅膀）
盈利区间：比 Iron Condor 窄

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
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "27"))

SYMBOL = os.getenv("IBF_SYMBOL", "AAPL")
EXCHANGE = os.getenv("IBF_EXCHANGE", "SMART")
CURRENCY = os.getenv("IBF_CURRENCY", "USD")

NUM_CONTRACTS = int(os.getenv("IBF_CONTRACTS", "1"))
WING_PCT = float(os.getenv("IBF_WING_PCT", "0.05"))
PROFIT_TARGET_PCT = float(os.getenv("IBF_PROFIT_TARGET", "0.50"))
STOP_LOSS_PCT = float(os.getenv("IBF_STOP_LOSS", "1.0"))
CHECK_INTERVAL_SEC = int(os.getenv("IBF_CHECK_INTERVAL", "60"))
FALLBACK_PRICE = float(os.getenv("IBF_FALLBACK_PRICE", "280"))

USE_DELAYED_DATA = os.getenv("IBF_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("IBF_SIMULATION", "true").lower() == "true"

shutdown_requested = False


@dataclass
class IronButterflyPosition:
    """Iron Butterfly 仓位"""
    atm_strike: float = 0.0      # ATM 行权价（卖Call+Put）
    lower_strike: float = 0.0    # 下翼（买Put）
    upper_strike: float = 0.0    # 上翼（买Call）
    expiry: str = ""
    contracts: int = 0
    initial_credit: float = 0.0
    current_value: float = 0.0

    def get_max_profit(self) -> float:
        return self.initial_credit

    def get_max_loss(self) -> float:
        wing_width = (self.atm_strike - self.lower_strike) * \
            100 * self.contracts
        return wing_width - self.initial_credit

    def get_profit_range(self) -> Tuple[float, float]:
        # 盈利区间 = ATM ± 权利金/100
        margin = self.initial_credit / \
            (100 * self.contracts) if self.contracts else 0
        return (self.atm_strike - margin, self.atm_strike + margin)


@dataclass
class StrategyState:
    position: IronButterflyPosition = field(
        default_factory=IronButterflyPosition)
    start_time: Optional[datetime] = None
    current_price: float = 0.0

    long_put: Optional[Option] = None
    short_put: Optional[Option] = None
    short_call: Optional[Option] = None
    long_call: Optional[Option] = None

    def get_pnl(self) -> float:
        return self.position.initial_credit - self.position.current_value

    def get_pnl_pct(self) -> float:
        if self.position.initial_credit == 0:
            return 0.0
        return self.get_pnl() / self.position.initial_credit


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


async def get_option_price(ib: IB, option: Option) -> float:
    ticker = ib.reqMktData(option, "", False, False)
    await asyncio.sleep(2)
    price = ticker.last or ticker.close or (
        (ticker.bid or 0) + (ticker.ask or 0)) / 2
    ib.cancelMktData(option)
    return price if price and not math.isnan(price) else 0.0


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
    print(f"🦋 Iron Butterfly 状态 {'(' + reason + ')' if reason else ''}")
    print("=" * 60)
    print(f"股价: ${state.current_price:.2f} | ATM: ${pos.atm_strike:.2f}")
    print("-" * 60)
    print("【结构】")
    print(f"  买Put ${pos.lower_strike:.0f} ← 卖Put ${pos.atm_strike:.0f} = 卖Call ${pos.atm_strike:.0f} → 买Call ${pos.upper_strike:.0f}")

    # 价格可视化
    range_width = pos.upper_strike - pos.lower_strike
    if range_width > 0:
        price_pos = (state.current_price - pos.lower_strike) / range_width
        bar_len = 40
        price_idx = int(price_pos * bar_len)
        atm_idx = int((pos.atm_strike - pos.lower_strike) /
                      range_width * bar_len)
        bar = ["─"] * bar_len
        if 0 <= atm_idx < bar_len:
            bar[atm_idx] = "◆"
        if 0 <= price_idx < bar_len:
            bar[price_idx] = "●"
        print(f"  [{(''.join(bar))}]")
        print(f"  ● 当前  ◆ 最大盈利点")

    # 距离分析
    distance = abs(state.current_price - pos.atm_strike) / pos.atm_strike * 100
    if distance < 1:
        print(f"  ✅ 接近最大盈利点！距离 {distance:.1f}%")
    elif distance < 3:
        print(f"  🟡 距离最大盈利点 {distance:.1f}%")
    else:
        print(f"  ⚠️ 偏离最大盈利点 {distance:.1f}%")

    profit_range = pos.get_profit_range()
    print(f"  盈利区间: ${profit_range[0]:.2f} ~ ${profit_range[1]:.2f}")

    print("-" * 60)
    print("【盈亏】")
    print(f"  初始权利金: ${pos.initial_credit:.2f}")
    print(f"  最大盈利: ${pos.get_max_profit():.2f}（股价=${pos.atm_strike:.0f}）")
    print(f"  最大亏损: ${pos.get_max_loss():.2f}")
    print(f"  当前盈亏: ${state.get_pnl():+.2f} ({state.get_pnl_pct():+.1%})")
    print("=" * 60)


async def build_iron_butterfly(ib: IB, stock: Stock, state: StrategyState):
    price = await get_stock_price(ib, stock)
    state.current_price = price

    expiries, strikes = await get_option_chain_info(ib, stock)
    if not expiries or not strikes:
        raise RuntimeError("无法获取期权链")

    expiry = expiries[1] if len(expiries) > 1 else expiries[0]

    # ATM 行权价
    atm_strike = min(strikes, key=lambda x: abs(x - price))
    lower_strike = min(strikes, key=lambda x: abs(
        x - price * (1 - WING_PCT)) if x < atm_strike else float('inf'))
    upper_strike = min(strikes, key=lambda x: abs(
        x - price * (1 + WING_PCT)) if x > atm_strike else float('inf'))

    logger.info(f"构建 Iron Butterfly @ {expiry}")
    logger.info(
        f"  买Put ${lower_strike} | 卖Put+Call ${atm_strike} | 买Call ${upper_strike}")

    state.long_put = await find_option(ib, stock, "P", lower_strike, expiry)
    state.short_put = await find_option(ib, stock, "P", atm_strike, expiry)
    state.short_call = await find_option(ib, stock, "C", atm_strike, expiry)
    state.long_call = await find_option(ib, stock, "C", upper_strike, expiry)

    if not all([state.long_put, state.short_put, state.short_call, state.long_call]):
        raise RuntimeError("无法获取所有期权")

    lp_price = await get_option_price(ib, state.long_put)
    sp_price = await get_option_price(ib, state.short_put)
    sc_price = await get_option_price(ib, state.short_call)
    lc_price = await get_option_price(ib, state.long_call)

    # 净收入 = 卖出 - 买入
    net_credit = (sp_price + sc_price - lp_price -
                  lc_price) * 100 * NUM_CONTRACTS

    if SIMULATION_MODE:
        logger.info(f"[模拟] Iron Butterfly 净收入: ${net_credit:.2f}")

    state.position = IronButterflyPosition(
        atm_strike=atm_strike, lower_strike=lower_strike, upper_strike=upper_strike,
        expiry=expiry, contracts=NUM_CONTRACTS, initial_credit=net_credit, current_value=net_credit)


async def update_position_value(ib: IB, state: StrategyState):
    if not all([state.long_put, state.short_put, state.short_call, state.long_call]):
        return
    lp = await get_option_price(ib, state.long_put)
    sp = await get_option_price(ib, state.short_put)
    sc = await get_option_price(ib, state.short_call)
    lc = await get_option_price(ib, state.long_call)
    state.position.current_value = (sp + sc - lp - lc) * 100 * NUM_CONTRACTS


async def run_iron_butterfly(ib: IB):
    global shutdown_requested
    logger.info("🦋 启动 Iron Butterfly 策略")

    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]

    state = StrategyState(start_time=datetime.now())
    await build_iron_butterfly(ib, stock, state)
    print_status(state, "建仓")

    try:
        while not shutdown_requested:
            await asyncio.sleep(CHECK_INTERVAL_SEC)
            state.current_price = await get_stock_price(ib, stock)
            await update_position_value(ib, state)

            pnl_pct = state.get_pnl_pct()
            logger.info(
                f"股价: ${state.current_price:.2f} | P&L: {pnl_pct:+.1%}")

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
        await run_iron_butterfly(ib)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    print("🦋 Iron Butterfly - 卖ATM期权，收取高权利金")
    asyncio.run(main())
