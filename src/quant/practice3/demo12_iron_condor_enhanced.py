"""
Demo 12 Enhanced: Iron Condor Strategy with Daily Run Mode
(铁鹰策略 - 增强版：支持每日定时运行和智能调仓)

================================================================================
📌 新增功能
================================================================================
1. 单次运行模式 - 检查一次就退出，适合 cron 定时任务
2. 仓位持久化 - 保存/加载仓位状态，重启后恢复
3. 智能调仓决策 - 根据市场条件建议调仓动作
4. 展期功能 - 期权到期前自动展期

================================================================================
📌 运行模式
================================================================================
# 模式1: 单次检查（推荐用于 cron）
IC_MODE=daily uv run demo12_iron_condor_enhanced.py

# 模式2: 持续监控（用于手动观察）
IC_MODE=continuous uv run demo12_iron_condor_enhanced.py

# cron 示例（美东时间 9:35 和 15:30）
# 35 9,15 * * 1-5 cd /path && IC_MODE=daily uv run demo12_iron_condor_enhanced.py

================================================================================
📌 调仓逻辑
================================================================================
1. 止盈 (50%): 盈利达目标自动平仓
2. 止损 (100%): 亏损达阈值自动平仓
3. 展期: 到期前 5 天建议展期
4. 上移/下移: 价格接近触及点时调整
5. 到期处理: 平仓或展期

================================================================================
📌 调仓示意图
================================================================================
                 展期                    展期
                  ↓                       ↓
   下移 ⬅️ [卖Put] ←── 安全区间 ──→ [卖Call] ➡️ 上移
         $266              $280           $294
                  ↑                       ↑
              危险区域                危险区域
           (价格接近)              (价格接近)

================================================================================
"""
import asyncio
import os
import math
import json
import logging
from datetime import datetime
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass, field, asdict

from ib_async import IB, Stock, Option, MarketOrder

# 导入仓位管理模块
try:
    from position_manager import (
        PositionManager, AdjustmentAction,
        check_iron_condor_adjustment, format_adjustment_report
    )
except ImportError:
    # 如果模块不存在，提供基本实现
    PositionManager = None

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
NUM_CONTRACTS = int(os.getenv("IC_CONTRACTS", "1"))
SHORT_OTM_PCT = float(os.getenv("IC_SHORT_OTM", "0.05"))
LONG_OTM_PCT = float(os.getenv("IC_LONG_OTM", "0.10"))
PROFIT_TARGET_PCT = float(os.getenv("IC_PROFIT_TARGET", "0.50"))
STOP_LOSS_PCT = float(os.getenv("IC_STOP_LOSS", "1.0"))
ROLL_DAYS = int(os.getenv("IC_ROLL_DAYS", "5"))  # 展期天数

CHECK_INTERVAL_SEC = int(os.getenv("IC_CHECK_INTERVAL", "60"))
FALLBACK_PRICE = float(os.getenv("IC_FALLBACK_PRICE", "280"))

# 运行模式: daily = 单次检查, continuous = 持续监控
RUN_MODE = os.getenv("IC_MODE", "daily")

USE_DELAYED_DATA = os.getenv("IC_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("IC_SIMULATION", "true").lower() == "true"

# 状态文件
STATE_DIR = os.path.join(os.path.dirname(__file__), ".states")
STATE_FILE = os.path.join(STATE_DIR, f"iron_condor_{SYMBOL.lower()}.json")


@dataclass
class IronCondorPosition:
    """Iron Condor 仓位"""
    short_call_strike: float = 0.0
    short_put_strike: float = 0.0
    long_call_strike: float = 0.0
    long_put_strike: float = 0.0
    expiry: str = ""
    contracts: int = 0
    initial_credit: float = 0.0
    current_value: float = 0.0
    entry_price: float = 0.0  # 建仓时股价
    entry_date: str = ""      # 建仓日期

    def get_max_profit(self) -> float:
        return self.initial_credit

    def get_max_loss(self) -> float:
        call_wing = self.long_call_strike - self.short_call_strike
        return call_wing * 100 * self.contracts - self.initial_credit

    def get_profit_range(self) -> Tuple[float, float]:
        return (self.short_put_strike, self.short_call_strike)

    def get_days_to_expiry(self) -> int:
        if not self.expiry:
            return 999
        try:
            expiry_date = datetime.strptime(self.expiry, "%Y%m%d")
            return (expiry_date.date() - datetime.now().date()).days
        except:
            return 999

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'IronCondorPosition':
        return cls(**data)


def load_position() -> Optional[IronCondorPosition]:
    """从文件加载仓位"""
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, 'r') as f:
            data = json.load(f)
            return IronCondorPosition.from_dict(data['position'])
    except Exception as e:
        logger.error(f"加载仓位失败: {e}")
        return None


