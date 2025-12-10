"""
Demo 12: Iron Condor Strategy (铁鹰策略)

================================================================================
📌 策略原理
================================================================================
Iron Condor = 卖出虚值 Call + 卖出虚值 Put + 买入更虚值的 Call/Put 保护

结构示意：
  买Put($252) ← 卖Put($266) ← 当前价$280 → 卖Call($294) → 买Call($308)
  └─── 保护翅膀 ───┘              └─── 保护翅膀 ───┘

收益逻辑：
1. 卖出期权收取权利金（时间价值）
2. 只要股价在 $266~$294 之间到期，赚取全部权利金
3. 买入的保护翅膀限制最大亏损

================================================================================
📌 触发条件 & 参数说明
================================================================================
IC_SHORT_OTM=0.05    # 卖出期权虚值 5%（当前价 × 1.05 = 卖Call行权价）
IC_LONG_OTM=0.10     # 买入期权虚值 10%（保护翅膀更远）
IC_PROFIT_TARGET=0.50  # 盈利达到初始权利金的 50% 时止盈
IC_STOP_LOSS=1.0       # 亏损达到初始权利金的 100% 时止损

示例（股价 $280）：
  - 卖 Put: $280 × 0.95 = $266
  - 卖 Call: $280 × 1.05 = $294
  - 买 Put: $280 × 0.90 = $252
  - 买 Call: $280 × 1.10 = $308
  - 盈利区间: $266 ~ $294

================================================================================
📌 使用场景
================================================================================
✅ 适合：
   - 预期股价横盘震荡
   - IV（隐含波动率）较高时建仓更有利
   - 距离财报/重大事件较远时

❌ 不适合：
   - 预期大涨大跌
   - 临近财报（可能跳空）
   - 低 IV 环境（权利金太少）

================================================================================
📌 运行方式（推荐：每天检查 1-2 次）
================================================================================
# 方式1: 每天开盘后运行
uv run demo12_iron_condor.py

# 方式2: cron 定时任务（美东时间 9:35 和 15:30 各检查一次）
# 35 9,15 * * 1-5 cd /path/to/project && uv run demo12_iron_condor.py

# 首次运行：自动建立 Iron Condor 仓位
# 后续运行：监控价格，达到止盈/止损条件自动平仓

================================================================================
📌 盈亏分析
================================================================================
假设：收到权利金 $200（卖出 - 买入）

最大盈利：$200（股价在 $266~$294 之间到期）
最大亏损：翅膀宽度 × 100 - $200 = ($294-$266)/2 × 100 × 2 - $200 = $2600（极端情况）
盈亏比：约 1:13（盈利有限，亏损也有限但较大）

建议：控制仓位，不要 All-in

================================================================================
📌 风险提示
================================================================================
⚠️ 价格突破盈利区间会快速亏损
⚠️ 财报/重大新闻可能导致跳空突破
⚠️ 美式期权可能被提前行权
⚠️ 流动性差时平仓成本高

================================================================================
"""
import asyncio
import os
import math
import logging
from datetime import datetime
from typing import Optional, Tuple
from dataclasses import dataclass, field

from ib_async import IB, Stock, Option, MarketOrder

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ========== 配置 ==========
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "22"))

SYMBOL = os.getenv("IC_SYMBOL", "AAPL")
EXCHANGE = os.getenv("IC_EXCHANGE", "SMART")
CURRENCY = os.getenv("IC_CURRENCY", "USD")

# 策略配置
NUM_CONTRACTS = int(os.getenv("IC_CONTRACTS", "1"))  # 合约数量
SHORT_OTM_PCT = float(os.getenv("IC_SHORT_OTM", "0.05"))  # 卖出期权虚值程度 5%
LONG_OTM_PCT = float(os.getenv("IC_LONG_OTM", "0.10"))  # 买入期权虚值程度 10%
PROFIT_TARGET_PCT = float(os.getenv("IC_PROFIT_TARGET", "0.50"))  # 盈利50%平仓
STOP_LOSS_PCT = float(os.getenv("IC_STOP_LOSS", "1.0"))  # 亏损100%平仓（亏完权利金）

CHECK_INTERVAL_SEC = int(os.getenv("IC_CHECK_INTERVAL", "60"))
FALLBACK_PRICE = float(os.getenv("IC_FALLBACK_PRICE", "280"))

