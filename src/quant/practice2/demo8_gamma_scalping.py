"""
Demo 8: Gamma Scalping Strategy (伽马套利策略)

================================================================================
📌 策略原理
================================================================================
Gamma Scalping = Long Gamma (买入跨式/宽跨/ATM Call) + Dynamic Delta Hedging

核心逻辑：
1. 买入期权（持有正 Gamma）：通常是 ATM Call 或 Straddle。
2. Delta 对冲：初始时卖出股票使组合 Delta = 0。
3. 动态调整：
   - 股价上涨 -> Call Delta 增加 -> 组合变为正 Delta -> 卖出股票（高卖）
   - 股价下跌 -> Call Delta 减少 -> 组合变为负 Delta -> 买入股票（低买）
4. 获利来源：通过"高抛低吸"股票来覆盖期权的时间损耗(Theta)，并赚取净利润。

================================================================================
📌 运行模式 (GS_MODE)
================================================================================
- daily:      单次检查。如果 Delta 偏离超过阈值则进行再平衡，否则退出。这适合 Cron Job。
- continuous: 持续运行循环监控 (默认间隔 60s)。
- close_all:  平仓所有关联头寸（期权+股票）。

================================================================================
📌 状态持久化
================================================================================
策略会在 .states/gamma_scalping_{symbol}.json 中保存当前状态 (累积盈亏、持仓详情)。
重启时会自动加载状态，确保长期运行的 P&L 统计连续性。
"""
import asyncio
import os
import math
import json
import logging
from datetime import datetime
from typing import Optional, Dict, List
from dataclasses import dataclass, asdict

from ib_async import IB, Stock, Option, MarketOrder, Contract

# ========== 日志配置 ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ========== 环境配置 ==========
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "18"))

SYMBOL = os.getenv("GS_SYMBOL", "AAPL")
EXCHANGE = os.getenv("GS_EXCHANGE", "SMART")
CURRENCY = os.getenv("GS_CURRENCY", "USD")

# 策略参数
OPTION_CONTRACTS = int(os.getenv("GS_CONTRACTS", "1"))  # 期权手数
DELTA_THRESHOLD = float(os.getenv("GS_DELTA_THRESHOLD", "0.10")) # Delta 偏离这一比例触发对冲 (e.g. 0.1 = 10%)
CHECK_INTERVAL = int(os.getenv("GS_INTERVAL", "60"))
RUN_MODE = "continuous" # Force continuous mode as primary
SIMULATION_MODE = os.getenv("GS_SIMULATION", "false").lower() == "true"

# 状态管理
STATE_DIR = os.path.join(os.path.dirname(__file__), ".states")
STATE_FILE = os.path.join(STATE_DIR, f"gamma_scalping_{SYMBOL.lower()}.json")


@dataclass
class GammaPosition:
    symbol: str
    option_conId: int
    option_expiry: str
    option_strike: float
    option_right: str  # C or P
    
    option_contracts: int  # 正数=Long
    stock_shares: int      # 负数=Short
    
    entry_price: float     # 初始股价
    total_realized_pnl: float = 0.0 # 累计已实现盈亏(股票)
    total_traded_shares: int = 0
    net_cash_balance: float = 0.0 # 净现金流 (Credits - Debits)
    start_date: str = ""
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'GammaPosition':
        return cls(**data)


@dataclass
class StrategyState:
    position: Optional[GammaPosition] = None
    last_update: datetime = datetime.now()
    current_price: float = 0.0
    current_option_price: float = 0.0
    current_delta: float = 0.0
    
    
# ========== 工具函数 ==========

def load_local_state() -> Optional[GammaPosition]:
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, 'r') as f:
            data = json.load(f)
            return GammaPosition.from_dict(data)
    except Exception as e:
        logger.error(f"加载状态失败: {e}")
        return None

def save_local_state(pos: GammaPosition):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(pos.to_dict(), f, indent=2)
    logger.info("状态已保存")

