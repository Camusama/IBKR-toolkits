"""
Demo 13: Volatility Mean Reversion (波动率均值回归策略)

================================================================================
📌 策略原理
================================================================================
波动率均值回归 = 监控 IV（隐含波动率），在极端值时反向交易

核心假设：
- IV 过高 → 市场恐慌过度 → IV 会下降 → 卖期权（做空波动率）
- IV 过低 → 市场过于乐观 → IV 会上升 → 买期权（做多波动率）

交易方式：
- 做空波动率：卖出 Straddle（同时卖 Call + Put）
- 做多波动率：买入 Straddle（同时买 Call + Put）

================================================================================
📌 触发条件 & 参数说明
================================================================================
VOL_IV_HIGH=0.40     # IV > 40% 时做空波动率（卖 Straddle）
VOL_IV_LOW=0.20      # IV < 20% 时做多波动率（买 Straddle）
VOL_STOP_LOSS=0.30   # 亏损达到 30% 时止损

平仓条件：
- 做空后 IV 下降 20%（IV < 建仓IV × 0.8）→ 止盈平仓
- 做多后 IV 上升 20%（IV > 建仓IV × 1.2）→ 止盈平仓

示例：
  当前 IV = 45%（高于 40% 阈值）
  → 卖出 Straddle 做空波动率
  → IV 下降到 36%（45% × 0.8）时止盈

================================================================================
📌 如何判断 IV 高低？
================================================================================
方法1: 与历史波动率 (HV) 比较
  - IV / HV > 1.2 → IV 偏高
  - IV / HV < 0.8 → IV 偏低

方法2: IV 百分位（需要更多历史数据）
  - IV > 80% 百分位 → 偏高
  - IV < 20% 百分位 → 偏低

本策略使用固定阈值（40%/20%），可根据标的历史 IV 调整

================================================================================
📌 使用场景
================================================================================
✅ 适合：
   - 财报后 IV 回落（IV Crush 效应）
   - VIX 恐慌指数飙升后回落
   - 重大事件前 IV 升高，事件后回落

❌ 不适合：
   - 临近财报（IV 可能继续升高）
   - 黑天鹅事件（做空波动率风险无限）
   - 长期趋势性波动率变化

================================================================================
📌 运行方式（推荐：每天检查 1 次）
================================================================================
# 方式1: 每天开盘后检查 IV 水平
uv run demo13_volatility_mean_reversion.py

# 方式2: cron 定时任务（美东时间 9:35 检查）
# 35 9 * * 1-5 cd /path/to/project && uv run demo13_volatility_mean_reversion.py

# 首次运行：检查 IV，若触发条件则建仓
# 后续运行：监控 IV 变化，达到止盈/止损时平仓

================================================================================
📌 常见标的 IV 参考值
================================================================================
标的      | 低IV  | 正常IV | 高IV
----------|-------|--------|-------
AAPL      | <20%  | 20-30% | >35%
TSLA      | <40%  | 40-60% | >80%
SPY       | <12%  | 12-18% | >25%
QQQ       | <15%  | 15-22% | >30%

建议根据标的调整 VOL_IV_HIGH 和 VOL_IV_LOW

================================================================================
📌 风险提示
================================================================================
⚠️ 做空波动率风险巨大（理论亏损无限）
⚠️ 黑天鹅事件可能导致 IV 飙升不回归
⚠️ 财报前做空容易亏损
⚠️ 必须设置止损！

================================================================================
"""
import asyncio
import os
import math
import logging
from datetime import datetime
from typing import Optional, List
from dataclasses import dataclass, field
from collections import deque

from ib_async import IB, Stock, Option, MarketOrder

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ========== 配置 ==========
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "23"))

SYMBOL = os.getenv("VOL_SYMBOL", "AAPL")
EXCHANGE = os.getenv("VOL_EXCHANGE", "SMART")
CURRENCY = os.getenv("VOL_CURRENCY", "USD")

# 波动率配置
IV_HIGH_THRESHOLD = float(os.getenv("VOL_IV_HIGH", "0.40"))  # IV > 40% 卖期权
IV_LOW_THRESHOLD = float(os.getenv("VOL_IV_LOW", "0.20"))   # IV < 20% 买期权
IV_LOOKBACK_DAYS = int(os.getenv("VOL_LOOKBACK", "20"))     # 历史波动率回看天数

