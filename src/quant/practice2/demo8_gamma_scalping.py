"""
Demo 8: Gamma Scalping Strategy (伽马套利策略)

策略原理：
1. 买入 N 张 ATM Call 期权（持有正 Gamma）
2. 卖空标的股票来对冲 Delta，使组合 Delta 中性
3. 当股价波动导致 Delta 偏离阈值时，调整股票仓位恢复 Delta 中性
4. 从股价来回波动中"刮取" Gamma 收益

配置说明：
- OPTION_CONTRACTS: 买入的期权张数（每张=100股），控制资金量
- DELTA_THRESHOLD: Delta 偏离阈值，超过时触发再平衡
- REBALANCE_INTERVAL_SEC: 检查间隔（秒）
- STRATEGY_DURATION_SEC: 策略运行总时长（秒）
"""
import asyncio
import os
import math
import logging
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

from ib_async import IB, Stock, Option, LimitOrder, MarketOrder

# ========== 日志配置 ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ========== 连接配置 ==========
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "18"))

# ========== 标的配置 ==========
SYMBOL = os.getenv("GS_SYMBOL", "AAPL")
EXCHANGE = os.getenv("GS_EXCHANGE", "SMART")
CURRENCY = os.getenv("GS_CURRENCY", "USD")

# ========== 策略配置 ==========
OPTION_CONTRACTS = int(os.getenv("GS_OPTION_CONTRACTS", "3"))  # 期权张数
# Delta 偏离阈值 (相对于总Delta)
DELTA_THRESHOLD = float(os.getenv("GS_DELTA_THRESHOLD", "0.10"))
REBALANCE_INTERVAL_SEC = int(
    os.getenv("GS_REBALANCE_INTERVAL", "30"))  # 检查间隔（秒）
FALLBACK_PRICE = float(os.getenv("GS_FALLBACK_PRICE", "280"))  # 备用股价（市场关闭时使用）

# ========== 止损配置 ==========
# 止损阈值（相对于初始期权成本的百分比，如 0.50 = 亏损50%时止损）
STOP_LOSS_THRESHOLD = float(os.getenv("GS_STOP_LOSS", "0.50"))
# 是否启用止损
STOP_LOSS_ENABLED = os.getenv("GS_STOP_LOSS_ENABLED", "true").lower() == "true"

# ========== 行情配置 ==========
USE_DELAYED_DATA = os.getenv(
    "GS_USE_DELAYED", "true").lower() == "true"  # 是否使用延迟行情

# ========== 模拟模式 ==========
SIMULATION_MODE = os.getenv(
    "GS_SIMULATION", "true").lower() == "true"  # 模拟模式（不下真单）

# ========== 全局退出标志 ==========
shutdown_requested = False


@dataclass
class Position:
    """持仓状态"""
    option_contracts: int = 0  # 期权张数（正=多头）
    option_delta_per_contract: float = 0.0  # 每张期权的 Delta
    stock_shares: int = 0  # 股票股数（负=空头）

    # 成本追踪
    option_cost: float = 0.0  # 期权购买成本
    stock_pnl: float = 0.0  # 股票交易累计盈亏
    stock_avg_price: float = 0.0  # 股票平均成本价

    # 交易统计
    rebalance_count: int = 0  # 再平衡次数
    total_stock_traded: int = 0  # 累计股票交易量

    @property
    def total_option_delta(self) -> float:
        """期权组合总 Delta（每张期权代表100股）"""
        return self.option_contracts * self.option_delta_per_contract * 100

    @property
    def total_stock_delta(self) -> float:
        """股票仓位 Delta（股数即Delta）"""
        return float(self.stock_shares)

    @property
    def net_delta(self) -> float:
        """组合净 Delta"""
        return self.total_option_delta + self.total_stock_delta


