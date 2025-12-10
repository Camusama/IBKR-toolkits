"""
Demo 11: Covered Call Strategy (持股卖权策略)

================================================================================
📌 策略原理
================================================================================
Covered Call = 持有股票 + 卖出虚值 Call 期权

1. 买入/持有 100 股标的股票（每卖 1 张期权需要 100 股做担保）
2. 卖出 1 张虚值 Call 期权（行权价高于当前价 5%），收取权利金
3. 到期日：
   - 股价 < 行权价：期权作废，保留全部权利金，可再次卖出新期权
   - 股价 > 行权价：被行权，以行权价卖出股票，赚到权利金 + 股票涨幅

收益来源：持续收取权利金（类似收租）

================================================================================
📌 使用场景
================================================================================
✅ 适合：
   - 震荡市或慢牛行情
   - 已持有股票想增强收益
   - 能接受在高位卖出股票

❌ 不适合：
   - 预期股价会大涨（收益被行权价封顶）
   - 股价快速下跌（承担股票亏损）

================================================================================
📌 运行方式（推荐：每天检查一次）
================================================================================
# 方式1: 每天开盘后手动运行一次检查
uv run demo11_covered_call.py

# 方式2: 使用 cron 定时任务（美东时间 9:35 开盘后5分钟运行）
# 35 9 * * 1-5 cd /path/to/project && uv run demo11_covered_call.py

# 首次运行会：买入股票 + 卖出 Call
# 后续运行会：检查是否需要展期（期权到期前3天自动展期）

================================================================================
📌 参数配置
================================================================================
CC_SYMBOL=AAPL          # 标的股票
CC_SHARES=100           # 持股数量（必须是100的倍数）
CC_OTM_PCT=0.05         # 虚值程度（0.05 = 卖出比当前价高5%的Call）
CC_MIN_PREMIUM=1.0      # 最低权利金（低于此值不卖）
CC_SIMULATION=true      # 模拟模式（设为false启用真实交易）

示例：股价 $280，卖出 $294 的 Call（280 × 1.05 = $294）

================================================================================
📌 预期收益
================================================================================
假设：股价 $280，卖出 2 周后到期的虚值 Call，权利金 $2.5/股
- 每张期权收入：$2.5 × 100 = $250
- 年化收益率：($250 / $28000) × 26周 ≈ 23%（未计股价变动）

================================================================================
📌 风险提示
================================================================================
⚠️ 股价大涨：收益被行权价封顶，错过上涨空间
⚠️ 股价大跌：期权权利金无法弥补股票亏损
⚠️ 被提前行权：美式期权可能在到期前被行权（分红前常见）

================================================================================
"""

import asyncio
import os
import math
import logging
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field

from ib_async import IB, Stock, Option, MarketOrder

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ========== 配置 ==========
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "21"))

SYMBOL = os.getenv("CC_SYMBOL", "AAPL")
EXCHANGE = os.getenv("CC_EXCHANGE", "SMART")
CURRENCY = os.getenv("CC_CURRENCY", "USD")

# 策略配置
STOCK_SHARES = int(os.getenv("CC_SHARES", "100"))  # 持股数量（需为100的倍数）
OTM_PERCENTAGE = float(os.getenv("CC_OTM_PCT", "0.05"))  # 虚值程度 5%
MIN_PREMIUM = float(os.getenv("CC_MIN_PREMIUM", "1.0"))  # 最低权利金要求
CHECK_INTERVAL_SEC = int(os.getenv("CC_CHECK_INTERVAL", "60"))
FALLBACK_PRICE = float(os.getenv("CC_FALLBACK_PRICE", "280"))

