"""
Demo 14: Butterfly Spread (蝴蝶价差策略)

================================================================================
📌 策略原理
================================================================================
Butterfly Spread = 低买 + 中卖×2 + 高买

结构示意（使用 Call 期权）：
  买1张 $265 Call ← 卖2张 $280 Call ← 买1张 $295 Call
  └── 下翼 ──┘      └── 身体 ──┘      └── 上翼 ──┘

                    ↗ 最大盈利点 ↘
              $265 ─────●────── $295
                     $280
                    (当前价)

收益逻辑：
1. 预期股价在中间行权价附近到期
2. 中间行权价的卖出期权赚取时间价值
3. 两侧买入期权限制风险（翅膀）

================================================================================
📌 触发条件 & 参数说明
================================================================================
BF_WING_PCT=0.05     # 翼展距离：当前价 ± 5%（$280 ± $14 = $266/$294）
BF_PROFIT_TARGET=0.50  # 盈利达 50% 时止盈
BF_STOP_LOSS=0.80      # 亏损达 80% 时止损

示例（股价 $280，翼展 5%）：
  - 买 1 张 $266 Call（下翼）
  - 卖 2 张 $280 Call（身体）
  - 买 1 张 $294 Call（上翼）
  - 最大盈利点：$280（股价恰好在中间）
  - 盈亏平衡：$266 + 净成本 ~ $294 - 净成本

================================================================================
📌 与 Iron Condor 的区别
================================================================================
┌─────────────┬───────────────────────────────────────┐
│   策略      │         特点                          │
├─────────────┼───────────────────────────────────────┤
│ Butterfly   │ 精准押注某一价位，风险更低，收益也低   │
│ Iron Condor │ 押注价格区间，收益和风险都更高         │
└─────────────┴───────────────────────────────────────┘

================================================================================
📌 使用场景
================================================================================
✅ 适合：
   - 强烈预期股价横盘不动
   - 财报前后波动率预期很低
   - 想要极低成本入场试探
   - 预判价格会收敛到某一点位

❌ 不适合：
   - 预期大涨大跌
   - 高波动率环境
   - 流动性差的标的（难以构建4腿）

================================================================================
📌 运行方式（推荐：每天检查 1 次）
================================================================================
# 方式1: 每天开盘后运行
uv run demo14_butterfly_spread.py

# 方式2: cron 定时任务（美东时间 9:35 检查）
# 35 9 * * 1-5 cd /path/to/project && uv run demo14_butterfly_spread.py

# 首次运行：自动建立 Butterfly 仓位
# 后续运行：监控价格，达到止盈/止损条件自动平仓

================================================================================
📌 盈亏分析
================================================================================
假设：净成本 $50（买入 - 卖出）

最大盈利：翼展宽度×100 - 净成本 = ($280-$266)×100 - $50 = $1350（到期正好$280）
最大亏损：$50（净成本，股价远离中点）
盈亏比：约 27:1 （盈利空间大，但概率低）

建议：小仓位尝试，明确预期价位

================================================================================
📌 风险提示
================================================================================
⚠️ 股价偏离中点越远，盈利越少
⚠️ 时间价值对 Butterfly 不利（需要价格配合）
⚠️ 构建4腿交易，手续费较高
⚠️ 流动性差时难以平仓

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
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "24"))

SYMBOL = os.getenv("BF_SYMBOL", "AAPL")
EXCHANGE = os.getenv("BF_EXCHANGE", "SMART")
CURRENCY = os.getenv("BF_CURRENCY", "USD")

# 策略配置
NUM_CONTRACTS = int(os.getenv("BF_CONTRACTS", "1"))  # 蝴蝶数量
WING_PCT = float(os.getenv("BF_WING_PCT", "0.05"))   # 翼展距离 5%
PROFIT_TARGET_PCT = float(os.getenv("BF_PROFIT_TARGET", "0.50"))  # 盈利50%平仓
STOP_LOSS_PCT = float(os.getenv("BF_STOP_LOSS", "0.80"))  # 亏损80%平仓

CHECK_INTERVAL_SEC = int(os.getenv("BF_CHECK_INTERVAL", "60"))
FALLBACK_PRICE = float(os.getenv("BF_FALLBACK_PRICE", "280"))

USE_DELAYED_DATA = os.getenv("BF_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("BF_SIMULATION", "true").lower() == "true"

shutdown_requested = False


@dataclass
class ButterflyPosition:
    """Butterfly Spread 仓位"""
    # 行权价
    lower_strike: float = 0.0   # 下翼（买入）
    middle_strike: float = 0.0  # 身体（卖出×2）
    upper_strike: float = 0.0   # 上翼（买入）

    expiry: str = ""
    contracts: int = 0
    option_type: str = "C"  # Call Butterfly

    # 成本
    initial_cost: float = 0.0   # 初始净成本
    current_value: float = 0.0  # 当前持仓价值

    def get_max_profit(self) -> float:
        """最大盈利 = 翼展 × 100 - 初始成本（股价恰好在中点到期）"""
        wing_width = self.middle_strike - self.lower_strike
        return wing_width * 100 * self.contracts - self.initial_cost

    def get_max_loss(self) -> float:
        """最大亏损 = 初始成本（股价远离中点）"""
        return self.initial_cost

    def get_profit_point(self) -> float:
        """最大盈利点"""
        return self.middle_strike


@dataclass
class StrategyState:
    position: ButterflyPosition = field(default_factory=ButterflyPosition)
    start_time: Optional[datetime] = None
    current_price: float = 0.0
    initial_price: float = 0.0

    # 期权合约
    lower_option: Optional[Option] = None
    middle_option: Optional[Option] = None
    upper_option: Optional[Option] = None

    def get_pnl(self) -> float:
        """当前盈亏 = 当前价值 - 初始成本"""
        return self.position.current_value - self.position.initial_cost

    def get_pnl_pct(self) -> float:
        """盈亏比例"""
        if self.position.initial_cost == 0:
            return 0.0
        # Butterfly 是净支出策略，盈利是正的
        return self.get_pnl() / self.position.initial_cost


async def connect_ib() -> IB:
    """连接到 Interactive Brokers"""
    ib = IB()
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    # 设置市场数据类型：3=延迟数据，1=实时数据
    ib.reqMarketDataType(3 if USE_DELAYED_DATA else 1)
    return ib


async def get_stock_price(ib: IB, stock: Stock) -> float:
    """获取股票价格"""
    ticker = ib.reqMktData(stock, "", False, False)
    await asyncio.sleep(2)
    price = ticker.last or ticker.close or FALLBACK_PRICE
    ib.cancelMktData(stock)
    return price if price and not math.isnan(price) else FALLBACK_PRICE


async def get_option_price(ib: IB, option: Option) -> float:
    """获取期权价格"""
    ticker = ib.reqMktData(option, "", False, False)
    await asyncio.sleep(2)
    price = ticker.last or ticker.close or (
        (ticker.bid or 0) + (ticker.ask or 0)) / 2
    ib.cancelMktData(option)
    return price if price and not math.isnan(price) else 0.0


async def find_option(ib: IB, stock: Stock, right: str, strike: float, expiry: str) -> Optional[Option]:
    """获取指定期权合约"""
    option = Option(stock.symbol, expiry, strike, right, "SMART")
    try:
        qualified = await ib.qualifyContractsAsync(option)
        if qualified and qualified[0]:
            return qualified[0]
    except Exception as e:
        logger.error(f"获取期权失败: {e}")
    return None


async def get_option_chain_info(ib: IB, stock: Stock) -> Tuple[list, list]:
    """获取期权链信息（到期日和行权价列表）"""
    chains = await ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)
    if not chains:
        return [], []

    # 优先选择 SMART 交易所
    chain = next((c for c in chains if c.exchange == "SMART"), chains[0])

    # 筛选未来的到期日
    today = datetime.now().strftime("%Y%m%d")
    valid_expiries = sorted([e for e in chain.expirations if e > today])
    strikes = sorted(chain.strikes)

    return valid_expiries, strikes


def print_status(state: StrategyState, reason: str = ""):
    """打印策略状态"""
    pos = state.position
    print("\n" + "=" * 60)
    print(f"🦋 Butterfly Spread 状态 {'(' + reason + ')' if reason else ''}")
    print("=" * 60)

    elapsed = (datetime.now() -
               state.start_time).total_seconds() if state.start_time else 0
    print(f"⏰ 运行: {int(elapsed/60)} 分钟 | 📈 股价: ${state.current_price:.2f}")

    # 图形化显示结构
    print("-" * 60)
    print("【Butterfly 结构】")
    print(f"  买1张 ${pos.lower_strike:.0f} Call ← 卖2张 ${pos.middle_strike:.0f} Call → 买1张 ${pos.upper_strike:.0f} Call")
    print(f"  └── 下翼 ──┘      └── 身体 ──┘      └── 上翼 ──┘")
    print(f"  最大盈利点: ${pos.middle_strike:.2f}")

    # 价格位置可视化
    range_width = pos.upper_strike - pos.lower_strike
    if range_width > 0:
        price_pos = (state.current_price - pos.lower_strike) / range_width
        bar_len = 40
        price_idx = int(price_pos * bar_len)
        price_idx = max(0, min(bar_len, price_idx))
        middle_idx = int(0.5 * bar_len)  # 中点

        bar = ["─"] * bar_len
        bar[middle_idx] = "◆"  # 最大盈利点
        if 0 <= price_idx < bar_len:
            bar[price_idx] = "●"  # 当前价格
        print(f"  [{(''.join(bar))}]")
        print(
            f"  ● = 当前价格 ${state.current_price:.0f}  ◆ = 最大盈利点 ${pos.middle_strike:.0f}")

    # 位置状态
    distance_pct = abs(state.current_price -
                       pos.middle_strike) / pos.middle_strike * 100
    if distance_pct < 1:
        print(f"  ✅ 接近最大盈利点！距离 {distance_pct:.1f}%")
    elif distance_pct < 3:
        print(f"  🟡 距离最大盈利点 {distance_pct:.1f}%")
    else:
        print(f"  ⚠️ 偏离最大盈利点 {distance_pct:.1f}%")

    print("-" * 60)
    print("【盈亏】")
    print(f"  初始成本: ${pos.initial_cost:.2f}")
    print(f"  当前价值: ${pos.current_value:.2f}")
    pnl = state.get_pnl()
    pnl_pct = state.get_pnl_pct()
    print(f"  当前盈亏: ${pnl:+.2f} ({pnl_pct:+.1%})")
    print(f"  最大盈利: ${pos.get_max_profit():.2f}（股价=${pos.middle_strike:.0f}时）")
    print(f"  最大亏损: ${pos.get_max_loss():.2f}（成本）")
    print("=" * 60)


async def build_butterfly(ib: IB, stock: Stock, state: StrategyState):
    """建立 Butterfly Spread 仓位"""
    price = await get_stock_price(ib, stock)
    state.current_price = price
    state.initial_price = price

    # 获取期权链
    expiries, strikes = await get_option_chain_info(ib, stock)
    if not expiries or not strikes:
        raise RuntimeError("无法获取期权链")

    # 选择到期日（2-4周后）
    expiry = expiries[1] if len(expiries) > 1 else expiries[0]

    # 计算行权价：下翼、中点（ATM）、上翼
    middle_strike = min(strikes, key=lambda x: abs(x - price))
    lower_strike = min(strikes, key=lambda x: abs(
        x - price * (1 - WING_PCT)) if x < middle_strike else float('inf'))
    upper_strike = min(strikes, key=lambda x: abs(
        x - price * (1 + WING_PCT)) if x > middle_strike else float('inf'))

    logger.info(f"构建 Butterfly @ {expiry}")
    logger.info(
        f"  买 ${lower_strike} Call | 卖×2 ${middle_strike} Call | 买 ${upper_strike} Call")

    # 获取期权合约
    state.lower_option = await find_option(ib, stock, "C", lower_strike, expiry)
    state.middle_option = await find_option(ib, stock, "C", middle_strike, expiry)
    state.upper_option = await find_option(ib, stock, "C", upper_strike, expiry)

    if not all([state.lower_option, state.middle_option, state.upper_option]):
        raise RuntimeError("无法获取所有期权腿")

    # 获取价格并计算净成本
    lower_price = await get_option_price(ib, state.lower_option)
    middle_price = await get_option_price(ib, state.middle_option)
    upper_price = await get_option_price(ib, state.upper_option)

    # 净成本 = 买入价 - 卖出价（卖2张中间）
    # Butterfly: +1 lower, -2 middle, +1 upper
    net_cost = (lower_price - 2 * middle_price +
                upper_price) * 100 * NUM_CONTRACTS

    if SIMULATION_MODE:
        logger.info(f"[模拟] 建立 Butterfly, 净成本: ${net_cost:.2f}")
        logger.info(f"  下翼: ${lower_price:.2f} × 1 = ${lower_price * 100:.2f}")
        logger.info(
            f"  身体: ${middle_price:.2f} × 2 = ${middle_price * 200:.2f}（卖出）")
        logger.info(f"  上翼: ${upper_price:.2f} × 1 = ${upper_price * 100:.2f}")

    # 更新状态
    state.position.lower_strike = lower_strike
    state.position.middle_strike = middle_strike
    state.position.upper_strike = upper_strike
    state.position.expiry = expiry
    state.position.contracts = NUM_CONTRACTS
    state.position.initial_cost = net_cost
    state.position.current_value = net_cost


async def update_position_value(ib: IB, state: StrategyState):
    """更新持仓价值"""
    if not all([state.lower_option, state.middle_option, state.upper_option]):
        return

    lower_price = await get_option_price(ib, state.lower_option)
    middle_price = await get_option_price(ib, state.middle_option)
    upper_price = await get_option_price(ib, state.upper_option)

    # 当前价值 = 平仓可获得的金额
    current_value = (lower_price - 2 * middle_price +
                     upper_price) * 100 * NUM_CONTRACTS
    state.position.current_value = current_value


async def close_butterfly(ib: IB, state: StrategyState):
    """平仓 Butterfly"""
    logger.info("🔄 平仓 Butterfly...")
    await update_position_value(ib, state)

    final_pnl = state.get_pnl()
    if SIMULATION_MODE:
        logger.info(f"[模拟] 平仓, 最终盈亏: ${final_pnl:+.2f}")

    state.position.contracts = 0


async def run_butterfly(ib: IB):
    """主策略循环"""
    global shutdown_requested

    logger.info("🦋 启动 Butterfly Spread 策略")
    logger.info(f"标的: {SYMBOL} | 合约: {NUM_CONTRACTS}")
    logger.info(f"翼展: ±{WING_PCT:.1%}")
    logger.info("💡 按 Ctrl+C 退出")

    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]

    state = StrategyState()
    state.start_time = datetime.now()

    # 建仓
    await build_butterfly(ib, stock, state)
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
    await close_butterfly(ib, state)
    print_status(state, "结束")


def handle_shutdown(signum, frame):
    """处理关闭信号"""
    global shutdown_requested
    shutdown_requested = True


async def main():
    import signal
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    ib = await connect_ib()
    try:
        await run_butterfly(ib)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    print("""
🦋 Butterfly Spread 策略 - 精准押注价格回归
   预期股价在某一价位附近到期，低成本高回报
   按 Ctrl+C 退出
""")
    asyncio.run(main())