@dataclass
class StrategyState:
    """策略状态"""
    position: Position = field(default_factory=Position)
    start_time: Optional[datetime] = None
    initial_stock_price: float = 0.0
    current_stock_price: float = 0.0
    current_option_price: float = 0.0
    current_option_delta: float = 0.0

    # 累计收益
    realized_pnl: float = 0.0  # 已实现盈亏（来自股票交易）

    def get_unrealized_pnl(self) -> float:
        """计算未实现盈亏"""
        # 期权价值变化
        option_value = self.position.option_contracts * self.current_option_price * 100
        option_pnl = option_value - self.position.option_cost

        # 股票未实现盈亏（空头）
        if self.position.stock_shares != 0 and self.position.stock_avg_price > 0:
            stock_unrealized = (self.position.stock_avg_price -
                                self.current_stock_price) * abs(self.position.stock_shares)
            if self.position.stock_shares > 0:  # 多头
                stock_unrealized = (
                    self.current_stock_price - self.position.stock_avg_price) * self.position.stock_shares
        else:
            stock_unrealized = 0.0

        return option_pnl + stock_unrealized

    def get_total_pnl(self) -> float:
        """计算总盈亏"""
        return self.realized_pnl + self.get_unrealized_pnl()


async def connect_ib() -> IB:
    """连接 IBKR"""
    ib = IB()
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    if USE_DELAYED_DATA:
        ib.reqMarketDataType(3)  # 延迟行情
    else:
        ib.reqMarketDataType(1)  # 实时行情
    return ib


async def get_atm_option(ib: IB, stock: Stock, stock_price: float) -> Optional[Option]:
    """获取 ATM Call 期权合约"""
    chains = await ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)
    if not chains:
        logger.error("未找到期权链")
        return None

    # 选择 SMART 交易所的链
    chain = next((c for c in chains if c.exchange == "SMART"), chains[0])

    # 选择最近的到期日（至少7天后，避免临近到期）
    expirations = sorted([exp for exp in chain.expirations])
    if not expirations:
        logger.error("未找到有效到期日")
        return None

    # 选择第2个或更远的到期日（如果可能）
    next_expiry = expirations[1] if len(expirations) > 1 else expirations[0]

    # 选择 ATM Strike（最接近当前价格）
    strikes = sorted([s for s in chain.strikes])
    if not strikes:
        logger.error("未找到有效行权价")
        return None

    atm_strike = min(strikes, key=lambda x: abs(x - stock_price))

    logger.info(f"选择期权: {stock.symbol} {next_expiry} Call @ {atm_strike}")

    option = Option(stock.symbol, next_expiry, atm_strike, "C", "SMART")
    qualified = await ib.qualifyContractsAsync(option)
    if not qualified:
        logger.error("期权合约验证失败")
        return None

    return qualified[0]


async def get_option_greeks(ib: IB, option: Option, wait_sec: float = 3.0, stock_price: float = 0.0) -> tuple[float, float]:
    """获取期权价格和 Delta"""
    ticker = ib.reqMktData(option, "106", False, False)  # 106 = Greeks
    await asyncio.sleep(wait_sec)

    price = ticker.last if not math.isnan(ticker.last) else ticker.close
    if price is None or math.isnan(price):
        price = (ticker.bid + ticker.ask) / \
            2 if ticker.bid and ticker.ask else 0.0

    # 如果无法获取期权价格，使用内在价值估算
    if price is None or math.isnan(price) or price <= 0:
        if stock_price > 0 and option.strike:
            intrinsic = max(
                0, stock_price - option.strike) if option.right == "C" else max(0, option.strike - stock_price)
            price = intrinsic + 2.0  # 内在价值 + 估算时间价值
            logger.warning(f"无法获取期权价格，使用估算价格: ${price:.2f}")

    # 获取 Delta（从 Greeks）
    delta = 0.5  # 默认 ATM Delta
    if ticker.modelGreeks:
        delta = ticker.modelGreeks.delta or 0.5
    elif ticker.lastGreeks:
        delta = ticker.lastGreeks.delta or 0.5

    ib.cancelMktData(option)
    return price, delta


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

    # 如果无法获取价格，使用备用价格
    if price is None or math.isnan(price) or price <= 0:
        logger.warning(f"无法获取实时股价，使用备用价格: ${FALLBACK_PRICE:.2f}")
        price = FALLBACK_PRICE

    ib.cancelMktData(stock)
    return price