def clear_local_state():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
        logger.info("本地状态已清除")


async def get_stock_price(ib: IB, contract: Contract) -> float:
    """获取标的最新价格"""
    ticker = ib.reqMktData(contract, "", False, False)
    await asyncio.sleep(2)
    price = ticker.last or ticker.close or ((ticker.bid + ticker.ask)/2 if ticker.bid else 0)
    ib.cancelMktData(contract)
    return price if price and not math.isnan(price) else 0.0


async def get_atm_option_contract(ib: IB, stock: Stock, price: float) -> Optional[Option]:
    """寻找最近月 ATM Call"""
    logger.info("寻找 ATM Option...")
    chains = await ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)
    if not chains:
        logger.error("无期权链数据")
        return None
        
    chain = next((c for c in chains if c.exchange == "SMART"), chains[0])
    
    # 找至少 14 天后的到期日
    import datetime as dt
    target_date = (datetime.now() + dt.timedelta(days=14)).strftime("%Y%m%d")
    valid_exps = sorted([e for e in chain.expirations if e > target_date])
    
    if not valid_exps:
        valid_exps = sorted([e for e in chain.expirations if e > datetime.now().strftime("%Y%m%d")])
    
    if not valid_exps:
        logger.error("无有效到期日")
        return None
        
    expiry = valid_exps[0]
    
    # 获取详细合约列表以确认 Strike 存在
    temp = Option(stock.symbol, expiry, exchange="SMART")
    details = await ib.reqContractDetailsAsync(temp)
    if not details:
        logger.error("无法获取合约详情")
        return None
        
    calls = [d.contract for d in details if d.contract.right == 'C']
    if not calls:
        return None
        
    # 找 ATM
    best_call = min(calls, key=lambda c: abs(c.strike - price))
    
    return best_call


async def get_greeks(ib: IB, contract: Option) -> tuple[float, float]:
    """获取期权价格和 Delta"""
    # Generated ticks: 106=Option Implied Volatility
    ticker = ib.reqMktData(contract, "106", False, False)
    await asyncio.sleep(3) # Wait for greeks
    
    delta = 0.5 # Default
    if ticker.modelGreeks and ticker.modelGreeks.delta:
        delta = ticker.modelGreeks.delta
    elif ticker.lastGreeks and ticker.lastGreeks.delta:
        delta = ticker.lastGreeks.delta
        
    price = ticker.last or ticker.close or ((ticker.bid + ticker.ask)/2 if ticker.bid else 0)
    
    ib.cancelMktData(contract)
    return (price, delta)

# ========== 核心逻辑 ==========

