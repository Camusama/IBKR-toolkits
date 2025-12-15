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
📌 运行方式
================================================================================
# 模式1: 单次检查（推荐用於 cron）
BF_MODE=daily uv run demo14_butterfly_spread.py

# 模式2: 持续监控
BF_MODE=continuous uv run demo14_butterfly_spread.py

================================================================================
"""
import asyncio
import os
import math
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field, asdict

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
RUN_MODE = os.getenv("BF_MODE", "daily")

USE_DELAYED_DATA = os.getenv("BF_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("BF_SIMULATION", "false").lower() == "true"  # Default false for live

# 状态文件
STATE_DIR = os.path.join(os.path.dirname(__file__), ".states")
STATE_FILE = os.path.join(STATE_DIR, f"butterfly_{SYMBOL.lower()}.json")


@dataclass
class ButterflyPosition:
    """Butterfly Spread 仓位"""
    symbol: str
    lower_strike: float = 0.0   # 下翼（买入）
    middle_strike: float = 0.0  # 身体（卖出×2）
    upper_strike: float = 0.0   # 上翼（买入）
    expiry: str = ""
    contracts: int = 0
    option_type: str = "C"  # Call Butterfly
    initial_cost: float = 0.0   # 初始净成本
    current_value: float = 0.0  # 当前持仓价值
    entry_date: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'ButterflyPosition':
        return cls(**data)

    def get_max_profit(self) -> float:
        """最大盈利 = 翼展 × 100 - 初始成本（股价恰好在中点到期）"""
        wing_width = self.middle_strike - self.lower_strike
        return wing_width * 100 * self.contracts - self.initial_cost

    def get_max_loss(self) -> float:
        """最大亏损 = 初始成本（股价远离中点）"""
        return self.initial_cost


@dataclass
class StrategyState:
    position: Optional[ButterflyPosition] = None
    current_price: float = 0.0


def load_local_position() -> Optional[ButterflyPosition]:
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, 'r') as f:
            data = json.load(f)
            return ButterflyPosition.from_dict(data['position'])
    except Exception as e:
        logger.error(f"加载仓位失败: {e}")
        return None


def save_position(position: ButterflyPosition):
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
    """获取期权价格"""
    ticker = ib.reqMktData(option, "", False, False)
    await asyncio.sleep(2)
    price = ticker.last or ticker.close or ((ticker.bid or 0) + (ticker.ask or 0)) / 2
    ib.cancelMktData(option)
    return price if price and not math.isnan(price) else 0.0


async def cancel_all_option_orders(ib: IB, symbol: str):
    open_trades = ib.openTrades()
    count = 0
    for trade in open_trades:
        c = trade.contract
        if c.secType == "OPT" and c.symbol == symbol:
            if trade.orderStatus.status in ["PendingSubmit", "PreSubmitted", "Submitted"]:
                ib.cancelOrder(trade.order)
                count += 1
    if count:
        await asyncio.sleep(2)
        logger.info(f"✅ 已取消 {count} 个挂单")


async def load_position_from_ibkr(ib: IB, symbol: str) -> Optional[ButterflyPosition]:
    """从 IBKR 识别 Butterfly 持仓"""
    positions = ib.positions()
    opts = [p for p in positions if p.contract.symbol == symbol and p.contract.secType == "OPT"]
    
    if not opts:
        return None
    
    # 按照 Expiry 分组
    from collections import defaultdict
    by_expiry = defaultdict(list)
    for p in opts:
        by_expiry[p.contract.lastTradeDateOrContractMonth].append(p)
        
    for expiry, group in by_expiry.items():
        # 需要至少3个腿
        if len(group) < 3:
            continue
            
        calls = [p for p in group if p.contract.right == 'C']
        
        # 简化识别：Long Call (Low) + Short Call (Mid) + Long Call (High)
        # Quantity ratio: 1 : -2 : 1
        # Sort by strike
        calls.sort(key=lambda p: p.contract.strike)
        
        if len(calls) >= 3:
            # 滑动窗口检测
            for i in range(len(calls) - 2):
                low_leg = calls[i]
                mid_leg = calls[i+1]
                high_leg = calls[i+2]
                
                # 检查 Strike 等距
                if not math.isclose(mid_leg.contract.strike - low_leg.contract.strike, 
                                    high_leg.contract.strike - mid_leg.contract.strike, abs_tol=0.1):
                    continue
                    
                # 检查方向和比例
                # 典型蝶式: 1 Long, -2 Short, 1 Long
                qty_low = low_leg.position
                qty_mid = mid_leg.position
                qty_high = high_leg.position
                
                # 检查是否为标准比例 1:-2:1
                if qty_low > 0 and qty_high > 0 and qty_mid < 0:
                    ratio_ok = (qty_low == abs(qty_mid)/2) and (qty_high == abs(qty_mid)/2)
                    # 或者简单持仓检查
                    if ratio_ok:
                        logger.info(f"✅ 检测到 Butterfly: {expiry} Call {low_leg.contract.strike}/{mid_leg.contract.strike}/{high_leg.contract.strike}")
                        
                        local = load_local_position()
                        cost = local.initial_cost if local else 0.0
                        date = local.entry_date if local else ""
                        
                        return ButterflyPosition(
                            symbol=symbol,
                            lower_strike=low_leg.contract.strike,
                            middle_strike=mid_leg.contract.strike,
                            upper_strike=high_leg.contract.strike,
                            expiry=expiry,
                            contracts=int(qty_low),
                            initial_cost=cost,
                            entry_date=date
                        )
    return None


async def open_butterfly(ib: IB, stock: Stock, price: float) -> Optional[ButterflyPosition]:
    """建立 Butterfly Spread 仓位"""
    logger.info("📦 正在开仓 Butterfly...")
    
    chains = await ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)
    if not chains:
        logger.error("无法获取期权链")
        return None
        
    chain = next((c for c in chains if c.exchange == "SMART"), chains[0])
    
    # 获取有效到期日
    import datetime as dt
    target_date = (datetime.now() + dt.timedelta(days=14)).strftime("%Y%m%d") # 2周后
    valid_exps = sorted([e for e in chain.expirations if e > target_date])
    if not valid_exps:
        valid_exps = sorted([e for e in chain.expirations if e > datetime.now().strftime("%Y%m%d")])
        
    if not valid_exps:
        logger.error("无可用到期日")
        return None
        
    expiry = valid_exps[0]
    
    # 获取 Contract Details 以确保 Strike 存在
    temp = Option(stock.symbol, expiry, exchange="SMART")
    try:
        details = await ib.reqContractDetailsAsync(temp)
    except Exception as e:
        logger.error(f"无法获取合约详情: {e}")
        return None
        
    if not details:
        return None
        
    # 只看 Call
    valid_calls = sorted([d.contract for d in details if d.contract.right == 'C'], key=lambda c: c.strike)
    if not valid_calls:
        return None
        
    # 找 ATM Strike 作为 Body
    mid_idx = -1
    min_diff = float('inf')
    for i, c in enumerate(valid_calls):
        diff = abs(c.strike - price)
        if diff < min_diff:
            min_diff = diff
            mid_idx = i
            
    if mid_idx == -1:
        return None
        
    # 寻找 Wings
    # WING_PCT e.g. 0.05 => Strike +/- 5%
    wing_dist_req = price * WING_PCT
    
    # 向上/下搜寻最接近 wing_dist 的 strike
    mid_strike = valid_calls[mid_idx].strike
    
    low_idx = -1
    min_dist_low = float('inf')
    
    high_idx = -1
    min_dist_high = float('inf')
    
    # 向下找 Lower Inner Wing (Standard Butterfly uses equidistant wings)
    # 实际上我们只要找两个 equidistant 的点即可.
    # 简单起见，遍历所有组合
    # 但为了效率，我们从 mid 向两边找
    
    # 更好的方法：确定 Lower, 则 Upper = Mid + (Mid - Lower)
    # 遍历可能的 Lower
    best_combo = None
    min_cost_diff = float('inf') # 这里不是指价格成本，而是指“偏离理想Wing宽度的程度”
    
    for i in range(mid_idx - 1, -1, -1):
        lower_c = valid_calls[i]
        width = mid_strike - lower_c.strike
        target_upper = mid_strike + width
        
        # 检查是否存在 Upper
        upper_c = next((c for c in valid_calls if abs(c.strike - target_upper) < 0.01), None)
        
        if upper_c:
            # 找到一个组合
            # 检查宽度是否接近理想值
            diff_metric = abs(width - wing_dist_req)
            if diff_metric < min_cost_diff:
                min_cost_diff = diff_metric
                best_combo = (lower_c, valid_calls[mid_idx], upper_c)
                
    if not best_combo:
         logger.error("无法找到合适的 Butterfly 组合 (等距Strike)")
         return None
         
    low_opt, mid_opt, high_opt = best_combo
    
    # Qualify (already from details, usually qualified, but good to be safe for order)
    # details contracts are usually fully defined but let's just use them
    
    # Get Prices
    lp = await get_option_price(ib, low_opt)
    mp = await get_option_price(ib, mid_opt)
    hp = await get_option_price(ib, high_opt)
    
    net_cost = (lp - 2*mp + hp) * 100 * NUM_CONTRACTS
    
    if SIMULATION_MODE:
        logger.info(f"[模拟] Butterfly: +1 {low_opt.strike}, -2 {mid_opt.strike}, +1 {high_opt.strike}, Cost: ${net_cost:.2f}")
    else:
        # Place orders
        # Leg 1: Buy Low
        # Leg 2: Sell 2 Mid
        # Leg 3: Buy High
        
        o1 = MarketOrder("BUY", NUM_CONTRACTS)
        o2 = MarketOrder("SELL", 2 * NUM_CONTRACTS)
        o3 = MarketOrder("BUY", NUM_CONTRACTS)
        
        t1 = ib.placeOrder(low_opt, o1)
        t2 = ib.placeOrder(mid_opt, o2)
        t3 = ib.placeOrder(high_opt, o3)
        
        MAX_WAIT = 15
        for _ in range(MAX_WAIT):
            if t1.isDone() and t2.isDone() and t3.isDone():
                break
            await asyncio.sleep(1)
            
        logger.info("✅ 订单提交完成")
        
    return ButterflyPosition(
        symbol=SYMBOL,
        lower_strike=low_opt.strike,
        middle_strike=mid_opt.strike,
        upper_strike=high_opt.strike,
        expiry=expiry,
        contracts=NUM_CONTRACTS,
        initial_cost=net_cost,
        current_value=net_cost,
        entry_date=datetime.now().strftime("%Y-%m-%d")
    )


async def close_butterfly(ib: IB, position: ButterflyPosition, reason: str):
    logger.info(f"🔄 平仓 Butterfly ({reason})...")
    
    if SIMULATION_MODE:
        logger.info("[模拟] 平仓完成")
        clear_position()
        return
        
    # Reconstruct contracts
    low_opt = Option(position.symbol, position.expiry, position.lower_strike, "C", "SMART")
    mid_opt = Option(position.symbol, position.expiry, position.middle_strike, "C", "SMART")
    high_opt = Option(position.symbol, position.expiry, position.upper_strike, "C", "SMART")
    
    await ib.qualifyContractsAsync(low_opt)
    await ib.qualifyContractsAsync(mid_opt)
    await ib.qualifyContractsAsync(high_opt)
    
    # Reverse ops
    o1 = MarketOrder("SELL", position.contracts)
    o2 = MarketOrder("BUY", 2 * position.contracts)
    o3 = MarketOrder("SELL", position.contracts)
    
    t1 = ib.placeOrder(low_opt, o1)
    t2 = ib.placeOrder(mid_opt, o2)
    t3 = ib.placeOrder(high_opt, o3)
    
    MAX_WAIT = 15
    for _ in range(MAX_WAIT):
        if t1.isDone() and t2.isDone() and t3.isDone():
            break
        await asyncio.sleep(1)
        
    logger.info("✅ 平仓完成")
    clear_position()


async def close_all_positions(ib: IB):
    print("\n🔥 一键平仓模式")
    await cancel_all_option_orders(ib, SYMBOL)
    pos = await load_position_from_ibkr(ib, SYMBOL)
    if pos:
        await close_butterfly(ib, pos, "一键平仓指令")
    else:
        print("📭 未检测到持仓")
        clear_position()


def print_status(state: StrategyState, action: str, reason: str):
    pos = state.position
    print("\n" + "=" * 60)
    print(f"🦋 Butterfly Spread 状态 - {SYMBOL}")
    print(f"⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print(f"股价: ${state.current_price:.2f}")
    
    if pos:
        print(f"\n【持仓结构】")
        print(f"  Long ${pos.lower_strike} C | Short 2x ${pos.middle_strike} C | Long ${pos.upper_strike} C")
        print(f"  到期: {pos.expiry}")
        
        # 处理 initial_cost 为 0 的情况
        cost_display = pos.initial_cost
        cost_note = ""
        if pos.initial_cost == 0 or abs(pos.initial_cost) < 0.01:
            # 成本信息缺失，使用当前价值作为成本（假设刚开仓无盈亏）
            cost_display = pos.current_value
            cost_note = " (⚠️ 估算值)"
        
        pnl = pos.current_value - cost_display
        pnl_pct = pnl / cost_display if cost_display != 0 else 0
        
        print(f"  初始成本: ${cost_display:.2f}{cost_note}")
        print(f"  当前价值: ${pos.current_value:.2f}")
        print(f"  当前盈亏: ${pnl:+.2f} ({pnl_pct:+.1%})")
        
        max_profit = pos.get_max_profit()
        print(f"  最大盈利: ${max_profit:.2f} (若到期价=${pos.middle_strike})")
        
    print(f"\n【决策】")
    print(f"  👉 动作: {action}")
    print(f"  📝 原因: {reason}")
    print("=" * 60)


async def run_strategy(ib: IB, continuous: bool = False):
    logger.info(f"启动 Butterfly 策略 (Continuous={continuous})")
    
    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]
    
    state = StrategyState()
    
    while True:
        state.current_price = await get_stock_price(ib, stock)
        
        # 修复：优先使用本地状态文件识别仓位
        # 1. 先加载本地保存的仓位
        local_position = load_local_position()
        
        if local_position:
            # 2. 验证 IBKR 中是否仍持有对应合约（至少有部分持仓）
            positions = ib.positions()
            opts = [p for p in positions if p.contract.symbol == SYMBOL and p.contract.secType == "OPT"]
            
            # 检查本地记录的三个腿是否在 IBKR 中存在
            has_lower = any(
                p.contract.strike == local_position.lower_strike and 
                p.contract.lastTradeDateOrContractMonth == local_position.expiry and
                p.contract.right == "C" and p.position > 0
                for p in opts
            )
            has_middle = any(
                p.contract.strike == local_position.middle_strike and 
                p.contract.lastTradeDateOrContractMonth == local_position.expiry and
                p.contract.right == "C" and p.position < 0
                for p in opts
            )
            has_upper = any(
                p.contract.strike == local_position.upper_strike and 
                p.contract.lastTradeDateOrContractMonth == local_position.expiry and
                p.contract.right == "C" and p.position > 0
                for p in opts
            )
            
            if has_lower and has_middle and has_upper:
                logger.info(f"✅ 从本地状态确认 Butterfly 仓位: {local_position.lower_strike}/{local_position.middle_strike}/{local_position.upper_strike} @ {local_position.expiry}")
                state.position = local_position
            else:
                logger.warning(f"⚠️ 本地记录的 Butterfly 在 IBKR 中部分或全部不存在 (lower={has_lower}, mid={has_middle}, upper={has_upper})，清除本地记录")
                clear_position()
                state.position = None
        else:
            # 3. 没有本地记录，尝试从 IBKR 自动检测
            state.position = await load_position_from_ibkr(ib, SYMBOL)
            
        action = "HOLD"
        reason = "观察中"
        
        if state.position:
            # Update Value
            l = Option(SYMBOL, state.position.expiry, state.position.lower_strike, "C", "SMART")
            m = Option(SYMBOL, state.position.expiry, state.position.middle_strike, "C", "SMART")
            h = Option(SYMBOL, state.position.expiry, state.position.upper_strike, "C", "SMART")
            
            await ib.qualifyContractsAsync(l)
            await ib.qualifyContractsAsync(m)
            await ib.qualifyContractsAsync(h)
            
            lp = await get_option_price(ib, l)
            mp = await get_option_price(ib, m)
            hp = await get_option_price(ib, h)
            
            curr_val = (lp - 2*mp + hp) * 100 * state.position.contracts
            state.position.current_value = curr_val
            
            # 修复：如果 initial_cost 为 0，使用当前价值作为成本基础并保存
            if state.position.initial_cost == 0 or abs(state.position.initial_cost) < 0.01:
                state.position.initial_cost = curr_val
                state.position.entry_date = state.position.entry_date or datetime.now().strftime("%Y-%m-%d")
                logger.warning(f"⚠️ 缺失 initial_cost，使用当前市场价值 ${curr_val:.2f} 作为成本基础")
                save_position(state.position)
            
            pnl = curr_val - state.position.initial_cost
            cost = state.position.initial_cost
            pnl_pct = pnl / cost if cost != 0 else 0
            
            if pnl_pct >= PROFIT_TARGET_PCT:
                action = "CLOSE"
                reason = f"止盈 ({pnl_pct:.1%})"
            elif pnl_pct <= -STOP_LOSS_PCT: # Butterfly is debit strategy, max loss is 100% of cost usually
                action = "CLOSE"
                reason = f"止损 ({pnl_pct:.1%})"
                
            if action == "CLOSE":
                await close_butterfly(ib, state.position, reason)
                state.position = None
                
        else:
            action = "OPEN"
            reason = "无持仓，建立 Butterfly"
            new_pos = await open_butterfly(ib, stock, state.current_price)
            if new_pos:
                state.position = new_pos
                save_position(new_pos)
            else:
                action = "WAIT"
                reason = "开仓失败 (未找到合适合约)"
                
        print_status(state, action, reason)
        
        if not continuous:
            break
            
        await asyncio.sleep(CHECK_INTERVAL_SEC)


async def main():
    import signal
    def handle_shutdown(signum, frame):
        pass
    signal.signal(signal.SIGINT, handle_shutdown)
    
    ib = await connect_ib()
    try:
        if RUN_MODE == "close_all":
            await close_all_positions(ib)
        elif RUN_MODE == "continuous":
            await run_strategy(ib, continuous=True)
        else:
            await run_strategy(ib, continuous=False)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