def calculate_hedge_shares(state: StrategyState) -> int:
    """计算需要对冲的股票数量"""
    # 目标：使净 Delta = 0
    # 期权 Delta 为正（多头 Call），需要卖空股票来对冲
    target_stock_delta = -state.position.total_option_delta
    current_stock_delta = state.position.stock_shares
    shares_to_trade = int(target_stock_delta - current_stock_delta)
    return shares_to_trade


def print_status(state: StrategyState, reason: str = ""):
    """打印当前状态"""
    pos = state.position
    print("\n" + "=" * 60)
    print(f"📊 Gamma Scalping 状态 {'(' + reason + ')' if reason else ''}")
    print("=" * 60)
    print(
        f"⏰ 运行时间: {(datetime.now() - state.start_time).seconds}s" if state.start_time else "")
    print(f"📈 股价: ${state.current_stock_price:.2f} (初始: ${state.initial_stock_price:.2f}, "
          f"变化: {((state.current_stock_price/state.initial_stock_price)-1)*100:+.2f}%)")
    print("-" * 60)
    print("【持仓】")
    print(
        f"  期权: {pos.option_contracts} 张 Call (Delta/张: {pos.option_delta_per_contract:.3f})")
    print(f"  股票: {pos.stock_shares} 股 {'(空头)' if pos.stock_shares < 0 else '(多头)' if pos.stock_shares > 0 else ''}")
    print("-" * 60)
    print("【Delta】")
    print(f"  期权 Delta: {pos.total_option_delta:+.1f}")
    print(f"  股票 Delta: {pos.total_stock_delta:+.1f}")
    print(f"  净 Delta:   {pos.net_delta:+.1f}")
    print("-" * 60)
    print("【损益】")
    print(f"  已实现 P&L: ${state.realized_pnl:+.2f}")
    print(f"  未实现 P&L: ${state.get_unrealized_pnl():+.2f}")
    print(f"  总 P&L:     ${state.get_total_pnl():+.2f}")
    print("-" * 60)
    print("【统计】")
    print(f"  再平衡次数: {pos.rebalance_count}")
    print(f"  累计交易量: {pos.total_stock_traded} 股")
    print("=" * 60 + "\n")


async def execute_stock_trade(ib: IB, stock: Stock, shares: int, price: float, state: StrategyState):
    """执行股票交易（模拟或真实）"""
    if shares == 0:
        return

    action = "SELL" if shares < 0 else "BUY"
    qty = abs(shares)

    if SIMULATION_MODE:
        # 模拟交易
        logger.info(f"[模拟] {action} {qty} 股 {stock.symbol} @ ${price:.2f}")

        # 更新仓位
        old_shares = state.position.stock_shares
        new_shares = old_shares + shares

        # 计算已实现盈亏（如果是平仓）
        if old_shares != 0 and ((old_shares > 0 and shares < 0) or (old_shares < 0 and shares > 0)):
            # 平仓
            close_qty = min(abs(old_shares), abs(shares))
            if old_shares < 0:  # 空头平仓
                pnl = (state.position.stock_avg_price - price) * close_qty
            else:  # 多头平仓
                pnl = (price - state.position.stock_avg_price) * close_qty
            state.realized_pnl += pnl
            logger.info(f"  平仓 {close_qty} 股，实现盈亏: ${pnl:+.2f}")

        # 更新平均成本（简化：使用最新价格）
        if new_shares != 0:
            state.position.stock_avg_price = price

        state.position.stock_shares = new_shares
        state.position.total_stock_traded += qty
        state.position.rebalance_count += 1

    else:
        # 真实交易
        order = MarketOrder(action, qty)
        trade = ib.placeOrder(stock, order)
        logger.info(f"下单: {action} {qty} 股 {stock.symbol}")

        # 等待成交
        await asyncio.sleep(2)
        if trade.orderStatus.status == "Filled":
            avg_price = trade.orderStatus.avgFillPrice
            logger.info(f"成交: {action} {qty} @ ${avg_price:.2f}")
            state.position.stock_shares += shares
            state.position.stock_avg_price = avg_price
            state.position.total_stock_traded += qty
            state.position.rebalance_count += 1
        else:
            logger.warning(f"订单状态: {trade.orderStatus.status}")


