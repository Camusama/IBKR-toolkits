"""
Demo 9: RSI Mean Reversion Strategy (RSI 均值回归策略)

策略原理：
1. RSI < 30 时视为超卖，买入信号
2. RSI > 70 时视为超买，卖出信号
3. 根据 RSI 值动态调整仓位
4. 设置止盈止损保护

适合场景：
- 震荡市场
- 均值回归特性明显的标的

配置说明：
- RSI_PERIOD: RSI 计算周期
- RSI_OVERSOLD: 超卖阈值（默认30）
- RSI_OVERBOUGHT: 超买阈值（默认70）
- MAX_POSITION: 最大持仓股数
"""
import asyncio
import os
import math
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from dataclasses import dataclass, field
from collections import deque

from ib_async import IB, Stock, MarketOrder

# ========== 日志配置 ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ========== 连接配置 ==========
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "19"))

# ========== 标的配置 ==========
SYMBOL = os.getenv("RSI_SYMBOL", "AAPL")
EXCHANGE = os.getenv("RSI_EXCHANGE", "SMART")
CURRENCY = os.getenv("RSI_CURRENCY", "USD")

# ========== RSI 配置 ==========
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))  # RSI 周期
RSI_OVERSOLD = float(os.getenv("RSI_OVERSOLD", "30"))  # 超卖阈值
RSI_OVERBOUGHT = float(os.getenv("RSI_OVERBOUGHT", "70"))  # 超买阈值

# ========== 交易配置 ==========
MAX_POSITION = int(os.getenv("RSI_MAX_POSITION", "100"))  # 最大持仓
TRADE_SIZE = int(os.getenv("RSI_TRADE_SIZE", "10"))  # 每次交易数量
CHECK_INTERVAL_SEC = int(os.getenv("RSI_CHECK_INTERVAL", "60"))  # 检查间隔（秒）
FALLBACK_PRICE = float(os.getenv("RSI_FALLBACK_PRICE", "280"))  # 备用股价

# ========== 风控配置 ==========
STOP_LOSS_PCT = float(os.getenv("RSI_STOP_LOSS", "0.05"))  # 止损比例 5%
TAKE_PROFIT_PCT = float(os.getenv("RSI_TAKE_PROFIT", "0.10"))  # 止盈比例 10%

# ========== 模式配置 ==========
USE_DELAYED_DATA = os.getenv("RSI_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("RSI_SIMULATION", "true").lower() == "true"

# ========== 全局退出标志 ==========
shutdown_requested = False


@dataclass
class Position:
    """持仓状态"""
    shares: int = 0  # 持仓股数（正=多头，负=空头）
    avg_price: float = 0.0  # 平均成本
    realized_pnl: float = 0.0  # 已实现盈亏
    total_trades: int = 0  # 总交易次数
    winning_trades: int = 0  # 盈利交易次数

    @property
    def win_rate(self) -> float:
        """胜率"""
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades


@dataclass
class StrategyState:
    """策略状态"""
    position: Position = field(default_factory=Position)
    start_time: Optional[datetime] = None
    current_price: float = 0.0
    current_rsi: float = 50.0
    price_history: deque = field(default_factory=lambda: deque(maxlen=100))
    signal_history: List[str] = field(default_factory=list)

    # 统计
    total_signals: int = 0
    buy_signals: int = 0
    sell_signals: int = 0

    def get_unrealized_pnl(self) -> float:
        """计算未实现盈亏"""
        if self.position.shares == 0 or self.position.avg_price == 0:
            return 0.0
        return (self.current_price - self.position.avg_price) * self.position.shares

    def get_total_pnl(self) -> float:
        """计算总盈亏"""
        return self.position.realized_pnl + self.get_unrealized_pnl()


def calculate_rsi(prices: List[float], period: int = RSI_PERIOD) -> float:
    """计算 RSI 指标"""
    if len(prices) < period + 1:
        return 50.0  # 数据不足时返回中性值

    # 计算价格变动
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]

    # 取最近 period 个变动
    recent_deltas = deltas[-period:]

    # 分离上涨和下跌
    gains = [d if d > 0 else 0 for d in recent_deltas]
    losses = [-d if d < 0 else 0 for d in recent_deltas]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


async def connect_ib() -> IB:
    """连接 IBKR"""
    ib = IB()
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    if USE_DELAYED_DATA:
        ib.reqMarketDataType(3)
    else:
        ib.reqMarketDataType(1)
    return ib