def save_position(position: IronCondorPosition):
    """保存仓位到文件"""
    os.makedirs(STATE_DIR, exist_ok=True)
    data = {
        'position': position.to_dict(),
        'last_updated': datetime.now().isoformat(),
        'symbol': SYMBOL
    }
    with open(STATE_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    logger.info(f"仓位已保存: {STATE_FILE}")


def clear_position():
    """清除仓位"""
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
        logger.info("仓位已清除")


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


def decide_adjustment(
    position: IronCondorPosition,
    current_price: float,
    pnl_pct: float
) -> Tuple[str, str]:
    """
    决定调仓动作

    返回: (action, reason)
        action: hold/take_profit/stop_loss/roll_out/roll_up/roll_down/close
        reason: 原因说明
    """
    days_to_expiry = position.get_days_to_expiry()

    # 1. 止盈检查
    if pnl_pct >= PROFIT_TARGET_PCT:
        return ("take_profit", f"盈利达目标 {pnl_pct:.1%}")

    # 2. 止损检查
    if pnl_pct <= -STOP_LOSS_PCT:
        return ("stop_loss", f"亏损达阈值 {pnl_pct:.1%}")

    # 3. 到期检查
    if days_to_expiry <= 0:
        return ("close", "期权已到期")

    # 4. 展期检查
    if days_to_expiry <= ROLL_DAYS:
        if pnl_pct > 0.3:
            return ("take_profit", f"即将到期且盈利 {pnl_pct:.1%}")
        return ("roll_out", f"距到期 {days_to_expiry} 天，建议展期")

    # 5. 价格危险区域检查
    profit_range = position.get_profit_range()
    put_danger = profit_range[0] * 1.02   # 接近 Put 行权价
    call_danger = profit_range[1] * 0.98  # 接近 Call 行权价

    if current_price <= put_danger:
        return ("roll_down", f"价格 ${current_price:.2f} 接近 Put ${profit_range[0]:.0f}")

    if current_price >= call_danger:
        return ("roll_up", f"价格 ${current_price:.2f} 接近 Call ${profit_range[1]:.0f}")

    return ("hold", "持仓正常")


def print_daily_report(
    position: IronCondorPosition,
    current_price: float,
    pnl: float,
    pnl_pct: float,
    action: str,
    reason: str
):
    """打印每日报告"""
    days = position.get_days_to_expiry()
    profit_range = position.get_profit_range()

    print("\n" + "=" * 60)
    print(f"📋 Iron Condor 每日报告 - {SYMBOL}")
    print(f"⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    print(f"\n【仓位信息】")
    print(f"  建仓日期: {position.entry_date}")
    print(f"  到期日期: {position.expiry} ({days} 天后)")
    print(f"  建仓价格: ${position.entry_price:.2f}")
    print(f"  当前价格: ${current_price:.2f}")

    print(f"\n【结构】")
    print(f"  买Put ${position.long_put_strike:.0f} ← 卖Put ${position.short_put_strike:.0f}"
          f" ←← ${current_price:.0f} →→ "
          f"卖Call ${position.short_call_strike:.0f} → 买Call ${position.long_call_strike:.0f}")
    print(f"  盈利区间: ${profit_range[0]:.0f} ~ ${profit_range[1]:.0f}")

    # 位置可视化
    range_width = position.long_call_strike - position.long_put_strike
    price_pos = (current_price - position.long_put_strike) / range_width
    bar_len = 50
    price_idx = max(0, min(bar_len - 1, int(price_pos * bar_len)))

    put_idx = int((position.short_put_strike -
                  position.long_put_strike) / range_width * bar_len)
    call_idx = int((position.short_call_strike -
                   position.long_put_strike) / range_width * bar_len)

    bar = ["─"] * bar_len
    if 0 <= put_idx < bar_len:
        bar[put_idx] = "P"
    if 0 <= call_idx < bar_len:
        bar[call_idx] = "C"
    bar[price_idx] = "●"

    print(f"\n  [{''.join(bar)}]")
    print(f"  P=卖Put行权价  C=卖Call行权价  ●=当前价格")

    if profit_range[0] <= current_price <= profit_range[1]:
        print(f"  ✅ 价格在盈利区间内")
    else:
        print(f"  ⚠️ 价格超出盈利区间！")

    print(f"\n【盈亏】")
    print(f"  初始权利金: ${position.initial_credit:.2f}")
    print(f"  当前价值: ${position.current_value:.2f}")
    print(f"  盈亏: ${pnl:+.2f} ({pnl_pct:+.1%})")
    print(f"  最大盈利: ${position.get_max_profit():.2f}")
    print(f"  最大亏损: ${position.get_max_loss():.2f}")

    print(f"\n【调仓建议】")
    action_icons = {
        "hold": "✅",
        "take_profit": "💰",
        "stop_loss": "🛑",
        "roll_out": "📅",
        "roll_up": "⬆️",
        "roll_down": "⬇️",
        "close": "🔒"
    }
    print(f"  {action_icons.get(action, '❓')} 建议: {action.upper()}")
    print(f"  📝 原因: {reason}")

    if action == "roll_out":
        print(f"\n  💡 展期操作:")
        print(f"     1. 平仓当前4腿")
        print(f"     2. 以当前价格为中心重新建仓")
        print(f"     3. 选择下一个到期周期")
    elif action == "roll_up":
        print(f"\n  💡 上移操作:")
        print(f"     1. 平仓当前 Call Spread")
        print(f"     2. 以更高行权价重新卖出 Call Spread")
    elif action == "roll_down":
        print(f"\n  💡 下移操作:")
        print(f"     1. 平仓当前 Put Spread")
        print(f"     2. 以更低行权价重新卖出 Put Spread")

    print("=" * 60)


async def build_iron_condor(ib: IB, stock: Stock, price: float) -> IronCondorPosition:
    """建立新的 Iron Condor 仓位"""
    expiries, strikes = await get_option_chain_info(ib, stock)
    if not expiries or not strikes:
        raise RuntimeError("无法获取期权链")

    expiry = expiries[1] if len(expiries) > 1 else expiries[0]

    # 计算行权价
    short_call = min(strikes, key=lambda x: abs(
        x - price * (1 + SHORT_OTM_PCT)) if x > price else float('inf'))
    short_put = min(strikes, key=lambda x: abs(
        x - price * (1 - SHORT_OTM_PCT)) if x < price else float('inf'))
    long_call = min(strikes, key=lambda x: abs(
        x - price * (1 + LONG_OTM_PCT)) if x > short_call else float('inf'))
    long_put = min(strikes, key=lambda x: abs(
        x - price * (1 - LONG_OTM_PCT)) if x < short_put else float('inf'))

    logger.info(f"构建 Iron Condor @ {expiry}")
    logger.info(
        f"  买Put ${long_put} | 卖Put ${short_put} | 卖Call ${short_call} | 买Call ${long_call}")

    # 获取期权价格
    sc = await find_option(ib, stock, "C", short_call, expiry)
    sp = await find_option(ib, stock, "P", short_put, expiry)
    lc = await find_option(ib, stock, "C", long_call, expiry)
    lp = await find_option(ib, stock, "P", long_put, expiry)

    if not all([sc, sp, lc, lp]):
        raise RuntimeError("无法获取所有期权腿")

    sc_price = await get_option_price(ib, sc)
    sp_price = await get_option_price(ib, sp)
    lc_price = await get_option_price(ib, lc)
    lp_price = await get_option_price(ib, lp)

    net_credit = (sc_price + sp_price - lc_price -
                  lp_price) * 100 * NUM_CONTRACTS

    if SIMULATION_MODE:
        logger.info(f"[模拟] 建立 Iron Condor, 净收入: ${net_credit:.2f}")

    return IronCondorPosition(
        short_call_strike=short_call,
        short_put_strike=short_put,
        long_call_strike=long_call,
        long_put_strike=long_put,
        expiry=expiry,
        contracts=NUM_CONTRACTS,
        initial_credit=net_credit,
        current_value=net_credit,
        entry_price=price,
        entry_date=datetime.now().strftime("%Y-%m-%d")
    )


async def update_position_value(ib: IB, stock: Stock, position: IronCondorPosition) -> float:
    """更新持仓价值，返回当前价值"""
    sc = await find_option(ib, stock, "C", position.short_call_strike, position.expiry)
    sp = await find_option(ib, stock, "P", position.short_put_strike, position.expiry)
    lc = await find_option(ib, stock, "C", position.long_call_strike, position.expiry)
    lp = await find_option(ib, stock, "P", position.long_put_strike, position.expiry)

    if not all([sc, sp, lc, lp]):
        return position.current_value

    sc_price = await get_option_price(ib, sc)
    sp_price = await get_option_price(ib, sp)
    lc_price = await get_option_price(ib, lc)
    lp_price = await get_option_price(ib, lp)

    current_value = (sc_price + sp_price - lc_price -
                     lp_price) * 100 * NUM_CONTRACTS
    return current_value


async def execute_action(ib: IB, stock: Stock, position: IronCondorPosition, action: str, current_price: float):
    """执行调仓动作"""
    if action in ["take_profit", "stop_loss", "close"]:
        if SIMULATION_MODE:
            pnl = position.initial_credit - position.current_value
            logger.info(f"[模拟] 平仓 Iron Condor, 盈亏: ${pnl:+.2f}")
        clear_position()
        print("✅ 仓位已平仓")

    elif action == "roll_out":
        # 平仓后重新建仓
        if SIMULATION_MODE:
            logger.info("[模拟] 展期: 平仓现有仓位并重新建仓")
        new_position = await build_iron_condor(ib, stock, current_price)
        save_position(new_position)
        print("✅ 已展期到新周期")

    elif action in ["roll_up", "roll_down"]:
        # 实际调仓逻辑（简化处理：记录建议，不自动执行）
        logger.info(f"建议 {action}，请手动执行或确认")
        # 可以在这里添加自动调仓逻辑


async def run_daily_check(ib: IB):
    """单次检查模式（每日运行）"""
    logger.info("📆 每日检查模式")

    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]

    current_price = await get_stock_price(ib, stock)

    # 加载现有仓位
    position = load_position()

    if position is None:
        # 无仓位，建立新仓
        print("\n📭 无现有仓位，建立新 Iron Condor...")
        position = await build_iron_condor(ib, stock, current_price)
        save_position(position)
        print(f"✅ 已建立 Iron Condor 仓位")
        print(
            f"   盈利区间: ${position.short_put_strike:.0f} ~ ${position.short_call_strike:.0f}")
        print(f"   到期日: {position.expiry}")
        print(f"   初始权利金: ${position.initial_credit:.2f}")
    else:
        # 有仓位，检查并更新
        position.current_value = await update_position_value(ib, stock, position)
        pnl = position.initial_credit - position.current_value
        pnl_pct = pnl / position.initial_credit if position.initial_credit else 0

        # 决定调仓动作
        action, reason = decide_adjustment(position, current_price, pnl_pct)

        # 打印报告
        print_daily_report(position, current_price,
                           pnl, pnl_pct, action, reason)

        # 执行动作（如果需要）
        if action != "hold":
            print(f"\n🔄 是否执行建议动作 '{action}'?")
            if SIMULATION_MODE:
                await execute_action(ib, stock, position, action, current_price)
            else:
                print("   设置 IC_SIMULATION=false 并手动确认执行")


async def run_continuous(ib: IB):
    """持续监控模式"""
    logger.info("🔄 持续监控模式")
    # 原有的持续运行逻辑...
    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]

    position = load_position()
    if position is None:
        price = await get_stock_price(ib, stock)
        position = await build_iron_condor(ib, stock, price)
        save_position(position)

    check_count = 0
    try:
        while True:
            await asyncio.sleep(CHECK_INTERVAL_SEC)
            check_count += 1

            current_price = await get_stock_price(ib, stock)
            position.current_value = await update_position_value(ib, stock, position)

            pnl = position.initial_credit - position.current_value
            pnl_pct = pnl / position.initial_credit if position.initial_credit else 0

            logger.info(
                f"检查 #{check_count} | 价格: ${current_price:.2f} | P&L: {pnl_pct:+.1%}")

            action, reason = decide_adjustment(
                position, current_price, pnl_pct)
            if action != "hold":
                print(f"\n⚠️ 触发调仓: {action} - {reason}")
                if action in ["take_profit", "stop_loss"]:
                    await execute_action(ib, stock, position, action, current_price)
                    break

    except KeyboardInterrupt:
        print("\n👋 用户中断")


async def main():
    import signal

    ib = await connect_ib()
    try:
        if RUN_MODE == "daily":
            await run_daily_check(ib)
        else:
            await run_continuous(ib)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    print(f"""
🎯 Iron Condor 增强版
   运行模式: {RUN_MODE}
   标的: {SYMBOL}
   模拟: {SIMULATION_MODE}
""")
    asyncio.run(main())