async def initialize_position(ib: IB, stock: Stock, option: Option, state: StrategyState):
    """初始化仓位：买入期权 + 对冲"""
    logger.info(f"初始化仓位: 买入 {OPTION_CONTRACTS} 张 Call 期权...")

    # 获取期权价格和 Delta
    stock_price = await get_stock_price(ib, stock)
    opt_price, opt_delta = await get_option_greeks(ib, option, stock_price=stock_price)

    if opt_price <= 0 or stock_price <= 0:
        raise RuntimeError("无法获取有效价格")

    # 记录初始状态
    state.initial_stock_price = stock_price
    state.current_stock_price = stock_price
    state.current_option_price = opt_price
    state.current_option_delta = opt_delta

    # 设置期权仓位
    state.position.option_contracts = OPTION_CONTRACTS
    state.position.option_delta_per_contract = opt_delta
    state.position.option_cost = OPTION_CONTRACTS * opt_price * 100  # 每张期权 = 100股

    logger.info(f"期权价格: ${opt_price:.2f}, Delta: {opt_delta:.3f}")
    logger.info(f"期权成本: ${state.position.option_cost:.2f}")

    # 计算并执行初始对冲
    hedge_shares = calculate_hedge_shares(state)
    logger.info(f"初始对冲: 需要卖空 {abs(hedge_shares)} 股")

    await execute_stock_trade(ib, stock, hedge_shares, stock_price, state)

    print_status(state, "初始建仓完成")


async def rebalance_if_needed(ib: IB, stock: Stock, option: Option, state: StrategyState) -> bool:
    """检查并执行再平衡（如需要）"""
    # 获取最新价格和 Delta
    stock_price = await get_stock_price(ib, stock)
    opt_price, opt_delta = await get_option_greeks(ib, option, stock_price=stock_price)

    # 更新状态
    state.current_stock_price = stock_price
    state.current_option_price = opt_price
    state.current_option_delta = opt_delta
    state.position.option_delta_per_contract = opt_delta

    # 计算 Delta 偏离
    net_delta = state.position.net_delta
    total_option_delta = abs(state.position.total_option_delta)

    if total_option_delta == 0:
        return False

    delta_ratio = abs(net_delta) / total_option_delta

    logger.info(
        f"检查 Delta: 净={net_delta:+.1f}, 偏离比例={delta_ratio:.2%}, 阈值={DELTA_THRESHOLD:.2%}")

    if delta_ratio > DELTA_THRESHOLD:
        logger.info(f"⚠️ Delta 偏离超过阈值，触发再平衡!")

        hedge_shares = calculate_hedge_shares(state)
        if hedge_shares != 0:
            await execute_stock_trade(ib, stock, hedge_shares, stock_price, state)
            print_status(state, "再平衡完成")
            return True

    return False


def check_stop_loss(state: StrategyState) -> bool:
    """检查是否触发止损"""
    if not STOP_LOSS_ENABLED:
        return False

    if state.position.option_cost <= 0:
        return False

    total_pnl = state.get_total_pnl()
    loss_ratio = -total_pnl / state.position.option_cost

    if loss_ratio >= STOP_LOSS_THRESHOLD:
        logger.warning(
            f"🛑 触发止损! 亏损比例: {loss_ratio:.2%} >= 阈值 {STOP_LOSS_THRESHOLD:.2%}")
        return True

    return False


