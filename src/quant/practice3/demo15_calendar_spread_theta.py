"""
Demo 15: Calendar Spread (日历价差)

================================================================================
📌 策略原理
================================================================================
Calendar Spread = 卖出近期期权 + 买入远期期权（同行权价）

核心逻辑：近期期权时间衰减更快，赚取 Theta 差

================================================================================
📌 参数说明
================================================================================
CAL_FRONT_IDX=1   # 近期到期日索引
CAL_BACK_IDX=3    # 远期到期日索引

================================================================================
📌 运行模式
================================================================================
# 模式1: 单次检查（推荐用於 cron）
CAL_MODE=daily uv run demo15_calendar_spread_theta.py

# 模式2: 持续监控
CAL_MODE=continuous uv run demo15_calendar_spread_theta.py

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
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "25"))

SYMBOL = os.getenv("CAL_SYMBOL", "AAPL")
EXCHANGE = os.getenv("CAL_EXCHANGE", "SMART")
CURRENCY = os.getenv("CAL_CURRENCY", "USD")

NUM_CONTRACTS = int(os.getenv("CAL_CONTRACTS", "1"))
FRONT_EXPIRY_IDX = int(os.getenv("CAL_FRONT_IDX", "1"))
BACK_EXPIRY_IDX = int(os.getenv("CAL_BACK_IDX", "3"))
PROFIT_TARGET_PCT = float(os.getenv("CAL_PROFIT_TARGET", "0.30"))
STOP_LOSS_PCT = float(os.getenv("CAL_STOP_LOSS", "0.50"))
CHECK_INTERVAL_SEC = int(os.getenv("CAL_CHECK_INTERVAL", "60"))
FALLBACK_PRICE = float(os.getenv("CAL_FALLBACK_PRICE", "280"))

# 运行模式
RUN_MODE = os.getenv("CAL_MODE", "daily")

USE_DELAYED_DATA = os.getenv("CAL_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("CAL_SIMULATION", "false").lower() == "true"

STATE_DIR = os.path.join(os.path.dirname(__file__), ".states")
STATE_FILE = os.path.join(STATE_DIR, f"calendar_spread_{SYMBOL.lower()}.json")


@dataclass
class CalendarPosition:
    """日历价差持仓"""
    symbol: str
    strike: float
    front_expiry: str  # 卖出的近期
    back_expiry: str   # 买入的远期
    rights: str        # "C" or "P"
    contracts: int = 0
    initial_cost: float = 0.0
    current_value: float = 0.0
    entry_date: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'CalendarPosition':
        return cls(**data)

    def get_days_to_front_expiry(self) -> int:
        if not self.front_expiry:
            return 0
        try:
            return (datetime.strptime(self.front_expiry, "%Y%m%d").date() - datetime.now().date()).days
        except:
            return 0


@dataclass
class StrategyState:
    position: Optional[CalendarPosition] = None
    current_price: float = 0.0
    net_theta: float = 0.0
    front_iv: float = 0.0
    back_iv: float = 0.0


def load_local_position() -> Optional[CalendarPosition]:
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, 'r') as f:
            data = json.load(f)
            return CalendarPosition.from_dict(data['position'])
    except Exception as e:
        logger.error(f"加载仓位失败: {e}")
        return None


def save_position(position: CalendarPosition):
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


async def load_position_from_ibkr(ib: IB, symbol: str) -> Optional[CalendarPosition]:
    """从 IBKR 识别日历价差 (同Strike, 同Right, 不同Expiry, 一多一空)"""
    positions = ib.positions()
    opts = [p for p in positions if p.contract.symbol == symbol and p.contract.secType == "OPT"]
    
    if not opts:
        return None
        
    # Group by Strike and Right
    from collections import defaultdict
    groups = defaultdict(list)
    for p in opts:
        groups[(p.contract.strike, p.contract.right)].append(p)
        
    for key, pos_list in groups.items():
        if len(pos_list) >= 2:
            # 找一正一负
            longs = [p for p in pos_list if p.position > 0]
            shorts = [p for p in pos_list if p.position < 0]
            
            if longs and shorts:
                l_pos = longs[0]
                s_pos = shorts[0]
                
                # Expiry check: Short should be near (front), Long should be far (back)
                l_exp = l_pos.contract.lastTradeDateOrContractMonth
                s_exp = s_pos.contract.lastTradeDateOrContractMonth
                
                if l_exp > s_exp and abs(l_pos.position) == abs(s_pos.position):
                    logger.info(f"✅ 检测到日历价差: {s_exp}(S)/{l_exp}(L) @ {key[0]}")
                    
                    local = load_local_position()
                    cost = local.initial_cost if local else 0.0
                    date = local.entry_date if local else ""
                    
                    return CalendarPosition(
                        symbol=symbol,
                        strike=key[0],
                        front_expiry=s_exp,
                        back_expiry=l_exp,
                        rights=key[1],
                        contracts=int(abs(l_pos.position)),
                        initial_cost=cost,
                        entry_date=date
                    )
    return None


async def get_option_greeks(ib: IB, option: Option) -> Tuple[float, float]: # Price, Theta
    ticker = ib.reqMktData(option, "106", False, False)
    await asyncio.sleep(2)
    price = ticker.last or ticker.close or ((ticker.bid or 0) + (ticker.ask or 0)) / 2
    theta = 0.0
    if ticker.modelGreeks and ticker.modelGreeks.theta:
        theta = ticker.modelGreeks.theta
    ib.cancelMktData(option)
    return price, theta


async def open_calendar_spread(ib: IB, stock: Stock, price: float) -> Optional[CalendarPosition]:
    """开仓: 卖近 买远"""
    logger.info("📦 正在开仓 Calendar Spread...")
    
    chains = await ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)
    if not chains:
        logger.error("无法获取期权链参数")
        return None
        
    chain = next((c for c in chains if c.exchange == "SMART"), chains[0])
    
    today = datetime.now().strftime("%Y%m%d")
    valid_exps = sorted([e for e in chain.expirations if e > today])
    
    if len(valid_exps) <= BACK_EXPIRY_IDX:
        logger.error("期权链到期日不足")
        return None
        
    front_exp = valid_exps[FRONT_EXPIRY_IDX]
    back_exp = valid_exps[BACK_EXPIRY_IDX]
    
    # 获取有效合约，确保 Strike 存在
    # 我们先查近月合约，因为近月合约通常更少
    temp_contract = Option(stock.symbol, front_exp, exchange="SMART")
    try:
        details = await ib.reqContractDetailsAsync(temp_contract)
    except Exception as e:
        logger.error(f"获取近月合约详情失败: {e}")
        return None
        
    if not details:
        logger.error("无有效近月合约")
        return None
        
    valid_front = [d.contract for d in details if d.contract.right == "C"]
    if not valid_front:
        valid_front = [d.contract for d in details if d.contract.right == "P"] # 如果没有Call试Put
        
    if not valid_front:
        logger.error("无有效近月合约 (Call/Put)")
        return None
        
    right = valid_front[0].right
    
    # 找最接近现价的 Strike
    best_front = min(valid_front, key=lambda c: abs(c.strike - price))
    strike = best_front.strike
    
    front_opt = best_front
    
    # 构造远月合约 (假设远月同Strike通常存在，但也需验证)
    # 为了保险，我们最好也 qualify 一下远月
    back_opt = Option(stock.symbol, back_exp, strike, right, "SMART")
    try:
        # 这里用 qualify 验证远月是否存在
        qualified_back = await ib.qualifyContractsAsync(back_opt)
        if not qualified_back:
             raise ValueError("无法 Qualify 远月合约")
        back_opt = qualified_back[0]
    except Exception as e:
        logger.error(f"远月合约 {back_exp} Strike {strike} 不存在: {e}")
        return None
    
    fp, _ = await get_option_greeks(ib, front_opt)
    bp, _ = await get_option_greeks(ib, back_opt)
    
    net_debit_per = bp - fp
    total_cost = net_debit_per * 100 * NUM_CONTRACTS
    
    if SIMULATION_MODE:
        logger.info(f"[模拟] 买入 {back_exp} {right}, 卖出 {front_exp} {right} @ {strike}, 净支出 ${total_cost:.2f}")
    else:
        # 腿 1: 买 Back
        b_order = MarketOrder("BUY", NUM_CONTRACTS)
        b_trade = ib.placeOrder(back_opt, b_order)
        
        # 腿 2: 卖 Front
        f_order = MarketOrder("SELL", NUM_CONTRACTS)
        f_trade = ib.placeOrder(front_opt, f_order)
        
        MAX_WAIT = 10
        for _ in range(MAX_WAIT):
            if b_trade.isDone() and f_trade.isDone():
                break
            await asyncio.sleep(1)
            
        logger.info("✅ 订单提交完成")
        
    return CalendarPosition(
        symbol=SYMBOL,
        strike=strike,
        front_expiry=front_exp,
        back_expiry=back_exp,
        rights=right,
        contracts=NUM_CONTRACTS,
        initial_cost=total_cost,
        current_value=total_cost,
        entry_date=datetime.now().strftime("%Y-%m-%d")
    )


async def close_position(ib: IB, position: CalendarPosition, reason: str):
    logger.info(f"🔻 平仓中 ({reason})...")
    
    if SIMULATION_MODE:
        logger.info("[模拟] 平仓完成")
        clear_position()
        return

    front_opt = Option(position.symbol, position.front_expiry, position.strike, position.rights, "SMART")
    back_opt = Option(position.symbol, position.back_expiry, position.strike, position.rights, "SMART")
    await ib.qualifyContractsAsync(front_opt)
    await ib.qualifyContractsAsync(back_opt)
    
    # 平仓: 买回 Front, 卖出 Back
    f_order = MarketOrder("BUY", position.contracts)
    b_order = MarketOrder("SELL", position.contracts)
    
    ft = ib.placeOrder(front_opt, f_order)
    bt = ib.placeOrder(back_opt, b_order)
    
    while not (ft.isDone() and bt.isDone()):
        await asyncio.sleep(1)
        
    logger.info("✅ 平仓完成")
    clear_position()


async def close_all_positions(ib: IB):
    print("\n🔥 一键平仓模式")
    await cancel_all_option_orders(ib, SYMBOL)
    pos = await load_position_from_ibkr(ib, SYMBOL)
    if pos:
        await close_position(ib, pos, "一键平仓指令")
    else:
        print("📭 未检测到持仓")
        clear_position()


def print_status(state: StrategyState, action: str, reason: str):
    pos = state.position
    print("\n" + "=" * 60)
    print(f"📅 Calendar Spread 状态 - {SYMBOL}")
    print(f"⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print(f"股价: ${state.current_price:.2f}")
    
    if pos:
        print(f"\n【持仓】")
        print(f"  行权价: ${pos.strike}")
        print(f"  近期(Short): {pos.front_expiry} ({pos.get_days_to_front_expiry()}天)")
        print(f"  远期(Long):  {pos.back_expiry}")
        print(f"  初始成本: ${pos.initial_cost:.2f}")
        print(f"  当前价值: ${pos.current_value:.2f}")
        
        pnl = pos.current_value - pos.initial_cost
        pnl_pct = pnl / pos.initial_cost if pos.initial_cost > 0 else 0
        print(f"  盈亏: ${pnl:+.2f} ({pnl_pct:+.1%})")
        print(f"  净 Theta: {state.net_theta:+.2f} (Time Decay Income)")
        
    print(f"\n【决策】")
    print(f"  👉 动作: {action}")
    print(f"  📝 原因: {reason}")
    print("=" * 60)


async def run_strategy(ib: IB, continuous: bool = False):
    logger.info(f"启动 Calendar 策略 (Continuous={continuous})")
    
    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]
    
    state = StrategyState()
    
    while True:
        state.current_price = await get_stock_price(ib, stock)
        
        state.position = await load_position_from_ibkr(ib, SYMBOL)
        if not state.position and SIMULATION_MODE:
            state.position = load_local_position()
            
        action = "HOLD"
        reason = "观察中"
        
        if state.position:
            # Update Value
            f_opt = Option(SYMBOL, state.position.front_expiry, state.position.strike, state.position.rights, "SMART")
            b_opt = Option(SYMBOL, state.position.back_expiry, state.position.strike, state.position.rights, "SMART")
            await ib.qualifyContractsAsync(f_opt)
            await ib.qualifyContractsAsync(b_opt)
            
            fp, ft = await get_option_greeks(ib, f_opt)
            bp, bt = await get_option_greeks(ib, b_opt)
            
            # Short Front (Theta is positive for us), Long Back (Theta is negative cost)
            # Typically Short Option has Positive Theta (earns money), Long has Negative
            # Here we sold Front -> Theta gain. Bought Back -> Theta loss.
            # Calendar relies on Front theta > Back theta.
            # IBKR 'theta' field usually negative for Long.
            # So: Net Theta = (Short Position Theta) + (Long Position Theta)
            # Short Pos Theta = -1 * Theta(of 1 unit) * (-1 contract) = Theta
            # Wait, IBKR report theta for 1 unit long.
            # We are Short Front: Theta Gain = -1 * (FrontTheta)
            # We are Long Back: Theta Loss = BackTheta
            # Wait, usually options have negative theta (value decays).
            # So FrontTheta is e.g. -0.05. Selling it means we gain 0.05.
            # BackTheta is e.g. -0.03. Buying it means we lose 0.03.
            # Net = -FrontTheta + BackTheta = -(-0.05) + (-0.03) = 0.02 Gain.
            
            state.net_theta = (-ft) + bt
            state.position.current_value = (bp - fp) * 100 * state.position.contracts
            
            pnl = state.position.current_value - state.position.initial_cost
            pnl_pct = pnl / state.position.initial_cost if state.position.initial_cost else 0
            
            days = state.position.get_days_to_front_expiry()
            
            if pnl_pct >= PROFIT_TARGET_PCT:
                action = "CLOSE"
                reason = f"止盈 ({pnl_pct:.1%})"
            elif pnl_pct <= -STOP_LOSS_PCT:
                action = "CLOSE"
                reason = f"止损 ({pnl_pct:.1%})"
            elif days <= 1:
                action = "CLOSE"
                reason = "近期合约即将到期"
                
            if action == "CLOSE":
                await close_position(ib, state.position, reason)
                state.position = None
                
        else:
            # Check Open
            # Calendar usually opened when IV is low and expected to rise, or neutral outlook
            # Here we just open if no position for practice
            action = "OPEN"
            reason = "无持仓，建立日历价差"
            new_pos = await open_calendar_spread(ib, stock, state.current_price)
            if new_pos:
                state.position = new_pos
                save_position(new_pos)
            
        print_status(state, action, reason)
        
        if not continuous:
            break
        
        await asyncio.sleep(CHECK_INTERVAL_SEC)


async def main():
    ib = await connect_ib()
    try:
        if RUN_MODE == "close_all":
            await close_all_positions(ib)
        elif RUN_MODE == "continuous":
            await run_strategy(ib, continuous=True)
        else:
            await run_strategy(ib, continuous=False)
            
    except KeyboardInterrupt:
        logger.info("用户停止")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
    finally:
        ib.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