# 交易配置
NUM_CONTRACTS = int(os.getenv("VOL_CONTRACTS", "1"))
STOP_LOSS_PCT = float(os.getenv("VOL_STOP_LOSS", "0.30"))
CHECK_INTERVAL_SEC = int(os.getenv("VOL_CHECK_INTERVAL", "60"))
FALLBACK_PRICE = float(os.getenv("VOL_FALLBACK_PRICE", "280"))

USE_DELAYED_DATA = os.getenv("VOL_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("VOL_SIMULATION", "true").lower() == "true"

shutdown_requested = False


@dataclass
class Position:
    """期权持仓"""
    contracts: int = 0  # 正=多头，负=空头
    option_type: str = ""  # "straddle", "strangle", "call", "put"
    entry_iv: float = 0.0  # 建仓时的 IV
    entry_price: float = 0.0  # 建仓价格
    current_value: float = 0.0
    realized_pnl: float = 0.0


@dataclass
class StrategyState:
    position: Position = field(default_factory=Position)
    start_time: Optional[datetime] = None
    current_price: float = 0.0
    current_iv: float = 0.0
    hv_20d: float = 0.0  # 20日历史波动率
    price_history: deque = field(default_factory=lambda: deque(maxlen=100))
    iv_history: deque = field(default_factory=lambda: deque(maxlen=100))

    # 期权合约
    call_option: Optional[Option] = None
    put_option: Optional[Option] = None

    def get_pnl(self) -> float:
        if self.position.contracts == 0:
            return 0.0
        return self.position.current_value - self.position.entry_price * abs(self.position.contracts) * 100


def calculate_historical_volatility(prices: List[float], days: int = 20) -> float:
    """计算历史波动率 (年化)"""
    if len(prices) < days + 1:
        return 0.25  # 默认 25%

    # 计算日收益率
    returns = []
    for i in range(1, min(days + 1, len(prices))):
        ret = math.log(prices[-i] / prices[-i-1])
        returns.append(ret)

    if len(returns) < 2:
        return 0.25

    # 标准差
    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
    daily_vol = math.sqrt(variance)

    # 年化 (252 交易日)
    annual_vol = daily_vol * math.sqrt(252)
    return annual_vol


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


async def get_option_with_greeks(ib: IB, option: Option) -> tuple:
    """获取期权价格和希腊值"""
    ticker = ib.reqMktData(option, "106", False, False)  # 106 = Greeks
    await asyncio.sleep(3)

    price = ticker.last or ticker.close or (
        (ticker.bid or 0) + (ticker.ask or 0)) / 2

    iv = 0.25  # 默认
    if ticker.modelGreeks and ticker.modelGreeks.impliedVol:
        iv = ticker.modelGreeks.impliedVol
    elif ticker.lastGreeks and ticker.lastGreeks.impliedVol:
        iv = ticker.lastGreeks.impliedVol

    ib.cancelMktData(option)
    return price if price and not math.isnan(price) else 0.0, iv


async def find_atm_options(ib: IB, stock: Stock, price: float) -> tuple:
    """获取 ATM Call 和 Put"""
    chains = await ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)
    if not chains:
        return None, None

    chain = next((c for c in chains if c.exchange == "SMART"), chains[0])

    today = datetime.now().strftime("%Y%m%d")
    valid_expiries = sorted([e for e in chain.expirations if e > today])
    if len(valid_expiries) < 2:
        return None, None
    expiry = valid_expiries[1]

    strikes = sorted(chain.strikes)
    atm_strike = min(strikes, key=lambda x: abs(x - price))

    call = Option(stock.symbol, expiry, atm_strike, "C", "SMART")
    put = Option(stock.symbol, expiry, atm_strike, "P", "SMART")

    try:
        call_q = await ib.qualifyContractsAsync(call)
        put_q = await ib.qualifyContractsAsync(put)
        return call_q[0] if call_q else None, put_q[0] if put_q else None
    except:
        return None, None


async def get_historical_prices(ib: IB, stock: Stock, days: int = 30) -> List[float]:
    """获取历史价格"""
    try:
        bars = await ib.reqHistoricalDataAsync(
            stock, endDateTime="", durationStr=f"{days} D",
            barSizeSetting="1 day", whatToShow="TRADES",
            useRTH=True, formatDate=1
        )
        return [bar.close for bar in bars] if bars else []
    except:
        return []