async def close_all_positions(ib: IB, stock: Stock, option: Option, state: StrategyState):
    """平仓所有仓位"""
    logger.info("="*60)
    logger.info("🔄 开始平仓所有仓位...")
    logger.info("="*60)

    # 获取最新价格
    stock_price = await get_stock_price(ib, stock)
    state.current_stock_price = stock_price

    # 平掉股票仓位
    if state.position.stock_shares != 0:
        shares_to_close = -state.position.stock_shares
        logger.info(
            f"平仓股票: {'买入' if shares_to_close > 0 else '卖出'} {abs(shares_to_close)} 股")

        if SIMULATION_MODE:
            # 计算平仓盈亏
            if state.position.stock_shares < 0:  # 空头平仓
                pnl = (state.position.stock_avg_price - stock_price) * \
                    abs(state.position.stock_shares)
            else:  # 多头平仓
                pnl = (stock_price - state.position.stock_avg_price) * \
                    state.position.stock_shares
            state.realized_pnl += pnl
            logger.info(f"  [模拟] 股票平仓，实现盈亏: ${pnl:+.2f}")
            state.position.stock_shares = 0
        else:
            action = "BUY" if shares_to_close > 0 else "SELL"
            order = MarketOrder(action, abs(shares_to_close))
            trade = ib.placeOrder(stock, order)
            await asyncio.sleep(3)
            if trade.orderStatus.status == "Filled":
                avg_price = trade.orderStatus.avgFillPrice
                if state.position.stock_shares < 0:
                    pnl = (state.position.stock_avg_price - avg_price) * \
                        abs(state.position.stock_shares)
                else:
                    pnl = (avg_price - state.position.stock_avg_price) * \
                        state.position.stock_shares
                state.realized_pnl += pnl
                logger.info(f"  股票平仓成交 @ ${avg_price:.2f}，实现盈亏: ${pnl:+.2f}")
                state.position.stock_shares = 0
            else:
                logger.warning(f"  股票平仓订单状态: {trade.orderStatus.status}")

    # 期权平仓（卖出期权）
    if state.position.option_contracts > 0:
        logger.info(f"平仓期权: 卖出 {state.position.option_contracts} 张 Call")

        if SIMULATION_MODE:
            opt_price, _ = await get_option_greeks(ib, option, stock_price=stock_price)
            sell_value = state.position.option_contracts * opt_price * 100
            option_pnl = sell_value - state.position.option_cost
            state.realized_pnl += option_pnl
            logger.info(
                f"  [模拟] 期权卖出价值: ${sell_value:.2f}，实现盈亏: ${option_pnl:+.2f}")
            state.position.option_contracts = 0
            state.position.option_cost = 0
        else:
            order = MarketOrder("SELL", state.position.option_contracts)
            trade = ib.placeOrder(option, order)
            await asyncio.sleep(3)
            if trade.orderStatus.status == "Filled":
                avg_price = trade.orderStatus.avgFillPrice
                sell_value = state.position.option_contracts * avg_price * 100
                option_pnl = sell_value - state.position.option_cost
                state.realized_pnl += option_pnl
                logger.info(
                    f"  期权平仓成交 @ ${avg_price:.2f}，实现盈亏: ${option_pnl:+.2f}")
                state.position.option_contracts = 0
                state.position.option_cost = 0
            else:
                logger.warning(f"  期权平仓订单状态: {trade.orderStatus.status}")

    logger.info("="*60)
    logger.info(f"✅ 平仓完成! 总实现盈亏: ${state.realized_pnl:+.2f}")
    logger.info("="*60)