USE_DELAYED_DATA = os.getenv("CC_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("CC_SIMULATION", "true").lower() == "true"

shutdown_requested = False


@dataclass
class Position:
    stock_shares: int = 0
    stock_avg_price: float = 0.0
    call_contracts: int = 0  # 卖出的 Call 数量（负数表示空头）
    call_strike: float = 0.0
    call_expiry: str = ""
    call_premium_received: float = 0.0  # 收到的权利金
    total_premium_collected: float = 0.0  # 累计权利金
    rolls: int = 0  # 展期次数


@dataclass
class StrategyState:
    position: Position = field(default_factory=Position)
    start_time: Optional[datetime] = None
    current_stock_price: float = 0.0
    current_option_price: float = 0.0
    option_contract: Optional[Option] = None

    def get_stock_pnl(self) -> float:
        if self.position.stock_shares == 0:
            return 0.0
        return (self.current_stock_price - self.position.stock_avg_price) * self.position.stock_shares

    def get_total_pnl(self) -> float:
        return self.get_stock_pnl() + self.position.total_premium_collected


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


async def get_option_price(ib: IB, option: Option) -> float:
    ticker = ib.reqMktData(option, "", False, False)
    await asyncio.sleep(2)
    price = ticker.last or ticker.close or (
        (ticker.bid or 0) + (ticker.ask or 0)) / 2
    if price is None or (isinstance(price, float) and math.isnan(price)):
        price = 0.0
    ib.cancelMktData(option)
    return price


async def find_otm_call(ib: IB, stock: Stock, stock_price: float) -> Optional[Option]:
    """寻找合适的虚值 Call 期权"""
    try:
        chains = await ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)
        if not chains:
            return None

        chain = next((c for c in chains if c.exchange == "SMART"), chains[0])

        # 选择2-4周后到期
        today = datetime.now().strftime("%Y%m%d")
        valid_expiries = sorted([e for e in chain.expirations if e > today])
        if len(valid_expiries) < 2:
            return None
        target_expiry = valid_expiries[1]  # 第二个到期日

        # 选择虚值行权价
        target_strike = stock_price * (1 + OTM_PERCENTAGE)
        strikes = sorted(chain.strikes)
        otm_strike = min(strikes, key=lambda x: abs(
            x - target_strike) if x > stock_price else float('inf'))

        option = Option(stock.symbol, target_expiry, otm_strike, "C", "SMART")
        qualified = await ib.qualifyContractsAsync(option)
        if qualified and qualified[0]:
            return qualified[0]
    except Exception as e:
        logger.error(f"获取期权失败: {e}")
    return None


def print_status(state: StrategyState, reason: str = ""):
    pos = state.position
    print("\n" + "=" * 60)
    print(f"📊 Covered Call 状态 {'(' + reason + ')' if reason else ''}")
    print("=" * 60)
    elapsed = (datetime.now() -
               state.start_time).total_seconds() if state.start_time else 0
    print(f"⏰ 运行: {int(elapsed/60)} 分钟")
    print(f"📈 股价: ${state.current_stock_price:.2f}")

    print("-" * 60)
    print("【股票持仓】")
    print(f"  持股: {pos.stock_shares} 股 @ ${pos.stock_avg_price:.2f}")
    print(f"  市值: ${pos.stock_shares * state.current_stock_price:.2f}")
    print(f"  股票盈亏: ${state.get_stock_pnl():+.2f}")

    print("-" * 60)
    print("【期权仓位】")
    if pos.call_contracts != 0:
        print(
            f"  卖出: {abs(pos.call_contracts)} 张 Call @ ${pos.call_strike:.2f}")
        print(f"  到期: {pos.call_expiry}")
        print(f"  权利金: ${pos.call_premium_received:.2f}")
        print(f"  当前价格: ${state.current_option_price:.2f}")

        # 计算被行权风险
        if state.current_stock_price > pos.call_strike:
            itm_pct = (state.current_stock_price -
                       pos.call_strike) / pos.call_strike * 100
            print(f"  ⚠️ 实值 {itm_pct:.1f}%，可能被行权")
        else:
            otm_pct = (pos.call_strike - state.current_stock_price) / \
                state.current_stock_price * 100
            print(f"  ✅ 虚值 {otm_pct:.1f}%")
    else:
        print("  无 Call 仓位")

    print("-" * 60)
    print("【收益统计】")
    print(f"  累计权利金: ${pos.total_premium_collected:.2f}")
    print(f"  展期次数: {pos.rolls}")
    print(f"  总收益: ${state.get_total_pnl():+.2f}")
    print("=" * 60)


async def buy_stock(ib: IB, stock: Stock, state: StrategyState):
    """买入股票"""
    price = await get_stock_price(ib, stock)
    if SIMULATION_MODE:
        logger.info(f"[模拟] 买入 {STOCK_SHARES} 股 @ ${price:.2f}")
        state.position.stock_shares = STOCK_SHARES
        state.position.stock_avg_price = price
    else:
        order = MarketOrder("BUY", STOCK_SHARES)
        trade = ib.placeOrder(stock, order)
        await asyncio.sleep(3)
        if trade.orderStatus.status == "Filled":
            state.position.stock_shares = STOCK_SHARES
            state.position.stock_avg_price = trade.orderStatus.avgFillPrice