def print_status(state: StrategyState, reason: str = ""):
    pos = state.position
    print("\n" + "=" * 60)
    print(f"📊 波动率均值回归状态 {'(' + reason + ')' if reason else ''}")
    print("=" * 60)

    elapsed = (datetime.now() -
               state.start_time).total_seconds() if state.start_time else 0
    print(f"⏰ 运行: {int(elapsed/60)} 分钟 | 💰 股价: ${state.current_price:.2f}")

    print("-" * 60)
    print("【波动率】")
    print(f"  隐含波动率 (IV): {state.current_iv:.1%}")
    print(f"  历史波动率 (HV): {state.hv_20d:.1%}")
    print(f"  IV/HV 比率: {state.current_iv/state.hv_20d:.2f}x" if state.hv_20d >
          0 else "  IV/HV 比率: N/A")

    # IV 状态可视化
    iv_bar_len = 40
    iv_pos = min(state.current_iv / 0.6, 1.0)  # 60% 为最大显示
    iv_idx = int(iv_pos * iv_bar_len)
    low_idx = int(IV_LOW_THRESHOLD / 0.6 * iv_bar_len)
    high_idx = int(IV_HIGH_THRESHOLD / 0.6 * iv_bar_len)

    bar = ["─"] * iv_bar_len
    bar[low_idx] = "L"
    bar[high_idx] = "H"
    if 0 <= iv_idx < iv_bar_len:
        bar[iv_idx] = "●"
    print(f"  [{''.join(bar)}]")
    print(f"   L={IV_LOW_THRESHOLD:.0%} (买)  H={IV_HIGH_THRESHOLD:.0%} (卖)")

    # 信号判断
    if state.current_iv > IV_HIGH_THRESHOLD:
        print(f"  🔴 IV偏高 → 适合做空波动率 (卖期权)")
    elif state.current_iv < IV_LOW_THRESHOLD:
        print(f"  🟢 IV偏低 → 适合做多波动率 (买期权)")
    else:
        print(f"  ⚪ IV正常 → 观望")

    print("-" * 60)
    print("【持仓】")
    if pos.contracts != 0:
        direction = "多头" if pos.contracts > 0 else "空头"
        print(f"  {direction} {abs(pos.contracts)} 张 {pos.option_type}")
        print(f"  建仓 IV: {pos.entry_iv:.1%}")
        print(f"  当前价值: ${pos.current_value:.2f}")
        print(f"  盈亏: ${state.get_pnl():+.2f}")
    else:
        print(f"  无持仓")

    print("=" * 60)


async def open_straddle(ib: IB, stock: Stock, state: StrategyState, direction: str):
    """开仓跨式期权 (同时买/卖 Call 和 Put)"""
    call, put = await find_atm_options(ib, stock, state.current_price)
    if not call or not put:
        logger.warning("无法获取 ATM 期权")
        return

    state.call_option = call
    state.put_option = put

    call_price, call_iv = await get_option_with_greeks(ib, call)
    put_price, put_iv = await get_option_with_greeks(ib, put)

    avg_iv = (call_iv + put_iv) / 2
    total_premium = (call_price + put_price) * 100 * NUM_CONTRACTS

    action = "SELL" if direction == "short" else "BUY"
    contracts = -NUM_CONTRACTS if direction == "short" else NUM_CONTRACTS

    if SIMULATION_MODE:
        logger.info(
            f"[模拟] {action} Straddle: Call ${call_price:.2f} + Put ${put_price:.2f} = ${total_premium:.2f}")
        state.position.contracts = contracts
        state.position.option_type = f"straddle ({action})"
        state.position.entry_iv = avg_iv
        state.position.entry_price = call_price + put_price
        state.position.current_value = total_premium


async def close_position(ib: IB, state: StrategyState):
    """平仓"""
    if state.position.contracts == 0:
        return

    if state.call_option and state.put_option:
        call_price, _ = await get_option_with_greeks(ib, state.call_option)
        put_price, _ = await get_option_with_greeks(ib, state.put_option)
        current_value = (call_price + put_price) * 100 * \
            abs(state.position.contracts)

        pnl = state.get_pnl()
        if state.position.contracts < 0:  # 空头平仓
            pnl = state.position.entry_price * \
                abs(state.position.contracts) * 100 - current_value

        if SIMULATION_MODE:
            logger.info(f"[模拟] 平仓, 盈亏: ${pnl:+.2f}")

        state.position.realized_pnl += pnl

    state.position.contracts = 0
    state.call_option = None
    state.put_option = None