async def get_stock_price(ib: IB, stock: Stock, wait_sec: float = 2.0) -> float:
    """获取股票价格"""
    ticker = ib.reqMktData(stock, "", False, False)
    await asyncio.sleep(wait_sec)

    price = ticker.last
    if price is None or math.isnan(price):
        price = ticker.close
    if price is None or math.isnan(price):
        price = (ticker.bid + ticker.ask) / \
            2 if ticker.bid and ticker.ask else 0.0
    if price is None or math.isnan(price) or price <= 0:
        logger.warning(f"无法获取实时股价，使用备用价格: ${FALLBACK_PRICE:.2f}")
        price = FALLBACK_PRICE

    ib.cancelMktData(stock)
    return price


async def get_historical_prices(ib: IB, stock: Stock, days: int = 30) -> List[float]:
    """获取历史收盘价"""
    try:
        bars = await ib.reqHistoricalDataAsync(
            stock,
            endDateTime="",
            durationStr=f"{days} D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1
        )
        if bars:
            return [bar.close for bar in bars]
    except Exception as e:
        logger.error(f"获取历史数据失败: {e}")
    return []


def get_signal(rsi: float) -> str:
    """根据 RSI 生成交易信号"""
    if rsi < RSI_OVERSOLD:
        return "BUY"
    elif rsi > RSI_OVERBOUGHT:
        return "SELL"
    else:
        return "HOLD"


async def execute_trade(ib: IB, stock: Stock, action: str, qty: int,
                        price: float, state: StrategyState) -> bool:
    """执行交易"""
    if qty == 0:
        return False

    if SIMULATION_MODE:
        logger.info(f"[模拟] {action} {qty} 股 {stock.symbol} @ ${price:.2f}")

        old_shares = state.position.shares

        if action == "BUY":
            # 计算新的平均成本
            total_cost = state.position.avg_price * old_shares + price * qty
            new_shares = old_shares + qty
            if new_shares > 0:
                state.position.avg_price = total_cost / new_shares
            state.position.shares = new_shares

        elif action == "SELL":
            # 计算已实现盈亏
            if old_shares > 0:
                realized = (price - state.position.avg_price) * \
                    min(qty, old_shares)
                state.position.realized_pnl += realized
                state.position.total_trades += 1
                if realized > 0:
                    state.position.winning_trades += 1
                logger.info(f"  平仓盈亏: ${realized:+.2f}")

            state.position.shares = old_shares - qty
            if state.position.shares <= 0:
                state.position.avg_price = 0.0

        return True
    else:
        order = MarketOrder(action, qty)
        trade = ib.placeOrder(stock, order)
        logger.info(f"下单: {action} {qty} 股 {stock.symbol}")
        await asyncio.sleep(2)

        if trade.orderStatus.status == "Filled":
            avg_price = trade.orderStatus.avgFillPrice
            logger.info(f"成交: {action} {qty} @ ${avg_price:.2f}")

            if action == "BUY":
                state.position.shares += qty
                state.position.avg_price = avg_price
            else:
                state.position.shares -= qty

            return True
        else:
            logger.warning(f"订单状态: {trade.orderStatus.status}")
            return False


def check_stop_loss_take_profit(state: StrategyState) -> Optional[str]:
    """检查止盈止损"""
    if state.position.shares <= 0 or state.position.avg_price <= 0:
        return None

    pnl_pct = (state.current_price - state.position.avg_price) / \
        state.position.avg_price

    if pnl_pct <= -STOP_LOSS_PCT:
        return f"STOP_LOSS (亏损 {pnl_pct:.2%})"
    elif pnl_pct >= TAKE_PROFIT_PCT:
        return f"TAKE_PROFIT (盈利 {pnl_pct:.2%})"

    return None