async def sell_call(ib: IB, option: Option, state: StrategyState):
    """卖出 Call 期权"""
    num_contracts = state.position.stock_shares // 100
    if num_contracts <= 0:
        return

    price = await get_option_price(ib, option)
    if price < MIN_PREMIUM:
        logger.warning(f"权利金 ${price:.2f} 低于最低要求 ${MIN_PREMIUM:.2f}")
        return

    premium = price * num_contracts * 100

    if SIMULATION_MODE:
        logger.info(
            f"[模拟] 卖出 {num_contracts} 张 Call @ ${price:.2f} = ${premium:.2f}")
        state.position.call_contracts = -num_contracts
        state.position.call_strike = option.strike
        state.position.call_expiry = option.lastTradeDateOrContractMonth
        state.position.call_premium_received = premium
        state.position.total_premium_collected += premium
        state.option_contract = option
    else:
        order = MarketOrder("SELL", num_contracts)
        trade = ib.placeOrder(option, order)
        await asyncio.sleep(3)
        if trade.orderStatus.status == "Filled":
            actual_price = trade.orderStatus.avgFillPrice
            premium = actual_price * num_contracts * 100
            state.position.call_contracts = -num_contracts
            state.position.call_strike = option.strike
            state.position.call_expiry = option.lastTradeDateOrContractMonth
            state.position.call_premium_received = premium
            state.position.total_premium_collected += premium
            state.option_contract = option


def check_expiry(state: StrategyState) -> bool:
    """检查期权是否即将到期（3天内）"""
    if not state.position.call_expiry:
        return False
    expiry = datetime.strptime(state.position.call_expiry, "%Y%m%d")
    days_to_expiry = (expiry - datetime.now()).days
    return days_to_expiry <= 3


async def close_call_position(ib: IB, state: StrategyState):
    """平仓 Call 期权"""
    if state.position.call_contracts == 0 or not state.option_contract:
        return

    num_contracts = abs(state.position.call_contracts)
    price = await get_option_price(ib, state.option_contract)
    cost = price * num_contracts * 100

    if SIMULATION_MODE:
        logger.info(
            f"[模拟] 买入平仓 {num_contracts} 张 Call @ ${price:.2f} = ${cost:.2f}")
        # 平仓成本从累计收益中扣除
        state.position.total_premium_collected -= cost
        state.position.call_contracts = 0
        state.position.call_strike = 0
        state.position.call_expiry = ""
        state.position.call_premium_received = 0
        state.option_contract = None


async def run_covered_call(ib: IB):
    global shutdown_requested

    logger.info("🚀 启动 Covered Call 策略")
    logger.info(
        f"标的: {SYMBOL} | 持股: {STOCK_SHARES} | 虚值: {OTM_PERCENTAGE:.1%}")
    logger.info("💡 按 Ctrl+C 退出")

    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]

    state = StrategyState()
    state.start_time = datetime.now()

    # 买入股票
    logger.info("📥 买入股票...")
    await buy_stock(ib, stock, state)
    state.current_stock_price = await get_stock_price(ib, stock)

    # 卖出 Call
    logger.info("📤 寻找合适的 Call 期权...")
    option = await find_otm_call(ib, stock, state.current_stock_price)
    if option:
        await sell_call(ib, option, state)
        state.current_option_price = await get_option_price(ib, option)
    else:
        logger.warning("未找到合适期权")

    print_status(state, "启动")

    check_count = 0
    exit_reason = "手动退出"

    try:
        while not shutdown_requested:
            await asyncio.sleep(CHECK_INTERVAL_SEC)
            check_count += 1

            state.current_stock_price = await get_stock_price(ib, stock)
            if state.option_contract:
                state.current_option_price = await get_option_price(ib, state.option_contract)

            logger.info(
                f"--- 检查 #{check_count} | 股价: ${state.current_stock_price:.2f} ---")

            # 检查是否需要展期
            if check_expiry(state):
                logger.info("⏰ 期权即将到期，执行展期...")
                await close_call_position(ib, state)

                option = await find_otm_call(ib, stock, state.current_stock_price)
                if option:
                    await sell_call(ib, option, state)
                    state.position.rolls += 1
                    print_status(state, f"展期 #{state.position.rolls}")

    except KeyboardInterrupt:
        exit_reason = "用户中断"

    logger.info(f"📤 退出: {exit_reason}")
    await close_call_position(ib, state)
    print_status(state, "结束")

    print(
        f"\n📋 总结: 累计权利金 ${state.position.total_premium_collected:.2f}, 展期 {state.position.rolls} 次")


def handle_shutdown(signum, frame):
    global shutdown_requested
    shutdown_requested = True


async def main():
    import signal
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    ib = await connect_ib()
    try:
        await run_covered_call(ib)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    print("""
🎯 Covered Call 策略 - 持股卖权收权利金
   适合震荡或慢牛行情，通过卖出虚值 Call 增强收益
   按 Ctrl+C 退出
""")
    asyncio.run(main())