async def run_volatility_strategy(ib: IB):
    global shutdown_requested

    logger.info("🚀 启动波动率均值回归策略")
    logger.info(f"标的: {SYMBOL}")
    logger.info(
        f"IV 卖出阈值: {IV_HIGH_THRESHOLD:.0%} | IV 买入阈值: {IV_LOW_THRESHOLD:.0%}")
    logger.info("💡 按 Ctrl+C 退出")

    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]

    state = StrategyState()
    state.start_time = datetime.now()

    # 加载历史数据
    logger.info("📥 加载历史数据...")
    hist_prices = await get_historical_prices(ib, stock, days=30)
    if hist_prices:
        state.price_history.extend(hist_prices)
        state.hv_20d = calculate_historical_volatility(
            hist_prices, IV_LOOKBACK_DAYS)
        logger.info(f"✅ 历史波动率 (20D): {state.hv_20d:.1%}")

    # 获取当前 IV
    state.current_price = await get_stock_price(ib, stock)
    call, put = await find_atm_options(ib, stock, state.current_price)
    if call and put:
        _, call_iv = await get_option_with_greeks(ib, call)
        _, put_iv = await get_option_with_greeks(ib, put)
        state.current_iv = (call_iv + put_iv) / 2
        logger.info(f"当前 IV: {state.current_iv:.1%}")

    print_status(state, "启动")

    check_count = 0
    exit_reason = "手动退出"

    try:
        while not shutdown_requested:
            await asyncio.sleep(CHECK_INTERVAL_SEC)
            check_count += 1

            # 更新价格和 IV
            state.current_price = await get_stock_price(ib, stock)
            state.price_history.append(state.current_price)
            state.hv_20d = calculate_historical_volatility(
                list(state.price_history), IV_LOOKBACK_DAYS)

            call, put = await find_atm_options(ib, stock, state.current_price)
            if call and put:
                call_price, call_iv = await get_option_with_greeks(ib, call)
                put_price, put_iv = await get_option_with_greeks(ib, put)
                state.current_iv = (call_iv + put_iv) / 2

                if state.position.contracts != 0:
                    state.position.current_value = (
                        call_price + put_price) * 100 * abs(state.position.contracts)

            logger.info(
                f"--- 检查 #{check_count} | IV: {state.current_iv:.1%} | HV: {state.hv_20d:.1%} ---")

            # 检查止损
            if state.position.contracts != 0:
                pnl_pct = state.get_pnl() / (state.position.entry_price *
                                             abs(state.position.contracts) * 100)
                if pnl_pct < -STOP_LOSS_PCT:
                    logger.warning(f"🛑 触发止损 {pnl_pct:.1%}")
                    await close_position(ib, state)
                    print_status(state, "止损")
                    continue

            # 无持仓时检查开仓信号
            if state.position.contracts == 0:
                if state.current_iv > IV_HIGH_THRESHOLD:
                    logger.info(
                        f"🔴 IV偏高 {state.current_iv:.1%} > {IV_HIGH_THRESHOLD:.0%}, 做空波动率")
                    await open_straddle(ib, stock, state, "short")
                    print_status(state, "开仓做空 IV")
                elif state.current_iv < IV_LOW_THRESHOLD:
                    logger.info(
                        f"🟢 IV偏低 {state.current_iv:.1%} < {IV_LOW_THRESHOLD:.0%}, 做多波动率")
                    await open_straddle(ib, stock, state, "long")
                    print_status(state, "开仓做多 IV")

            # 有持仓时检查平仓信号 (IV 回归)
            elif state.position.contracts < 0 and state.current_iv < state.position.entry_iv * 0.8:
                logger.info(f"✅ IV 回落，平仓获利")
                await close_position(ib, state)
                print_status(state, "平仓")
            elif state.position.contracts > 0 and state.current_iv > state.position.entry_iv * 1.2:
                logger.info(f"✅ IV 上升，平仓获利")
                await close_position(ib, state)
                print_status(state, "平仓")

    except KeyboardInterrupt:
        exit_reason = "用户中断"

    logger.info(f"📤 退出: {exit_reason}")
    await close_position(ib, state)
    print_status(state, "结束")
    print(f"\n📋 总结: 累计盈亏 ${state.position.realized_pnl:+.2f}")


def handle_shutdown(signum, frame):
    global shutdown_requested
    shutdown_requested = True


async def main():
    import signal
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    ib = await connect_ib()
    try:
        await run_volatility_strategy(ib)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    print("""
🎯 波动率均值回归策略
   IV 偏高时做空波动率，IV 偏低时做多波动率
   按 Ctrl+C 退出
""")
    asyncio.run(main())
