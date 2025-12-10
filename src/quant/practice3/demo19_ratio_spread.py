"""
Demo 19: Ratio Spread (比率价差策略)

================================================================================
📌 策略原理
================================================================================
Ratio Spread = 买入 1 张期权 + 卖出 2 张更虚值期权

Call Ratio Spread（看适度上涨）：
  买1张 $280 Call ← 卖2张 $294 Call
  └─ 成本 $5 ─┘    └─ 收入 $3×2=$6 ─┘
  净收入: $1（信用）

最大盈利点：$294（卖出的 Call 行权价）
风险：上方无限（裸卖 1 张 Call）

================================================================================
📌 关键概念
================================================================================
比率常见形式：
  - 1:2 (买1卖2) ← 最常见
  - 1:3 (买1卖3)
  - 2:3 (买2卖3)

净收入(Credit) vs 净支出(Debit)：
  - Credit：卖出收入 > 买入成本（下方风险有限）
  - Debit：卖出收入 < 买入成本（下方有亏损风险）

================================================================================
📌 参数说明
================================================================================
RS_LONG_STRIKE_OTM=0.00   # 买入 ATM (平值)
RS_SHORT_STRIKE_OTM=0.05  # 卖出 OTM 5%
RS_RATIO=2                # 卖出数量 / 买入数量

示例（股价 $280）：
  - 买 1 张 $280 Call @ $5.0
  - 卖 2 张 $294 Call @ $2.5 × 2 = $5.0
  - 净收入: $0（盈亏平衡）
  - 最大盈利: ($294 - $280) × 100 = $1400（股价=$294时）

================================================================================
📌 使用场景
================================================================================
✅ 适合：
   - 预期适度上涨到某一价位
   - 想要低成本或零成本入场
   - IV 较高时（卖出更值钱）

❌ 不适合：
   - 预期暴涨（裸卖期权风险）
   - 不愿承担无限风险

================================================================================
📌 风险分析
================================================================================
下方风险：净支出时为支出金额；净收入时为 $0
最大盈利：(卖出行权价 - 买入行权价) × 买入数量 × 100
上方风险：无限！（裸卖 1 张期权）

务必设置止损！

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
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "29"))

SYMBOL = os.getenv("RS_SYMBOL", "AAPL")
EXCHANGE = os.getenv("RS_EXCHANGE", "SMART")
CURRENCY = os.getenv("RS_CURRENCY", "USD")

NUM_LONG_CONTRACTS = int(os.getenv("RS_LONG_CONTRACTS", "1"))
RATIO = int(os.getenv("RS_RATIO", "2"))  # 卖出/买入 比率
LONG_STRIKE_OTM = float(os.getenv("RS_LONG_OTM", "0.00"))  # ATM
SHORT_STRIKE_OTM = float(os.getenv("RS_SHORT_OTM", "0.05"))  # OTM 5%
PROFIT_TARGET_PCT = float(os.getenv("RS_PROFIT_TARGET", "0.50"))
STOP_LOSS_PCT = float(os.getenv("RS_STOP_LOSS", "0.50"))
CHECK_INTERVAL_SEC = int(os.getenv("RS_CHECK_INTERVAL", "60"))
FALLBACK_PRICE = float(os.getenv("RS_FALLBACK_PRICE", "280"))

USE_DELAYED_DATA = os.getenv("RS_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("RS_SIMULATION", "true").lower() == "true"

shutdown_requested = False


@dataclass
class RatioSpreadPosition:
    """Ratio Spread 仓位"""
    long_strike: float = 0.0   # 买入行权价
    short_strike: float = 0.0  # 卖出行权价
    expiry: str = ""
    long_contracts: int = 0
    short_contracts: int = 0
    initial_credit: float = 0.0  # 正=净收入，负=净支出
    current_value: float = 0.0

    def get_max_profit_point(self) -> float:
        return self.short_strike

    def get_max_profit(self) -> float:
        # 最大盈利 = (卖出行权价 - 买入行权价) × 买入数 × 100 + 净收入
        spread_profit = (self.short_strike - self.long_strike) * \
            self.long_contracts * 100
        return spread_profit + self.initial_credit

    def get_upside_risk(self) -> str:
        naked_calls = self.short_contracts - self.long_contracts
        if naked_calls > 0:
            return f"⚠️ 无限（裸卖{naked_calls}张）"
        return "有限"


@dataclass
class StrategyState:
    position: RatioSpreadPosition = field(default_factory=RatioSpreadPosition)
    start_time: Optional[datetime] = None
    current_price: float = 0.0

    long_option: Optional[Option] = None
    short_option: Optional[Option] = None

    def get_pnl(self) -> float:
        # 当前价值 = long价值 - short价值 (short是卖出，平仓需买入)
        return self.position.current_value + self.position.initial_credit

    def get_pnl_pct(self) -> float:
        max_profit = self.position.get_max_profit()
        if max_profit == 0:
            return 0.0
        return self.get_pnl() / max_profit


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
    print(f"📊 Ratio Spread 状态 {'(' + reason + ')' if reason else ''}")
    print("=" * 60)
    print(f"股价: ${state.current_price:.2f}")
    print("-" * 60)
    print("【结构】")
    print(f"  买 {pos.long_contracts} 张 ${pos.long_strike:.0f} Call")
    print(f"  卖 {pos.short_contracts} 张 ${pos.short_strike:.0f} Call")
    print(f"  比率: 1:{RATIO}")

    # 可视化
    range_start = pos.long_strike - 10
    range_end = pos.short_strike + 20
    bar_len = 40

    def to_idx(p):
        return int((p - range_start) / (range_end - range_start) * bar_len)

    bar = ["─"] * bar_len
    long_idx = to_idx(pos.long_strike)
    short_idx = to_idx(pos.short_strike)
    price_idx = to_idx(state.current_price)

    if 0 <= long_idx < bar_len:
        bar[long_idx] = "L"
    if 0 <= short_idx < bar_len:
        bar[short_idx] = "★"
    if 0 <= price_idx < bar_len:
        bar[price_idx] = "●"

    print(f"  [{(''.join(bar))}]")
    print(f"  L=买入行权价  ★=最大盈利点  ●=当前价格")

    print("-" * 60)
    print("【风险分析】")
    credit_text = "净收入" if pos.initial_credit >= 0 else "净支出"
    print(f"  {credit_text}: ${abs(pos.initial_credit):.2f}")
    print(f"  最大盈利: ${pos.get_max_profit():.2f}（股价=${pos.short_strike:.0f}时）")
    print(f"  上方风险: {pos.get_upside_risk()}")
    if pos.initial_credit >= 0:
        print(f"  下方风险: $0（有净收入保护）")
    else:
        print(f"  下方风险: ${abs(pos.initial_credit):.2f}")

    print("-" * 60)
    print("【盈亏】")
    print(f"  当前盈亏: ${state.get_pnl():+.2f} ({state.get_pnl_pct():+.1%})")
    print("=" * 60)


async def build_ratio_spread(ib: IB, stock: Stock, state: StrategyState):
    price = await get_stock_price(ib, stock)
    state.current_price = price

    expiries, strikes = await get_option_chain_info(ib, stock)
    if not expiries or not strikes:
        raise RuntimeError("无法获取期权链")

    expiry = expiries[1] if len(expiries) > 1 else expiries[0]

    long_strike = min(strikes, key=lambda x: abs(
        x - price * (1 + LONG_STRIKE_OTM)))
    short_strike = min(strikes, key=lambda x: abs(
        x - price * (1 + SHORT_STRIKE_OTM)) if x > long_strike else float('inf'))

    short_contracts = NUM_LONG_CONTRACTS * RATIO

    logger.info(f"构建 Ratio Spread @ {expiry}")
    logger.info(f"  买 {NUM_LONG_CONTRACTS} 张 ${long_strike} Call")
    logger.info(f"  卖 {short_contracts} 张 ${short_strike} Call")

    state.long_option = await find_option(ib, stock, "C", long_strike, expiry)
    state.short_option = await find_option(ib, stock, "C", short_strike, expiry)

    if not state.long_option or not state.short_option:
        raise RuntimeError("无法获取期权")

    long_price = await get_option_price(ib, state.long_option)
    short_price = await get_option_price(ib, state.short_option)

    # 净收入 = 卖出收入 - 买入支出
    net_credit = (short_price * short_contracts -
                  long_price * NUM_LONG_CONTRACTS) * 100

    if SIMULATION_MODE:
        credit_text = "净收入" if net_credit >= 0 else "净支出"
        logger.info(
            f"[模拟] Ratio Spread, {credit_text}: ${abs(net_credit):.2f}")

    state.position = RatioSpreadPosition(
        long_strike=long_strike, short_strike=short_strike, expiry=expiry,
        long_contracts=NUM_LONG_CONTRACTS, short_contracts=short_contracts,
        initial_credit=net_credit, current_value=0)


async def update_position_value(ib: IB, state: StrategyState):
    if not state.long_option or not state.short_option:
        return
    long_price = await get_option_price(ib, state.long_option)
    short_price = await get_option_price(ib, state.short_option)

    # 当前价值 = long价值 - short平仓成本
    pos = state.position
    current_value = (long_price * pos.long_contracts -
                     short_price * pos.short_contracts) * 100
    state.position.current_value = current_value


async def run_ratio_spread(ib: IB):
    global shutdown_requested
    logger.info("📊 启动 Ratio Spread 策略")

    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]

    state = StrategyState(start_time=datetime.now())
    await build_ratio_spread(ib, stock, state)
    print_status(state, "建仓")

    try:
        while not shutdown_requested:
            await asyncio.sleep(CHECK_INTERVAL_SEC)
            state.current_price = await get_stock_price(ib, stock)
            await update_position_value(ib, state)

            pnl_pct = state.get_pnl_pct()
            logger.info(
                f"股价: ${state.current_price:.2f} | P&L: {pnl_pct:+.1%}")

            # 上方风险检测
            if state.current_price > state.position.short_strike * 1.05:
                logger.warning("⚠️ 股价超过卖出行权价 5%，风险增加！")

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
        await run_ratio_spread(ib)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    print("📊 Ratio Spread - 预期适度上涨，低成本入场")
    asyncio.run(main())