async def run_gamma_scalping(ib: IB):
    """运行 Gamma Scalping 策略"""
    global shutdown_requested

    logger.info("=" * 60)
    logger.info("🚀 启动 Gamma Scalping 策略")
    logger.info("=" * 60)
    logger.info(f"标的: {SYMBOL}")
    logger.info(f"期权张数: {OPTION_CONTRACTS}")
    logger.info(f"Delta 阈值: {DELTA_THRESHOLD:.2%}")
    logger.info(f"检查间隔: {REBALANCE_INTERVAL_SEC}s")
    logger.info(
        f"止损阈值: {STOP_LOSS_THRESHOLD:.2%} ({'启用' if STOP_LOSS_ENABLED else '禁用'})")
    logger.info(f"模拟模式: {'是' if SIMULATION_MODE else '否'}")
    logger.info("=" * 60)
    logger.info("💡 按 Ctrl+C 可随时退出并自动平仓")
    logger.info("=" * 60)

    # 创建合约
    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]

    # 获取股价
    stock_price = await get_stock_price(ib, stock)
    if stock_price <= 0:
        stock_price = FALLBACK_PRICE
        logger.warning(f"无法获取股价，使用备用价格: ${stock_price}")

    logger.info(f"当前股价: ${stock_price:.2f}")

    # 获取 ATM 期权
    option = await get_atm_option(ib, stock, stock_price)
    if not option:
        raise RuntimeError("无法获取期权合约")

    # 初始化策略状态
    state = StrategyState()
    state.start_time = datetime.now()

    # 初始化仓位
    await initialize_position(ib, stock, option, state)

    # 主循环
    logger.info(f"\n⏳ 开始监控，每 {REBALANCE_INTERVAL_SEC}s 检查一次 Delta...")
    logger.info("💡 按 Ctrl+C 退出并平仓\n")

    check_count = 0
    exit_reason = "手动退出"

    try:
        while not shutdown_requested:
            await asyncio.sleep(REBALANCE_INTERVAL_SEC)
            check_count += 1

            elapsed = (datetime.now() - state.start_time).total_seconds()
            logger.info(f"--- 第 {check_count} 次检查 (运行 {int(elapsed)}s) ---")

            rebalanced = await rebalance_if_needed(ib, stock, option, state)

            if not rebalanced:
                logger.info("Delta 在阈值范围内，无需再平衡")

            # 检查止损
            if check_stop_loss(state):
                exit_reason = f"触发止损 (亏损超过 {STOP_LOSS_THRESHOLD:.0%})"
                break

    except KeyboardInterrupt:
        logger.info("\n⚠️ 收到 Ctrl+C 中断信号...")
        exit_reason = "用户中断 (Ctrl+C)"
    except Exception as e:
        logger.error(f"策略异常: {e}")
        exit_reason = f"异常退出: {e}"

    # 平仓所有仓位
    logger.info(f"\n📤 退出原因: {exit_reason}")
    await close_all_positions(ib, stock, option, state)

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
    print(f"再平衡次数: {state.position.rebalance_count}")
    print(f"累计交易量: {state.position.total_stock_traded} 股")
    print("-" * 60)
    print(f"期权成本: ${state.position.option_cost:.2f}")
    print(f"最终实现 P&L: ${state.realized_pnl:+.2f}")
    print("=" * 60)


def handle_shutdown(signum, frame):
    """处理退出信号"""
    global shutdown_requested
    shutdown_requested = True
    logger.info("\n🛑 收到退出信号，准备平仓退出...")


async def main():
    """主入口"""
    import signal

    # 注册信号处理器
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    ib = await connect_ib()
    logger.info(f"已连接 IBKR at {IB_HOST}:{IB_PORT}")

    try:
        await run_gamma_scalping(ib)
    finally:
        ib.disconnect()
        logger.info("已断开连接")


if __name__ == "__main__":
    print("""
============================================================
🎯 Gamma Scalping 策略
============================================================
📌 使用方法:
   - 按 Ctrl+C 随时退出并自动平仓
   - 设置 GS_STOP_LOSS=0.30 调整止损比例（默认50%）
   - 设置 GS_STOP_LOSS_ENABLED=false 禁用止损
   - 设置 GS_SIMULATION=false 启用真实交易
============================================================
""")
    asyncio.run(main())