USE_DELAYED_DATA = os.getenv("IC_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("IC_SIMULATION", "true").lower() == "true"

shutdown_requested = False


@dataclass
class IronCondorPosition:
    """Iron Condor 仓位"""
    # 卖出腿
    short_call_strike: float = 0.0
    short_put_strike: float = 0.0
    # 买入腿（保护）
    long_call_strike: float = 0.0
    long_put_strike: float = 0.0

    expiry: str = ""
    contracts: int = 0

    # 收入
    initial_credit: float = 0.0  # 初始净收入（权利金）
    current_value: float = 0.0  # 当前持仓价值

    def get_max_profit(self) -> float:
        """最大盈利 = 初始权利金"""
        return self.initial_credit

    def get_max_loss(self) -> float:
        """最大亏损 = 翅膀宽度 - 权利金"""
        wing_width = (self.short_call_strike - self.long_put_strike) / \
            2 - (self.short_call_strike - self.short_put_strike) / 2
        # 简化：取翅膀宽度
        call_wing = self.long_call_strike - self.short_call_strike
        return call_wing * 100 * self.contracts - self.initial_credit

    def get_profit_range(self) -> Tuple[float, float]:
        """盈利区间"""
        return (self.short_put_strike, self.short_call_strike)


@dataclass
class StrategyState:
    position: IronCondorPosition = field(default_factory=IronCondorPosition)
    start_time: Optional[datetime] = None
    current_price: float = 0.0
    initial_price: float = 0.0

    # 期权合约
    short_call: Optional[Option] = None
    short_put: Optional[Option] = None
    long_call: Optional[Option] = None
    long_put: Optional[Option] = None

    def get_pnl(self) -> float:
        """当前盈亏 = 初始收入 - 当前价值"""
        return self.position.initial_credit - self.position.current_value

    def get_pnl_pct(self) -> float:
        """盈亏比例"""
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
    """获取指定期权"""
    option = Option(stock.symbol, expiry, strike, right, "SMART")
    try:
        qualified = await ib.qualifyContractsAsync(option)
        if qualified and qualified[0]:
            return qualified[0]
    except:
        pass
    return None


async def get_option_chain_info(ib: IB, stock: Stock) -> Tuple[list, list]:
    """获取期权链信息"""
    chains = await ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)
    if not chains:
        return [], []

    chain = next((c for c in chains if c.exchange == "SMART"), chains[0])

    today = datetime.now().strftime("%Y%m%d")
    valid_expiries = sorted([e for e in chain.expirations if e > today])
    strikes = sorted(chain.strikes)

    return valid_expiries, strikes


def print_status(state: StrategyState, reason: str = ""):
    pos = state.position
    print("\n" + "=" * 60)
    print(f"📊 Iron Condor 状态 {'(' + reason + ')' if reason else ''}")
    print("=" * 60)

    elapsed = (datetime.now() -
               state.start_time).total_seconds() if state.start_time else 0
    print(f"⏰ 运行: {int(elapsed/60)} 分钟 | 📈 股价: ${state.current_price:.2f}")

    # 图形化显示结构
    print("-" * 60)
    print("【Iron Condor 结构】")
    profit_range = pos.get_profit_range()
    print(f"  买 Put ${pos.long_put_strike:.0f} ← 卖 Put ${pos.short_put_strike:.0f} ←←← 当前 ${state.current_price:.0f} →→→ 卖 Call ${pos.short_call_strike:.0f} → 买 Call ${pos.long_call_strike:.0f}")
    print(f"  盈利区间: ${profit_range[0]:.2f} ~ ${profit_range[1]:.2f}")

    # 价格位置可视化
    range_width = pos.long_call_strike - pos.long_put_strike
    price_pos = (state.current_price - pos.long_put_strike) / range_width
    bar_len = 40
    price_idx = int(price_pos * bar_len)
    price_idx = max(0, min(bar_len, price_idx))

    bar = "─" * bar_len
    bar = bar[:price_idx] + "●" + bar[price_idx+1:]
    print(f"  [{bar}]")

    # 位置状态
    if state.current_price < pos.short_put_strike:
        danger = (pos.short_put_strike - state.current_price) / \
            pos.short_put_strike * 100
        print(f"  ⚠️ 低于卖 Put 行权价 {danger:.1f}%")
    elif state.current_price > pos.short_call_strike:
        danger = (state.current_price - pos.short_call_strike) / \
            pos.short_call_strike * 100
        print(f"  ⚠️ 高于卖 Call 行权价 {danger:.1f}%")
    else:
        print(f"  ✅ 价格在盈利区间内")

    print("-" * 60)
    print("【盈亏】")
    print(f"  初始权利金: ${pos.initial_credit:.2f}")
    print(f"  当前价值: ${pos.current_value:.2f}")
    print(f"  当前盈亏: ${state.get_pnl():+.2f} ({state.get_pnl_pct():+.1%})")
    print(f"  最大盈利: ${pos.get_max_profit():.2f}")
    print(f"  最大亏损: ${pos.get_max_loss():.2f}")
    print("=" * 60)


