"""
Demo 16: Jade Lizard (翡翠蜥蜴策略)

================================================================================
📌 策略原理
================================================================================
Jade Lizard = 卖出虚值 Put + 卖出 Call Spread (垂直价差)

结构示意：
  卖Put($266) ← 当前价$280 → 卖Call($294) → 买Call($308)
  └─ 收权利金 ─┘              └─── Call Spread ───┘

  ● 下方：裸卖 Put，无限亏损风险
  ● 上方：Call Spread，风险有限

核心逻辑：
1. 预期股价不跌 → 卖 Put 收权利金
2. 预期股价不暴涨 → 卖 Call Spread 收权利金
3. 上方有保护（买入更高 Call），下方无保护

关键要点：
- 总权利金 > Call Spread 宽度 → 上方无亏损风险！
- 只在股价大跌时亏损

================================================================================
📌 参数说明
================================================================================
JL_PUT_OTM=0.05        # 卖Put虚值 5%
JL_CALL_SHORT_OTM=0.05  # 卖Call虚值 5%
JL_CALL_LONG_OTM=0.10   # 买Call虚值 10%

示例（股价 $280）：
  - 卖 Put $266 @ $2.0
  - 卖 Call $294 @ $3.0
  - 买 Call $308 @ $1.5
  - 净收入: $2.0 + $3.0 - $1.5 = $3.5 × 100 = $350
  - Call宽度: $308 - $294 = $14 × 100 = $1400
  - 因为 $350 < $1400，上方仍有亏损风险

================================================================================
📌 使用场景
================================================================================
✅ 适合：
   - 看多或中性偏多
   - 愿意在低位接股票
   - IV 较高时建仓

❌ 不适合：
   - 预期大跌
   - 不愿承担被行权买股的风险

================================================================================
📌 风险分析
================================================================================
上涨风险：Call Spread 宽度 - 总权利金（有限）
下跌风险：Put 行权价 × 100 - 总权利金（可能很大）

最佳情况：股价在 Put 和 Call 之间到期，收全部权利金

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
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "26"))

SYMBOL = os.getenv("JL_SYMBOL", "AAPL")
EXCHANGE = os.getenv("JL_EXCHANGE", "SMART")
CURRENCY = os.getenv("JL_CURRENCY", "USD")

NUM_CONTRACTS = int(os.getenv("JL_CONTRACTS", "1"))
PUT_OTM_PCT = float(os.getenv("JL_PUT_OTM", "0.05"))
CALL_SHORT_OTM_PCT = float(os.getenv("JL_CALL_SHORT_OTM", "0.05"))
CALL_LONG_OTM_PCT = float(os.getenv("JL_CALL_LONG_OTM", "0.10"))
PROFIT_TARGET_PCT = float(os.getenv("JL_PROFIT_TARGET", "0.50"))
STOP_LOSS_PCT = float(os.getenv("JL_STOP_LOSS", "1.0"))
CHECK_INTERVAL_SEC = int(os.getenv("JL_CHECK_INTERVAL", "60"))
FALLBACK_PRICE = float(os.getenv("JL_FALLBACK_PRICE", "280"))

USE_DELAYED_DATA = os.getenv("JL_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("JL_SIMULATION", "true").lower() == "true"

shutdown_requested = False


@dataclass
class JadeLizardPosition:
    """Jade Lizard 仓位"""
    put_strike: float = 0.0       # 卖Put
    short_call_strike: float = 0.0  # 卖Call
    long_call_strike: float = 0.0   # 买Call（保护）
    expiry: str = ""
    contracts: int = 0
    initial_credit: float = 0.0
    current_value: float = 0.0

    def get_profit_range(self) -> Tuple[float, float]:
        return (self.put_strike, self.short_call_strike)

    def get_upside_risk(self) -> float:
        # 上方最大亏损 = Call Spread 宽度 - 权利金
        spread_width = (self.long_call_strike -
                        self.short_call_strike) * 100 * self.contracts
        return max(0, spread_width - self.initial_credit)

    def get_downside_break_even(self) -> float:
        # 下方盈亏平衡 = Put 行权价 - 权利金/100
        return self.put_strike - self.initial_credit / (100 * self.contracts)


@dataclass
class StrategyState:
    position: JadeLizardPosition = field(default_factory=JadeLizardPosition)
    start_time: Optional[datetime] = None
    current_price: float = 0.0

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
    print(f"🦎 Jade Lizard 状态 {'(' + reason + ')' if reason else ''}")
    print("=" * 60)
    print(f"股价: ${state.current_price:.2f}")
    print("-" * 60)
    print("【结构】")
    print(
        f"  卖 Put ${pos.put_strike:.0f} | 卖 Call ${pos.short_call_strike:.0f} | 买 Call ${pos.long_call_strike:.0f}")
    print(f"  盈利区间: ${pos.put_strike:.0f} ~ ${pos.short_call_strike:.0f}")

    # 位置判断
    if state.current_price < pos.put_strike:
        print(f"  ⚠️ 低于 Put 行权价！可能被行权")
    elif state.current_price > pos.short_call_strike:
        print(f"  ⚠️ 高于 Call 行权价！")
    else:
        print(f"  ✅ 价格在盈利区间")

    print("-" * 60)
    print("【风险】")
    print(f"  上方风险: ${pos.get_upside_risk():.2f}（有限）")
    print(f"  下方盈亏平衡: ${pos.get_downside_break_even():.2f}")
    print("-" * 60)
    print("【盈亏】")
    print(f"  初始权利金: ${pos.initial_credit:.2f}")
    print(f"  当前价值: ${pos.current_value:.2f}")
    print(f"  盈亏: ${state.get_pnl():+.2f} ({state.get_pnl_pct():+.1%})")
    print("=" * 60)


async def build_jade_lizard(ib: IB, stock: Stock, state: StrategyState):
    price = await get_stock_price(ib, stock)
    state.current_price = price

    expiries, strikes = await get_option_chain_info(ib, stock)
    if not expiries or not strikes:
        raise RuntimeError("无法获取期权链")

    expiry = expiries[1] if len(expiries) > 1 else expiries[0]

    # 计算行权价
    put_strike = min(strikes, key=lambda x: abs(
        x - price * (1 - PUT_OTM_PCT)) if x < price else float('inf'))
    short_call = min(strikes, key=lambda x: abs(
        x - price * (1 + CALL_SHORT_OTM_PCT)) if x > price else float('inf'))
    long_call = min(strikes, key=lambda x: abs(
        x - price * (1 + CALL_LONG_OTM_PCT)) if x > short_call else float('inf'))

    logger.info(f"构建 Jade Lizard @ {expiry}")
    logger.info(
        f"  卖 Put ${put_strike} | 卖 Call ${short_call} | 买 Call ${long_call}")

    state.short_put = await find_option(ib, stock, "P", put_strike, expiry)
    state.short_call = await find_option(ib, stock, "C", short_call, expiry)
    state.long_call = await find_option(ib, stock, "C", long_call, expiry)

    if not all([state.short_put, state.short_call, state.long_call]):
        raise RuntimeError("无法获取所有期权")

    put_price = await get_option_price(ib, state.short_put)
    sc_price = await get_option_price(ib, state.short_call)
    lc_price = await get_option_price(ib, state.long_call)

    net_credit = (put_price + sc_price - lc_price) * 100 * NUM_CONTRACTS

    if SIMULATION_MODE:
        logger.info(f"[模拟] Jade Lizard 净收入: ${net_credit:.2f}")

    state.position = JadeLizardPosition(
        put_strike=put_strike, short_call_strike=short_call, long_call_strike=long_call,
        expiry=expiry, contracts=NUM_CONTRACTS, initial_credit=net_credit, current_value=net_credit)


async def update_position_value(ib: IB, state: StrategyState):
    if not all([state.short_put, state.short_call, state.long_call]):
        return
    put_price = await get_option_price(ib, state.short_put)
    sc_price = await get_option_price(ib, state.short_call)
    lc_price = await get_option_price(ib, state.long_call)
    state.position.current_value = (
        put_price + sc_price - lc_price) * 100 * NUM_CONTRACTS


async def run_jade_lizard(ib: IB):
    global shutdown_requested
    logger.info("🦎 启动 Jade Lizard 策略")

    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]

    state = StrategyState(start_time=datetime.now())
    await build_jade_lizard(ib, stock, state)
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
        await run_jade_lizard(ib)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    print("🦎 Jade Lizard - 卖Put + Call Spread，偏多策略")
    asyncio.run(main())
