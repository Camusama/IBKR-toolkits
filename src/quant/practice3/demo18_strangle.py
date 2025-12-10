"""
Demo 18: Strangle (宽跨式策略)

================================================================================
📌 策略原理
================================================================================
Strangle = 买入虚值 Call + 买入虚值 Put（或反向卖出）

买入 Strangle（做多波动率）：
  买Put($266) ←← 当前价$280 →→ 买Call($294)
  └─ 下跌获利 ─┘              └─ 上涨获利 ─┘

卖出 Strangle（做空波动率）：
  卖Put($266) ←← 当前价$280 →→ 卖Call($294)
  └─ 收权利金 ─┘              └─ 收权利金 ─┘

与 Straddle 的区别：
┌───────────┬─────────────────────────────────┐
│ Straddle  │ ATM期权，成本高，盈亏平衡近     │
│ Strangle  │ OTM期权，成本低，盈亏平衡远     │
└───────────┴─────────────────────────────────┘

================================================================================
📌 参数说明
================================================================================
STR_OTM_PCT=0.05    # 虚值程度 5%
STR_DIRECTION=long  # long=买入做多波动率, short=卖出做空波动率

示例（股价 $280，买入 Strangle）：
  - 买 Put $266 @ $2.0
  - 买 Call $294 @ $1.5
  - 总成本: $3.5 × 100 = $350
  - 下方盈亏平衡: $266 - $3.5 = $262.5
  - 上方盈亏平衡: $294 + $3.5 = $297.5

================================================================================
📌 使用场景
================================================================================
买入 Strangle（做多波动率）：
  ✅ 预期大涨或大跌，但不确定方向
  ✅ 财报前、重大事件前
  ✅ IV 较低时（期权便宜）

卖出 Strangle（做空波动率）：
  ✅ 预期横盘
  ✅ 财报后 IV 回落
  ⚠️ 风险无限！必须严格止损

================================================================================
📌 风险分析
================================================================================
买入 Strangle：
  - 最大亏损：权利金（有限）
  - 最大盈利：无限

卖出 Strangle：
  - 最大盈利：权利金（有限）
  - 最大亏损：无限 ⚠️

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
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "28"))

SYMBOL = os.getenv("STR_SYMBOL", "AAPL")
EXCHANGE = os.getenv("STR_EXCHANGE", "SMART")
CURRENCY = os.getenv("STR_CURRENCY", "USD")

NUM_CONTRACTS = int(os.getenv("STR_CONTRACTS", "1"))
OTM_PCT = float(os.getenv("STR_OTM_PCT", "0.05"))
DIRECTION = os.getenv("STR_DIRECTION", "long")  # long 或 short
PROFIT_TARGET_PCT = float(os.getenv("STR_PROFIT_TARGET", "0.50"))
STOP_LOSS_PCT = float(os.getenv("STR_STOP_LOSS", "0.50"))
CHECK_INTERVAL_SEC = int(os.getenv("STR_CHECK_INTERVAL", "60"))
FALLBACK_PRICE = float(os.getenv("STR_FALLBACK_PRICE", "280"))

USE_DELAYED_DATA = os.getenv("STR_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("STR_SIMULATION", "true").lower() == "true"

shutdown_requested = False


@dataclass
class StranglePosition:
    """Strangle 仓位"""
    put_strike: float = 0.0
    call_strike: float = 0.0
    expiry: str = ""
    contracts: int = 0
    direction: str = "long"  # long=买入, short=卖出
    initial_cost: float = 0.0  # 买入=支出（正），卖出=收入（正）
    current_value: float = 0.0

    def get_break_even_down(self) -> float:
        if self.direction == "long":
            return self.put_strike - self.initial_cost / (100 * self.contracts)
        return self.put_strike - self.initial_cost / (100 * self.contracts)

    def get_break_even_up(self) -> float:
        if self.direction == "long":
            return self.call_strike + self.initial_cost / (100 * self.contracts)
        return self.call_strike + self.initial_cost / (100 * self.contracts)


@dataclass
class StrategyState:
    position: StranglePosition = field(default_factory=StranglePosition)
    start_time: Optional[datetime] = None
    current_price: float = 0.0

    put_option: Optional[Option] = None
    call_option: Optional[Option] = None

    def get_pnl(self) -> float:
        if self.position.direction == "long":
            # 买入：盈亏 = 当前价值 - 初始成本
            return self.position.current_value - self.position.initial_cost
        else:
            # 卖出：盈亏 = 初始收入 - 当前价值
            return self.position.initial_cost - self.position.current_value

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
    dir_emoji = "📈" if pos.direction == "long" else "📉"
    dir_text = "买入（做多波动率）" if pos.direction == "long" else "卖出（做空波动率）"

    print("\n" + "=" * 60)
    print(f"{dir_emoji} Strangle 状态 {dir_text} {'(' + reason + ')' if reason else ''}")
    print("=" * 60)
    print(f"股价: ${state.current_price:.2f}")
    print("-" * 60)
    print("【结构】")
    action = "买" if pos.direction == "long" else "卖"
    print(f"  {action} Put ${pos.put_strike:.0f} ←← 当前 ${state.current_price:.0f} →→ {action} Call ${pos.call_strike:.0f}")

    # 可视化
    be_down = pos.get_break_even_down()
    be_up = pos.get_break_even_up()
    range_start = min(be_down - 5, pos.put_strike - 10)
    range_end = max(be_up + 5, pos.call_strike + 10)
    bar_len = 50

    def to_idx(p):
        return int((p - range_start) / (range_end - range_start) * bar_len)

    bar = ["─"] * bar_len
    put_idx = to_idx(pos.put_strike)
    call_idx = to_idx(pos.call_strike)
    price_idx = to_idx(state.current_price)

    if 0 <= put_idx < bar_len:
        bar[put_idx] = "P"
    if 0 <= call_idx < bar_len:
        bar[call_idx] = "C"
    if 0 <= price_idx < bar_len:
        bar[price_idx] = "●"

    print(f"  [{(''.join(bar))}]")
    print(f"  P=Put行权价  C=Call行权价  ●=当前价格")

    print(f"  盈亏平衡: ${be_down:.2f} / ${be_up:.2f}")

    # 位置判断
    if state.current_price < pos.put_strike or state.current_price > pos.call_strike:
        if pos.direction == "long":
            print(f"  ✅ 价格突破！做多波动率获利")
        else:
            print(f"  ⚠️ 价格突破！做空波动率亏损")
    else:
        if pos.direction == "long":
            print(f"  ⏳ 等待价格突破...")
        else:
            print(f"  ✅ 价格在区间内，做空获利")

    print("-" * 60)
    print("【盈亏】")
    action_text = "成本" if pos.direction == "long" else "收入"
    print(f"  初始{action_text}: ${pos.initial_cost:.2f}")
    print(f"  当前价值: ${pos.current_value:.2f}")
    print(f"  盈亏: ${state.get_pnl():+.2f} ({state.get_pnl_pct():+.1%})")
    print("=" * 60)


async def build_strangle(ib: IB, stock: Stock, state: StrategyState):
    price = await get_stock_price(ib, stock)
    state.current_price = price

    expiries, strikes = await get_option_chain_info(ib, stock)
    if not expiries or not strikes:
        raise RuntimeError("无法获取期权链")

    expiry = expiries[1] if len(expiries) > 1 else expiries[0]

    put_strike = min(strikes, key=lambda x: abs(
        x - price * (1 - OTM_PCT)) if x < price else float('inf'))
    call_strike = min(strikes, key=lambda x: abs(
        x - price * (1 + OTM_PCT)) if x > price else float('inf'))

    action = "买入" if DIRECTION == "long" else "卖出"
    logger.info(f"构建 Strangle @ {expiry} ({action})")
    logger.info(f"  Put ${put_strike} | Call ${call_strike}")

    state.put_option = await find_option(ib, stock, "P", put_strike, expiry)
    state.call_option = await find_option(ib, stock, "C", call_strike, expiry)

    if not state.put_option or not state.call_option:
        raise RuntimeError("无法获取期权")

    put_price = await get_option_price(ib, state.put_option)
    call_price = await get_option_price(ib, state.call_option)

    total_premium = (put_price + call_price) * 100 * NUM_CONTRACTS

    if SIMULATION_MODE:
        logger.info(f"[模拟] {action} Strangle, 权利金: ${total_premium:.2f}")

    state.position = StranglePosition(
        put_strike=put_strike, call_strike=call_strike, expiry=expiry,
        contracts=NUM_CONTRACTS, direction=DIRECTION,
        initial_cost=total_premium, current_value=total_premium)


async def update_position_value(ib: IB, state: StrategyState):
    if not state.put_option or not state.call_option:
        return
    put_price = await get_option_price(ib, state.put_option)
    call_price = await get_option_price(ib, state.call_option)
    state.position.current_value = (
        put_price + call_price) * 100 * NUM_CONTRACTS


async def run_strangle(ib: IB):
    global shutdown_requested
    dir_text = "做多波动率" if DIRECTION == "long" else "做空波动率"
    logger.info(f"🎯 启动 Strangle 策略 ({dir_text})")

    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]

    state = StrategyState(start_time=datetime.now())
    await build_strangle(ib, stock, state)
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
        await run_strangle(ib)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    print("🎯 Strangle - 押注波动率，不确定方向时使用")
    asyncio.run(main())