async def build_iron_condor(ib: IB, stock: Stock, state: StrategyState):
    """建立 Iron Condor 仓位"""
    price = await get_stock_price(ib, stock)
    state.current_price = price
    state.initial_price = price

    # 获取期权链
    expiries, strikes = await get_option_chain_info(ib, stock)
    if not expiries or not strikes:
        raise RuntimeError("无法获取期权链")

    # 选择到期日（2-4周后）
    expiry = expiries[1] if len(expiries) > 1 else expiries[0]

    # 计算行权价
    short_call_strike = min(strikes, key=lambda x: abs(
        x - price * (1 + SHORT_OTM_PCT)) if x > price else float('inf'))
    short_put_strike = min(strikes, key=lambda x: abs(
        x - price * (1 - SHORT_OTM_PCT)) if x < price else float('inf'))
    long_call_strike = min(strikes, key=lambda x: abs(
        x - price * (1 + LONG_OTM_PCT)) if x > short_call_strike else float('inf'))
    long_put_strike = min(strikes, key=lambda x: abs(
        x - price * (1 - LONG_OTM_PCT)) if x < short_put_strike else float('inf'))

    logger.info(f"构建 Iron Condor @ {expiry}")
    logger.info(
        f"  买 Put ${long_put_strike} | 卖 Put ${short_put_strike} | 卖 Call ${short_call_strike} | 买 Call ${long_call_strike}")

    # 获取期权合约
    state.short_call = await find_option(ib, stock, "C", short_call_strike, expiry)
    state.short_put = await find_option(ib, stock, "P", short_put_strike, expiry)
    state.long_call = await find_option(ib, stock, "C", long_call_strike, expiry)
    state.long_put = await find_option(ib, stock, "P", long_put_strike, expiry)

    if not all([state.short_call, state.short_put, state.long_call, state.long_put]):
        raise RuntimeError("无法获取所有期权腿")

    # 获取价格并计算净收入
    sc_price = await get_option_price(ib, state.short_call)
    sp_price = await get_option_price(ib, state.short_put)
    lc_price = await get_option_price(ib, state.long_call)
    lp_price = await get_option_price(ib, state.long_put)

    # 净收入 = 卖出 - 买入
    net_credit = (sc_price + sp_price - lc_price -
                  lp_price) * 100 * NUM_CONTRACTS

    if SIMULATION_MODE:
        logger.info(f"[模拟] 建立 Iron Condor, 净收入: ${net_credit:.2f}")

    # 更新状态
    state.position.short_call_strike = short_call_strike
    state.position.short_put_strike = short_put_strike
    state.position.long_call_strike = long_call_strike
    state.position.long_put_strike = long_put_strike
    state.position.expiry = expiry
    state.position.contracts = NUM_CONTRACTS
    state.position.initial_credit = net_credit
    state.position.current_value = net_credit


async def update_position_value(ib: IB, state: StrategyState):
    """更新持仓价值"""
    if not all([state.short_call, state.short_put, state.long_call, state.long_put]):
        return

    sc_price = await get_option_price(ib, state.short_call)
    sp_price = await get_option_price(ib, state.short_put)
    lc_price = await get_option_price(ib, state.long_call)
    lp_price = await get_option_price(ib, state.long_put)

    # 当前价值 = 平仓成本 = (买入价 - 卖出价)
    # 如果做空，平仓需要买入
    current_value = (sc_price + sp_price - lc_price -
                     lp_price) * 100 * NUM_CONTRACTS
    state.position.current_value = current_value


async def close_iron_condor(ib: IB, state: StrategyState):
    """平仓 Iron Condor"""
    logger.info("🔄 平仓 Iron Condor...")
    await update_position_value(ib, state)

    final_pnl = state.get_pnl()
    if SIMULATION_MODE:
        logger.info(f"[模拟] 平仓, 最终盈亏: ${final_pnl:+.2f}")

    state.position.contracts = 0


async def run_iron_condor(ib: IB):
    global shutdown_requested

    logger.info("🚀 启动 Iron Condor 策略")
    logger.info(f"标的: {SYMBOL} | 合约: {NUM_CONTRACTS}")
    logger.info(f"卖出虚值: {SHORT_OTM_PCT:.1%} | 买入虚值: {LONG_OTM_PCT:.1%}")
    logger.info("💡 按 Ctrl+C 退出")

    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]

    state = StrategyState()
    state.start_time = datetime.now()

    # 建仓
    await build_iron_condor(ib, stock, state)
    print_status(state, "建仓")

    check_count = 0
    exit_reason = "手动退出"

    try:
        while not shutdown_requested:
            await asyncio.sleep(CHECK_INTERVAL_SEC)
            check_count += 1

            state.current_price = await get_stock_price(ib, stock)
            await update_position_value(ib, state)

            pnl_pct = state.get_pnl_pct()
            logger.info(
                f"--- 检查 #{check_count} | 股价: ${state.current_price:.2f} | P&L: {pnl_pct:+.1%} ---")

            # 检查止盈
            if pnl_pct >= PROFIT_TARGET_PCT:
                logger.info(f"✅ 达到盈利目标 {pnl_pct:.1%}")
                exit_reason = f"止盈 ({pnl_pct:.1%})"
                break

            # 检查止损
            if pnl_pct <= -STOP_LOSS_PCT:
                logger.info(f"🛑 触发止损 {pnl_pct:.1%}")
                exit_reason = f"止损 ({pnl_pct:.1%})"
                break

    except KeyboardInterrupt:
        exit_reason = "用户中断"

    logger.info(f"📤 退出: {exit_reason}")
    await close_iron_condor(ib, state)
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
        await run_iron_condor(ib)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    print("""
🎯 Iron Condor 策略 - 市场中性期权收益策略
   预期低波动时建仓，赚取时间价值衰减
   按 Ctrl+C 退出
""")
    asyncio.run(main())