async def open_position(ib: IB, stock: Stock) -> Optional[GammaPosition]:
    """开仓: 买入期权 + 初始化对冲"""
    price = await get_stock_price(ib, stock)
    if price <= 0:
        logger.error("无效股价")
        return None
        
    opt_contract = await get_atm_option_contract(ib, stock, price)
    if not opt_contract:
        return None
        
    # 1. 买入期权
    opt_qty = OPTION_CONTRACTS
    logger.info(f"开仓: 买入 {opt_qty}x {opt_contract.localSymbol}")
    
    opt_conId = 0
    actual_opt_price = 0.0
    
    if SIMULATION_MODE:
        logger.info("[模拟] 期权订单已成交")
        opt_conId = 123456 # Fake
        actual_opt_price, _ = await get_greeks(ib, opt_contract)
    else:
        order = MarketOrder("BUY", opt_qty)
        trade = ib.placeOrder(opt_contract, order)
        
        MAX_WAIT = 20
        for _ in range(MAX_WAIT):
            if trade.isDone():
                break
            await asyncio.sleep(1)
            
        if trade.orderStatus.status != 'Filled':
            logger.error(f"期权订单未成交 (状态: {trade.orderStatus.status})")
            return None
            
        opt_conId = trade.contract.conId
        actual_opt_price = trade.orderStatus.avgFillPrice
        logger.info(f"✅ 期权成交 @ {actual_opt_price:.2f}")
        
    # 2. 初始对冲 (Sell Shares)
    _, delta = await get_greeks(ib, opt_contract)
    target_hedge = -int(delta * 100 * opt_qty)
    
    logger.info(f"初始对冲: Delta={delta:.2f}, 需持有股票 {target_hedge}")
    
    hedge_filled_qty = 0
    actual_stock_price = price
    
    if target_hedge != 0:
        action = "SELL" if target_hedge < 0 else "BUY"
        qty = abs(target_hedge)
        if SIMULATION_MODE:
             logger.info(f"[模拟] 股票 {action} {qty}")
             hedge_filled_qty = target_hedge
             actual_stock_price = price
        else:
             s_order = MarketOrder(action, qty)
             s_trade = ib.placeOrder(stock, s_order)
             
             MAX_WAIT = 20
             for _ in range(MAX_WAIT):
                 if s_trade.isDone():
                     break
                 await asyncio.sleep(1)
                 
             if s_trade.orderStatus.status == 'Filled':
                 actual_stock_price = s_trade.orderStatus.avgFillPrice
                 logger.info(f"✅ 股票成交 @ {actual_stock_price:.2f}")
                 hedge_filled_qty = target_hedge
             else:
                 logger.error(f"股票对冲订单未成交 (状态: {s_trade.orderStatus.status})")
                 hedge_filled_qty = 0
                 
    # 计算初始现金流
    # Cash -= Option Cost (Debit)
    # Cash -= Stock Cost (If Buy -, If Sell -(-val) = +)
    initial_cash_flow = -(opt_qty * 100 * actual_opt_price) - (hedge_filled_qty * actual_stock_price)
                 
    return GammaPosition(
        symbol=stock.symbol,
        option_conId=opt_conId,
        option_expiry=opt_contract.lastTradeDateOrContractMonth,
        option_strike=opt_contract.strike,
        option_right=opt_contract.right,
        option_contracts=opt_qty,
        stock_shares=hedge_filled_qty, 
        entry_price=price, 
        net_cash_balance=initial_cash_flow,
        start_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


async def rebalance(ib: IB, stock: Stock, state: StrategyState):
    """检查并执行再平衡"""
    pos = state.position
    
    # 重建期权对象
    opt = Option(pos.symbol, pos.option_expiry, pos.option_strike, pos.option_right, "SMART")
    if pos.option_conId:
        opt.conId = pos.option_conId
        
    if not SIMULATION_MODE:
        # Qualify to be sure
        details = await ib.reqContractDetailsAsync(opt)
        if details:
            opt = details[0].contract
            
    # 获取实时数据
    stock_price = await get_stock_price(ib, stock)
    opt_price, opt_delta = await get_greeks(ib, opt)
    
    state.current_price = stock_price
    state.current_option_price = opt_price # Added
    state.current_delta = opt_delta
    
    # 计算总 Delta
    # Option Delta Position = contracts * 100 * delta
    opt_pos_delta = pos.option_contracts * 100 * opt_delta
    
    # Stock Delta Position = shares (1 delta per share)
    stock_pos_delta = pos.stock_shares
    
    net_delta = opt_pos_delta + stock_pos_delta
    
    # 检查偏离度
    # 阈值是相对于 Option Delta 的比例? 还是绝对值?
    # 通常相对于 Option Delta: 如果 Net Delta > 10% of Option Exposure
    
    reference_exposure = abs(opt_pos_delta)
    if reference_exposure < 0.1: reference_exposure = 100 # Avoid div by zero
    
    deviation_pct = abs(net_delta) / reference_exposure
    
    print_status(state, net_delta, deviation_pct)
    
    if deviation_pct > DELTA_THRESHOLD:
        logger.info(f"⚠️ Delta 偏离 {deviation_pct:.1%} > {DELTA_THRESHOLD:.1%} - 执行再平衡")
        
        # 目标: Net Delta => 0
        # New Stock Shares = -Option Delta
        target_shares = -int(opt_pos_delta)
        diff = target_shares - pos.stock_shares
        
        if diff == 0:
            return
            
        action = "BUY" if diff > 0 else "SELL"
        qty = abs(diff)
        
        logger.info(f"调整股票: {action} {qty} 股 (当前: {pos.stock_shares} -> 目标: {target_shares})")
        
        executed_price = stock_price
        
        if SIMULATION_MODE:
            logger.info(f"[模拟] 成交 @ {stock_price:.2f}")
        else:
            order = MarketOrder(action, qty)
            trade = ib.placeOrder(stock, order)
            while not trade.isDone():
                await asyncio.sleep(1)
            executed_price = trade.orderStatus.avgFillPrice
            logger.info(f"✅ 真实成交 @ {executed_price:.2f}")
            
        # 记录 P&L (如果是平仓部分/反向交易)
        # Gamma Scalping P&L comes from:
        # Sell high (short more), Buy low (cover short)
        
        # 简单 P&L 估算: 
        # 我们这里只要追踪 Realized P&L
        # 对于股票: 
        # 如果当前是 Short 100, 现在 Buy 20 to become Short 80.
        # 这 20 股实际上是平了之前的 Short. 
        # 这种计算比较复杂 (FIFO/LIFO). 
        # 我们简化处理: 
        # 每次 'Scalp' (逆势操作) 都会产生 Realized P&L.
        
        # 粗略逻辑: 
        # 只要我们是在 rebalance, 
        # 如果是 Buy (股价跌了): 我们是在低位买回之前高位卖出的 -> 盈利
        # 如果是 Sell (股价涨了): 我们是在高位卖出 -> 锁定更高卖价
        
        # 为了精确计算，我们需要每个 share block 的成本。
        # 这里为了演示简单，我们只记录 "Implied Gamma P&L":
        # PnL approx = 0.5 * Gamma * (dS^2) 
        # 但我们这里是实盘，直接记个流水比较难。
        # 我们可以只更新 `total_stock_traded` 和 `stock_shares`。
        # 真正的 Realized P&L 最好由 IBKR Account Summary 提供.
        # 但为了 demo 效果，我们可以用 simplified avg_price method.
        # (暂略复杂 PnL 计算，专注动作)
        
        prev_shares = pos.stock_shares
        pos.stock_shares += diff
        pos.total_traded_shares += qty
        
        # 更新现金流
        # Buy: Cash -= price * qty
        # Sell: Cash += price * qty
        cash_change = -(executed_price * diff)
        pos.net_cash_balance += cash_change
        
        logger.info(f"资金变动: ${cash_change:.2f} | 当前净现金流: ${pos.net_cash_balance:.2f}")
        
        if not SIMULATION_MODE:
            # 尝试保存状态
            save_local_state(pos)
    else:
        logger.info("✅ Delta 平衡良好")


def print_status(state: StrategyState, net_delta: float, deviation: float):
    pos = state.position
    print("\n" + "=" * 60)
    print(f"📊 Gamma Scalping 状态 - {pos.symbol}")
    print(f"⏰ {datetime.now().strftime('%H:%M:%S')}")
    print("-" * 60)
    print(f"股价: ${state.current_price:.2f}")
    print(f"期权: {pos.option_contracts}x {pos.option_right} @ {pos.option_strike} (Exp: {pos.option_expiry})")
    print(f"股票: {pos.stock_shares} 股")
    print("-" * 60)
    print(f"当前 Delta: {state.current_delta:.3f}")
    print(f"期权总 Delta: {pos.option_contracts * 100 * state.current_delta:.1f}")
    print(f"股票总 Delta: {pos.stock_shares:.1f}")
    print(f"净 Delta:     {net_delta:+.1f}")
    print(f"偏离度:       {deviation:.1%} (阈值: {DELTA_THRESHOLD:.1%})")
    # P&L Calculation
    # Equity = Net Cash + MV(Options) + MV(Stock)
    if pos:
        mv_options = pos.option_contracts * 100 * state.current_option_price
        mv_stock = pos.stock_shares * state.current_price
        total_equity = pos.net_cash_balance + mv_options + mv_stock
        
        print("-" * 60)
        print(f"期权市值:     ${mv_options:.2f}")
        print(f"股票市值:     ${mv_stock:.2f}")
        print(f"净现金流:     ${pos.net_cash_balance:.2f}")
        print(f"总盈亏(P&L):  ${total_equity:+.2f}")
        
    print("=" * 60 + "\n")


async def close_all(ib: IB, stock: Stock):
    logger.info("🔥 执行全账户平仓/重置任务...")
    
    # 1. 优先根据本地记录平期权 (因为自动识别期权组合较难)
    pos = load_local_state()
    if pos and pos.option_contracts > 0:
        opt = Option(pos.symbol, pos.option_expiry, pos.option_strike, pos.option_right, "SMART")
        logger.info(f"平仓策略期权: Sell {pos.option_contracts}x {opt.symbol}")
        if not SIMULATION_MODE:
            try:
                details = await ib.reqContractDetailsAsync(opt)
                if details:
                    o_order = MarketOrder("SELL", pos.option_contracts)
                    trade = ib.placeOrder(details[0].contract, o_order)
                    while not trade.isDone(): await asyncio.sleep(1)
                    logger.info("✅ 期权平仓完成")
            except Exception as e:
                logger.error(f"期权平仓失败: {e}")
                
    # 2. 【关键修改】直接读取 IBKR 账户的实际股票持仓并清零
    # 不依赖本地记录，确保账户对应标的归零
    positions = ib.positions()
    target_pos = next((p for p in positions if p.contract.symbol == SYMBOL and p.contract.secType == 'STK'), None)
    
    if target_pos and target_pos.position != 0:
        actual_shares = target_pos.position
        logger.info(f"检测到账户实际持仓: {actual_shares} 股")
        
        action = "SELL" if actual_shares > 0 else "BUY"
        qty = abs(actual_shares)
        
        logger.info(f"执行股票清仓: {action} {qty} 股")
        if not SIMULATION_MODE:
            try:
                s_order = MarketOrder(action, qty)
                s_trade = ib.placeOrder(stock, s_order)
                while not s_trade.isDone(): await asyncio.sleep(1)
                logger.info("✅ 股票已全部平仓 (Qty=0)")
            except Exception as e:
                logger.error(f"股票平仓失败: {e}")
    else:
        logger.info("账户无股票持仓，无需操作")
            
    clear_local_state()
    logger.info("✅ 重置完成 (State Cleared)")


async def main():
    # Remove custom signal handler to allow KeyboardInterrupt to be raised normally
    
    ib = IB()
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    
    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]
    
    try:
        # 1. 每次启动前，先全平旧仓位（Reset）
        await close_all(ib, stock)
        
        # 2. 建立新仓位
        state = StrategyState()
        new_pos = await open_position(ib, stock)
        
        if new_pos:
            save_local_state(new_pos)
            state.position = new_pos
        else:
            logger.error("❌ 开仓失败，程序退出")
            return
            
        # 3. 进入持续监控循环
        logger.info(f"🟢 策略运行中 | 间隔: {CHECK_INTERVAL}s | 按 Ctrl+C 平仓并退出")
        while True:
            await rebalance(ib, stock, state)
            await asyncio.sleep(CHECK_INTERVAL)
            
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("\n🛑 用户停止 (Ctrl+C) - 正在平仓...")
        await close_all(ib, stock)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        await close_all(ib, stock)  # Error also triggers cleanup
    finally:
        if ib.isConnected():
            ib.disconnect()
        logger.info("已断开连接")

if __name__ == "__main__":
    asyncio.run(main())