def print_status(state: StrategyState, reason: str = ""):
    """打印当前状态"""
    pos = state.position
    print("\n" + "=" * 60)
    print(f"📊 RSI 均值回归策略状态 {'(' + reason + ')' if reason else ''}")
    print("=" * 60)

    elapsed = (datetime.now() -
               state.start_time).total_seconds() if state.start_time else 0
    print(f"⏰ 运行时间: {int(elapsed)}s ({elapsed/60:.1f} 分钟)")
    print(f"📈 当前价格: ${state.current_price:.2f}")
    print(f"📉 RSI({RSI_PERIOD}): {state.current_rsi:.1f}")

    # RSI 状态可视化
    rsi_bar = "▓" * int(state.current_rsi / 5) + "░" * \
        (20 - int(state.current_rsi / 5))
    rsi_status = "🔴超买" if state.current_rsi > RSI_OVERBOUGHT else "🟢超卖" if state.current_rsi < RSI_OVERSOLD else "⚪中性"
    print(f"   [{rsi_bar}] {rsi_status}")

    print("-" * 60)
    print("【持仓】")
    print(
        f"  股数: {pos.shares} 股 {'(多头)' if pos.shares > 0 else '(空仓)' if pos.shares == 0 else '(空头)'}")
    if pos.shares > 0:
        print(f"  成本: ${pos.avg_price:.2f}")
        pnl_pct = (state.current_price - pos.avg_price) / \
            pos.avg_price * 100 if pos.avg_price > 0 else 0
        print(f"  浮盈: ${state.get_unrealized_pnl():+.2f} ({pnl_pct:+.2f}%)")

    print("-" * 60)
    print("【损益】")
    print(f"  已实现 P&L: ${pos.realized_pnl:+.2f}")
    print(f"  未实现 P&L: ${state.get_unrealized_pnl():+.2f}")
    print(f"  总 P&L:     ${state.get_total_pnl():+.2f}")

    print("-" * 60)
    print("【统计】")
    print(
        f"  总信号数: {state.total_signals} (买入: {state.buy_signals}, 卖出: {state.sell_signals})")
    print(f"  交易次数: {pos.total_trades}")
    print(f"  胜率: {pos.win_rate:.1%}")
    print("=" * 60 + "\n")


async def close_all_positions(ib: IB, stock: Stock, state: StrategyState):
    """平仓所有仓位"""
    if state.position.shares <= 0:
        logger.info("无持仓需要平仓")
        return

    logger.info("=" * 60)
    logger.info("🔄 开始平仓...")
    logger.info("=" * 60)

    price = await get_stock_price(ib, stock)
    state.current_price = price

    await execute_trade(ib, stock, "SELL", state.position.shares, price, state)

    logger.info("=" * 60)
    logger.info(f"✅ 平仓完成! 总实现盈亏: ${state.position.realized_pnl:+.2f}")
    logger.info("=" * 60)


