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

from ib_async import IB, Stock, Option, MarketOrder, LimitOrder, Contract, ComboLeg, TagValue

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

# 运行模式: daily = 单次检查, continuous = 持续监控, close_all = 一键平仓
# $env:IC_MODE="close_all"; uv run .\practice3\demo12_iron_condor_enhanced.py
RUN_MODE = os.getenv("IC_MODE", "daily")

USE_DELAYED_DATA = os.getenv("IC_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("IC_SIMULATION", "false").lower() == "true"

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
    """从文件加载仓位（用于获取建仓时的元数据）"""
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


async def load_position_from_ibkr(ib: IB, symbol: str) -> Optional[IronCondorPosition]:
    """
    从 IBKR 查询真实期权持仓，检测是否存在 Iron Condor
    
    Iron Condor 结构:
    - 1 张 long put (正数)
    - 1 张 short put (负数)
    - 1 张 short call (负数)
    - 1 张 long call (正数)
    """
    # 获取所有持仓
    positions = ib.positions()
    
    # 过滤出该标的的期权持仓
    option_positions = [
        p for p in positions 
        if p.contract.symbol == symbol and p.contract.secType == "OPT"
    ]
    
    if not option_positions:
        logger.info(f"未发现 {symbol} 期权持仓")
        return None
    
    # 解析持仓
    calls = []  # (strike, position, expiry)
    puts = []
    
    for p in option_positions:
        opt = p.contract
        strike = opt.strike
        expiry = opt.lastTradeDateOrContractMonth
        qty = p.position
        
        if opt.right == "C":
            calls.append((strike, qty, expiry))
        else:
            puts.append((strike, qty, expiry))
    
    # 检查是否符合 Iron Condor 结构
    # 需要: 2 个 call (1正1负), 2 个 put (1正1负)
    if len(calls) < 2 or len(puts) < 2:
        logger.info(f"持仓不符合 Iron Condor 结构: {len(calls)} calls, {len(puts)} puts")
        return None
    
    # 找出 short/long 腿
    short_calls = [(s, q, e) for s, q, e in calls if q < 0]
    long_calls = [(s, q, e) for s, q, e in calls if q > 0]
    short_puts = [(s, q, e) for s, q, e in puts if q < 0]
    long_puts = [(s, q, e) for s, q, e in puts if q > 0]
    
    if not (short_calls and long_calls and short_puts and long_puts):
        logger.info("持仓不完整，缺少 Iron Condor 部分腿")
        return None
    
    # 取第一组匹配的 Iron Condor
    short_call_strike = short_calls[0][0]
    long_call_strike = long_calls[0][0]
    short_put_strike = short_puts[0][0]
    long_put_strike = long_puts[0][0]
    expiry = short_calls[0][2]
    
    # 使用4条腿中最小数量作为完整的 Iron Condor 数量
    sc_qty = int(abs(short_calls[0][1]))
    lc_qty = int(abs(long_calls[0][1]))
    sp_qty = int(abs(short_puts[0][1]))
    lp_qty = int(abs(long_puts[0][1]))
    contracts = min(sc_qty, lc_qty, sp_qty, lp_qty)
    
    logger.info(f"✅ 检测到 Iron Condor 持仓:")
    logger.info(f"   买Put ${long_put_strike} | 卖Put ${short_put_strike} | 卖Call ${short_call_strike} | 买Call ${long_call_strike}")
    logger.info(f"   到期日: {expiry}, 合约数: {contracts} (各腿: LP={lp_qty}, SP={sp_qty}, SC={sc_qty}, LC={lc_qty})")
    
    # 尝试从本地文件获取建仓时的元数据
    local_position = load_position()
    initial_credit = local_position.initial_credit if local_position else 0.0
    entry_price = local_position.entry_price if local_position else 0.0
    entry_date = local_position.entry_date if local_position else ""
    
    return IronCondorPosition(
        short_call_strike=short_call_strike,
        short_put_strike=short_put_strike,
        long_call_strike=long_call_strike,
        long_put_strike=long_put_strike,
        expiry=expiry,
        contracts=contracts,
        initial_credit=initial_credit,
        current_value=0.0,  # 稍后更新
        entry_price=entry_price,
        entry_date=entry_date
    )


def save_position(position: IronCondorPosition):
    """保存仓位到文件（记录建仓时的元数据）"""
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


async def cancel_all_option_orders(ib: IB, symbol: str):
    """
    取消所有指定标的的期权挂单
    在每次操作前调用，避免"同一合约两边都有订单"的冲突
    """
    open_trades = ib.openTrades()
    cancelled_count = 0
    
    for trade in open_trades:
        contract = trade.contract
        # 只取消期权订单，且是指定标的
        if contract.secType == "OPT" and contract.symbol == symbol:
            status = trade.orderStatus.status
            if status in ["PendingSubmit", "PreSubmitted", "Submitted"]:
                ib.cancelOrder(trade.order)
                cancelled_count += 1
                logger.info(f"取消挂单: {contract.localSymbol} {trade.order.action} {trade.order.totalQuantity}")
    
    if cancelled_count > 0:
        await asyncio.sleep(2)  # 等待取消完成
        logger.info(f"✅ 已取消 {cancelled_count} 个挂单")
    else:
        logger.info("无挂单需要取消")


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
    # 处理 initial_credit 为 0 的情况
    credit_display = position.initial_credit
    credit_note = ""
    if position.initial_credit == 0 or abs(position.initial_credit) < 0.01:
        credit_display = position.current_value
        credit_note = " (⚠️ 估算值)"
    print(f"  初始权利金: ${credit_display:.2f}{credit_note}")
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

    # 获取期权合约
    sc = await find_option(ib, stock, "C", short_call, expiry)
    sp = await find_option(ib, stock, "P", short_put, expiry)
    lc = await find_option(ib, stock, "C", long_call, expiry)
    lp = await find_option(ib, stock, "P", long_put, expiry)

    if not all([sc, sp, lc, lp]):
        raise RuntimeError("无法获取所有期权腿")

    # 获取期权价格
    sc_price = await get_option_price(ib, sc)
    sp_price = await get_option_price(ib, sp)
    lc_price = await get_option_price(ib, lc)
    lp_price = await get_option_price(ib, lp)

    # 净权利金 (卖出 - 买入)
    net_credit_per_contract = sc_price + sp_price - lc_price - lp_price
    net_credit = net_credit_per_contract * 100 * NUM_CONTRACTS

    logger.info(f"  预计净权利金: ${net_credit:.2f} (每合约 ${net_credit_per_contract:.2f})")

    if SIMULATION_MODE:
        logger.info(f"[模拟] 建立 Iron Condor, 净收入: ${net_credit:.2f}")
    else:
        # ========== 真正下单逻辑：分开下4条腿 ==========
        logger.info("🚀 正在提交 Iron Condor (4条腿分开下单)...")
        
        # Iron Condor 4腿订单
        legs = [
            (lp, "BUY", "Long Put"),   # 买入 Long Put (保护)
            (sp, "SELL", "Short Put"),  # 卖出 Short Put (收权利金)
            (sc, "SELL", "Short Call"), # 卖出 Short Call (收权利金)
            (lc, "BUY", "Long Call"),   # 买入 Long Call (保护)
        ]
        
        filled_trades = []
        total_credit = 0.0
        
        for option, action, name in legs:
            # 使用市价单确保成交
            order = MarketOrder(action, NUM_CONTRACTS)
            trade = ib.placeOrder(option, order)
            
            logger.info(f"  {action} {name} @ 行权价 ${option.strike} x {NUM_CONTRACTS}")
            
            # 等待成交（最多 60 秒，大单需要更长时间）
            for i in range(60):
                await asyncio.sleep(1)
                status = trade.orderStatus.status
                filled = trade.orderStatus.filled
                if status == "Filled":
                    break
                elif i % 10 == 9:
                    logger.info(f"    等待中... 已成交 {filled}/{NUM_CONTRACTS}")
            
            if trade.orderStatus.status == "Filled":
                fill_price = trade.orderStatus.avgFillPrice
                # 卖出收权利金（正），买入付权利金（负）
                if action == "SELL":
                    total_credit += fill_price * 100 * NUM_CONTRACTS
                else:
                    total_credit -= fill_price * 100 * NUM_CONTRACTS
                logger.info(f"    ✅ 成交 @ ${fill_price:.2f}")
                filled_trades.append(trade)
            else:
                logger.error(f"    ❌ {name} 未完全成交: {trade.orderStatus.status}, 已成交: {trade.orderStatus.filled}/{NUM_CONTRACTS}")
                # 如果有腿未成交，需要处理（此处简化处理）
                raise RuntimeError(f"腿 {name} 未成交")
        
        net_credit = total_credit
        logger.info(f"✅ Iron Condor 全部成交! 净权利金: ${net_credit:.2f}")

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


async def close_iron_condor(ib: IB, stock: Stock, position: IronCondorPosition, close_qty: int) -> float:
    """
    減仓 Iron Condor（平掉部分仓位）
    
    关闭操作是开仓的反向：
    - 卖出 long put (之前买入的)
    - 买入 short put (之前卖出的)
    - 买入 short call (之前卖出的)
    - 卖出 long call (之前买入的)
    
    返回：平仓获得的净权利金（正=收入，负=支出）
    """
    logger.info(f"🔻 正在平仓 {close_qty} 张 Iron Condor...")
    
    # 获取期权合约
    sc = await find_option(ib, stock, "C", position.short_call_strike, position.expiry)
    sp = await find_option(ib, stock, "P", position.short_put_strike, position.expiry)
    lc = await find_option(ib, stock, "C", position.long_call_strike, position.expiry)
    lp = await find_option(ib, stock, "P", position.long_put_strike, position.expiry)
    
    if not all([sc, sp, lc, lp]):
        raise RuntimeError("无法获取所有期权腿")
    
    if SIMULATION_MODE:
        # 模拟模式
        sc_price = await get_option_price(ib, sc)
        sp_price = await get_option_price(ib, sp)
        lc_price = await get_option_price(ib, lc)
        lp_price = await get_option_price(ib, lp)
        
        # 平仓权利金 = 买入short - 卖出long
        close_debit = (sc_price + sp_price - lc_price - lp_price) * 100 * close_qty
        logger.info(f"[模拟] 平仓 {close_qty} 张, 支出: ${close_debit:.2f}")
        return -close_debit  # 返回负数表示支出
    else:
        # 真实模式：4条腿反向平仓
        legs = [
            (lp, "SELL", "Long Put"),   # 卖出 Long Put (平仓)
            (sp, "BUY", "Short Put"),   # 买入 Short Put (平仓)
            (sc, "BUY", "Short Call"),  # 买入 Short Call (平仓)
            (lc, "SELL", "Long Call"),  # 卖出 Long Call (平仓)
        ]
        
        total_debit = 0.0
        
        for option, action, name in legs:
            order = MarketOrder(action, close_qty)
            trade = ib.placeOrder(option, order)
            
            logger.info(f"  {action} {name} @ 行权价 ${option.strike} x {close_qty}")
            
            # 等待成交
            for i in range(60):
                await asyncio.sleep(1)
                if trade.orderStatus.status == "Filled":
                    break
                elif i % 10 == 9:
                    logger.info(f"    等待中... 已成交 {trade.orderStatus.filled}/{close_qty}")
            
            if trade.orderStatus.status == "Filled":
                fill_price = trade.orderStatus.avgFillPrice
                if action == "BUY":
                    total_debit += fill_price * 100 * close_qty  # 买入支出
                else:
                    total_debit -= fill_price * 100 * close_qty  # 卖出收入
                logger.info(f"    ✅ 成交 @ ${fill_price:.2f}")
            else:
                logger.error(f"    ❌ {name} 未成交: {trade.orderStatus.status}")
                raise RuntimeError(f"平仓腿 {name} 未成交")
        
        logger.info(f"✅ 减仓完成! 净支出: ${total_debit:.2f}")
        return -total_debit  # 返回负数表示支出


async def close_all_positions(ib: IB):
    """
    一键平仓所有 AAPL 期权持仓
    平掉所有腿，清除本地状态，重新开始
    """
    print("\n🔥 一键平仓模式")
    print("=" * 50)
    
    # 先取消所有挂单
    await cancel_all_option_orders(ib, SYMBOL)
    
    # 获取所有期权持仓
    positions = ib.positions()
    option_positions = [
        p for p in positions 
        if p.contract.secType == "OPT" and p.contract.symbol == SYMBOL
    ]
    
    if not option_positions:
        print("📭 没有期权持仓需要平仓")
        clear_position()
        return
    
    print(f"📋 发现 {len(option_positions)} 个期权持仓:")
    for p in option_positions:
        c = p.contract
        qty = p.position
        side = "多" if qty > 0 else "空"
        print(f"   {c.right} ${c.strike} @ {c.lastTradeDateOrContractMonth}: {side}{abs(qty):.0f}张")
    
    print("\n🔻 开始平仓...")
    
    total_pnl = 0.0
    
    for p in option_positions:
        contract = p.contract
        qty = int(abs(p.position))
        
        # 反向操作：多仓卖出平仓，空仓买入平仓
        action = "SELL" if p.position > 0 else "BUY"
        
        # 确保合约有完整信息
        contract.exchange = "SMART"
        qualified = await ib.qualifyContractsAsync(contract)
        if not qualified:
            logger.error(f"无法确认合约: {contract.localSymbol}")
            continue
        
        order = MarketOrder(action, qty)
        trade = ib.placeOrder(qualified[0], order)
        
        logger.info(f"  {action} {contract.right} ${contract.strike} x {qty}")
        
        # 等待成交
        for i in range(60):
            await asyncio.sleep(1)
            if trade.orderStatus.status == "Filled":
                break
            elif i % 10 == 9:
                logger.info(f"    等待中... 已成交 {trade.orderStatus.filled}/{qty}")
        
        if trade.orderStatus.status == "Filled":
            fill_price = trade.orderStatus.avgFillPrice
            # 卖出收入为正，买入支出为负
            if action == "SELL":
                pnl = fill_price * 100 * qty
            else:
                pnl = -fill_price * 100 * qty
            total_pnl += pnl
            logger.info(f"    ✅ 成交 @ ${fill_price:.2f}, 盈亏: ${pnl:+.2f}")
        else:
            logger.error(f"    ❌ 未成交: {trade.orderStatus.status}")
    
    print("\n" + "=" * 50)
    print(f"✅ 平仓完成! 总金额: ${total_pnl:+.2f}")
    
    # 清除本地状态文件
    clear_position()
    print("🗑️ 本地状态文件已清除")
    print("现在可以重新运行 daily 模式建立新仓位")


async def run_daily_check(ib: IB):
    """单次检查模式（每日运行）"""
    logger.info("📆 每日检查模式")

    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]

    # 先取消所有期权挂单，避免冲突
    await cancel_all_option_orders(ib, SYMBOL)

    current_price = await get_stock_price(ib, stock)

    # 修复：优先从本地状态文件识别仓位（避免多策略共用期权导致识别错误）
    # 1. 先加载本地保存的仓位
    local_position = load_position()
    
    if local_position:
        # 2. 验证 IBKR 中是否仍持有对应合约的4条腿
        positions = ib.positions()
        opts = [p for p in positions if p.contract.symbol == SYMBOL and p.contract.secType == "OPT"]
        
        # 检查本地记录的四个腿是否在 IBKR 中存在
        has_short_call = any(
            p.contract.strike == local_position.short_call_strike and 
            p.contract.lastTradeDateOrContractMonth == local_position.expiry and
            p.contract.right == "C" and p.position < 0
            for p in opts
        )
        has_long_call = any(
            p.contract.strike == local_position.long_call_strike and 
            p.contract.lastTradeDateOrContractMonth == local_position.expiry and
            p.contract.right == "C" and p.position > 0
            for p in opts
        )
        has_short_put = any(
            p.contract.strike == local_position.short_put_strike and 
            p.contract.lastTradeDateOrContractMonth == local_position.expiry and
            p.contract.right == "P" and p.position < 0
            for p in opts
        )
        has_long_put = any(
            p.contract.strike == local_position.long_put_strike and 
            p.contract.lastTradeDateOrContractMonth == local_position.expiry and
            p.contract.right == "P" and p.position > 0
            for p in opts
        )
        
        if has_short_call and has_long_call and has_short_put and has_long_put:
            logger.info(f"✅ 从本地状态确认 Iron Condor 仓位:")
            logger.info(f"   买Put ${local_position.long_put_strike} | 卖Put ${local_position.short_put_strike} | 卖Call ${local_position.short_call_strike} | 买Call ${local_position.long_call_strike}")
            logger.info(f"   到期日: {local_position.expiry}, 合约数: {local_position.contracts}")
            position = local_position
        else:
            logger.warning(f"⚠️ 本地记录的 Iron Condor 在 IBKR 中部分或全部不存在 (SC={has_short_call}, LC={has_long_call}, SP={has_short_put}, LP={has_long_put})，清除本地记录")
            clear_position()
            position = None
    else:
        # 3. 没有本地记录，尝试从 IBKR 自动检测
        position = await load_position_from_ibkr(ib, SYMBOL)

    if position is None:
        # 无仓位，建立新仓
        print(f"\n📭 无现有仓位，建立新 Iron Condor ({NUM_CONTRACTS} 张)...")
        position = await build_iron_condor(ib, stock, current_price)
        save_position(position)
        print(f"✅ 已建立 Iron Condor 仓位")
        print(
            f"   盈利区间: ${position.short_put_strike:.0f} ~ ${position.short_call_strike:.0f}")
        print(f"   到期日: {position.expiry}")
        print(f"   初始权利金: ${position.initial_credit:.2f}")
    else:
        current_contracts = position.contracts
        
        # 从本地文件尝试补充权利金信息（如果 IBKR 查询的没有）
        if position.initial_credit == 0:
            local_pos = load_position()
            if local_pos and local_pos.initial_credit > 0:
                position.initial_credit = local_pos.initial_credit
                position.entry_price = local_pos.entry_price
                position.entry_date = local_pos.entry_date
                logger.info(f"从本地文件恢复权利金信息: ${position.initial_credit:.2f}")
        
        if current_contracts < NUM_CONTRACTS:
            # ========== 加仓逻辑 ==========
            add_contracts = NUM_CONTRACTS - current_contracts
            print(f"\n📈 检测到现有 {current_contracts} 张，需要加仓 {add_contracts} 张到 {NUM_CONTRACTS} 张...")
            
            original_contracts = NUM_CONTRACTS
            globals()['NUM_CONTRACTS'] = add_contracts
            
            try:
                add_position = await build_iron_condor(ib, stock, current_price)
                # 更新总持仓信息
                position.contracts = original_contracts
                position.initial_credit += add_position.initial_credit
                save_position(position)
                print(f"✅ 加仓成功！现在共 {position.contracts} 张")
                print(f"   总初始权利金: ${position.initial_credit:.2f}")
            finally:
                globals()['NUM_CONTRACTS'] = original_contracts
                
        elif current_contracts > NUM_CONTRACTS:
            # ========== 减仓逻辑 ==========
            close_contracts = current_contracts - NUM_CONTRACTS
            print(f"\n📉 检测到现有 {current_contracts} 张，需要减仓 {close_contracts} 张到 {NUM_CONTRACTS} 张...")
            
            try:
                close_pnl = await close_iron_condor(ib, stock, position, close_contracts)
                # 更新持仓信息
                position.contracts = NUM_CONTRACTS
                # 按比例减少初始权利金
                credit_per_contract = position.initial_credit / current_contracts if current_contracts > 0 else 0
                position.initial_credit -= credit_per_contract * close_contracts
                # 减仓的盈亏 = 平仓获得的权利金
                save_position(position)
                print(f"✅ 减仓成功！现在共 {position.contracts} 张")
                print(f"   平仓盈亏: ${close_pnl:.2f}")
                print(f"   剩余初始权利金: ${position.initial_credit:.2f}")
            except Exception as e:
                logger.error(f"减仓失败: {e}")
                print(f"❌ 减仓失败: {e}")
                
        else:
            # ========== 持仓数量正好，检查并更新 ==========
            position.current_value = await update_position_value(ib, stock, position)
            
            # 修复：如果 initial_credit 为 0，使用当前价值作为成本基础并保存
            if position.initial_credit == 0 or abs(position.initial_credit) < 0.01:
                position.initial_credit = position.current_value
                position.entry_date = position.entry_date or datetime.now().strftime("%Y-%m-%d")
                logger.warning(f"⚠️ 缺失 initial_credit，使用当前市场价值 ${position.current_value:.2f} 作为成本基础")
                save_position(position)
            
            pnl = position.initial_credit - position.current_value
            pnl_pct = pnl / position.initial_credit if position.initial_credit != 0 else 0

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
                    print("   （真实模式下需要手动确认）")


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
        elif RUN_MODE == "close_all":
            await close_all_positions(ib)
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