async def run_rsi_strategy(ib: IB):
    """运行 RSI 策略"""
    global shutdown_requested

    logger.info("=" * 60)
    logger.info("🚀 启动 RSI 均值回归策略")
    logger.info("=" * 60)
    logger.info(f"标的: {SYMBOL}")
    logger.info(f"RSI 周期: {RSI_PERIOD}")
    logger.info(f"超卖阈值: {RSI_OVERSOLD}")
    logger.info(f"超买阈值: {RSI_OVERBOUGHT}")
    logger.info(f"最大持仓: {MAX_POSITION} 股")
    logger.info(f"止损: {STOP_LOSS_PCT:.1%} | 止盈: {TAKE_PROFIT_PCT:.1%}")
    logger.info(f"模拟模式: {'是' if SIMULATION_MODE else '否'}")
    logger.info("=" * 60)
    logger.info("💡 按 Ctrl+C 可随时退出并自动平仓")
    logger.info("=" * 60)

    # 创建合约
    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]

    # 初始化状态
    state = StrategyState()
    state.start_time = datetime.now()

    # 获取历史数据计算初始 RSI
    logger.info("📥 加载历史数据...")
    historical_prices = await get_historical_prices(ib, stock, days=RSI_PERIOD + 10)
    if historical_prices:
        state.price_history.extend(historical_prices)
        state.current_rsi = calculate_rsi(list(state.price_history))
        logger.info(
            f"✅ 加载 {len(historical_prices)} 天历史数据，初始 RSI: {state.current_rsi:.1f}")
    else:
        logger.warning("⚠️ 无法加载历史数据，将从实时数据开始积累")

    # 获取当前价格
    state.current_price = await get_stock_price(ib, stock)
    logger.info(f"当前价格: ${state.current_price:.2f}")

    print_status(state, "策略启动")

    # 主循环
    logger.info(f"\n⏳ 开始监控，每 {CHECK_INTERVAL_SEC}s 检查一次...")
    logger.info("💡 按 Ctrl+C 退出并平仓\n")

    check_count = 0
    exit_reason = "手动退出"

    try:
        while not shutdown_requested:
            await asyncio.sleep(CHECK_INTERVAL_SEC)
            check_count += 1

            # 获取最新价格
            price = await get_stock_price(ib, stock)
            state.current_price = price
            state.price_history.append(price)

            # 计算 RSI
            state.current_rsi = calculate_rsi(list(state.price_history))

            logger.info(
                f"--- 第 {check_count} 次检查 | 价格: ${price:.2f} | RSI: {state.current_rsi:.1f} ---")

            # 检查止盈止损
            sl_tp = check_stop_loss_take_profit(state)
            if sl_tp:
                logger.warning(f"⚠️ 触发 {sl_tp}，执行平仓")
                await execute_trade(ib, stock, "SELL", state.position.shares, price, state)
                state.total_signals += 1
                state.sell_signals += 1
                print_status(state, sl_tp)
                continue

            # 生成信号
            signal = get_signal(state.current_rsi)

            if signal == "BUY" and state.position.shares < MAX_POSITION:
                # 买入信号
                qty = min(TRADE_SIZE, MAX_POSITION - state.position.shares)
                if qty > 0:
                    logger.info(
                        f"🟢 买入信号! RSI={state.current_rsi:.1f} < {RSI_OVERSOLD}")
                    await execute_trade(ib, stock, "BUY", qty, price, state)
                    state.total_signals += 1
                    state.buy_signals += 1
                    state.signal_history.append(f"BUY @ ${price:.2f}")
                    print_status(state, "买入执行")

            elif signal == "SELL" and state.position.shares > 0:
                # 卖出信号
                qty = min(TRADE_SIZE, state.position.shares)
                if qty > 0:
                    logger.info(
                        f"🔴 卖出信号! RSI={state.current_rsi:.1f} > {RSI_OVERBOUGHT}")
                    await execute_trade(ib, stock, "SELL", qty, price, state)
                    state.total_signals += 1
                    state.sell_signals += 1
                    state.signal_history.append(f"SELL @ ${price:.2f}")
                    print_status(state, "卖出执行")
            else:
                logger.info(
                    f"⚪ 持仓观望 | 信号: {signal} | 持仓: {state.position.shares}")

    except KeyboardInterrupt:
        logger.info("\n⚠️ 收到 Ctrl+C 中断信号...")
        exit_reason = "用户中断 (Ctrl+C)"
    except Exception as e:
        logger.error(f"策略异常: {e}")
        exit_reason = f"异常退出: {e}"

    # 平仓
    logger.info(f"\n📤 退出原因: {exit_reason}")
    await close_all_positions(ib, stock, state)

    # 最终状态
    print_status(state, "策略结束 - 已平仓")

    # 总结
    elapsed = (datetime.now() - state.start_time).total_seconds()
    print("\n" + "=" * 60)
    print("📋 策略总结")
    print("=" * 60)
    print(f"退出原因: {exit_reason}")
    print(f"运行时长: {int(elapsed)}s ({elapsed/60:.1f} 分钟)")
    print(f"检查次数: {check_count}")
    print(
        f"信号数量: {state.total_signals} (买入: {state.buy_signals}, 卖出: {state.sell_signals})")
    print(f"交易次数: {state.position.total_trades}")
    print(f"胜率: {state.position.win_rate:.1%}")
    print("-" * 60)
    print(f"最终实现 P&L: ${state.position.realized_pnl:+.2f}")
    print("=" * 60)


def handle_shutdown(signum, frame):
    """处理退出信号"""
    global shutdown_requested
    shutdown_requested = True
    logger.info("\n🛑 收到退出信号，准备平仓退出...")


async def main():
    """主入口"""
    import signal

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    ib = await connect_ib()
    logger.info(f"已连接 IBKR at {IB_HOST}:{IB_PORT}")

    try:
        await run_rsi_strategy(ib)
    finally:
        ib.disconnect()
        logger.info("已断开连接")


if __name__ == "__main__":
    print("""
============================================================
🎯 RSI 均值回归策略
============================================================
📌 策略原理:
   - RSI < 30 (超卖) → 买入
   - RSI > 70 (超买) → 卖出
   
📌 使用方法:
   - 按 Ctrl+C 随时退出并自动平仓
   - 设置 RSI_OVERSOLD/RSI_OVERBOUGHT 调整阈值
   - 设置 RSI_STOP_LOSS=0.05 调整止损比例
   - 设置 RSI_SIMULATION=false 启用真实交易
============================================================
""")
    asyncio.run(main())
