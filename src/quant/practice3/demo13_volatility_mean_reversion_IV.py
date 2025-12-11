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
# 模式1: 单次检查（推荐用于 cron）
VOL_MODE=daily uv run demo13_volatility_mean_reversion_IV.py

# 模式2: 持续监控
VOL_MODE=continuous uv run demo13_volatility_mean_reversion_IV.py

# 模式3: 一键平仓
VOL_MODE=close_all uv run demo13_volatility_mean_reversion_IV.py

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
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field, asdict
from collections import deque

from ib_async import IB, Stock, Option, MarketOrder, LimitOrder

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

# 运行模式: daily = 单次检查, continuous = 持续监控, close_all = 一键平仓
RUN_MODE = os.getenv("VOL_MODE", "daily")

USE_DELAYED_DATA = os.getenv("VOL_USE_DELAYED", "true").lower() == "true"
SIMULATION_MODE = os.getenv("VOL_SIMULATION", "false").lower() == "true"

# 状态文件
STATE_DIR = os.path.join(os.path.dirname(__file__), ".states")
STATE_FILE = os.path.join(STATE_DIR, f"vol_strategy_{SYMBOL.lower()}.json")


@dataclass
class VolatilityPosition:
    """波动率策略持仓 (Straddle/Strangle)"""
    symbol: str
    strike_call: float
    strike_put: float
    expiry: str
    contracts: int = 0  # 正=多头(买入)，负=空头(卖出)
    entry_iv: float = 0.0
    entry_price: float = 0.0  # 组合单价
    current_value: float = 0.0
    entry_date: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'VolatilityPosition':
        return cls(**data)

    def get_days_to_expiry(self) -> int:
        if not self.expiry:
            return 999
        try:
            return (datetime.strptime(self.expiry, "%Y%m%d").date() - datetime.now().date()).days
        except:
            return 999


@dataclass
class StrategyState:
    position: Optional[VolatilityPosition] = None
    hv_20d: float = 0.0
    current_iv: float = 0.0
    current_price: float = 0.0
    price_history: List[float] = field(default_factory=list)


def load_local_position() -> Optional[VolatilityPosition]:
    """从文件加载仓位"""
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, 'r') as f:
            data = json.load(f)
            return VolatilityPosition.from_dict(data['position'])
    except Exception as e:
        logger.error(f"加载仓位失败: {e}")
        return None


def save_position(position: VolatilityPosition):
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
    """清除仓位文件"""
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
    """取消所有期权挂单"""
    open_trades = ib.openTrades()
    cancelled_count = 0
    for trade in open_trades:
        contract = trade.contract
        if contract.secType == "OPT" and contract.symbol == symbol:
            status = trade.orderStatus.status
            if status in ["PendingSubmit", "PreSubmitted", "Submitted"]:
                ib.cancelOrder(trade.order)
                cancelled_count += 1
                logger.info(f"取消挂单: {contract.localSymbol} {trade.order.action}")
    
    if cancelled_count > 0:
        await asyncio.sleep(2)
        logger.info(f"✅ 已取消 {cancelled_count} 个挂单")


async def load_position_from_ibkr(ib: IB, symbol: str) -> Optional[VolatilityPosition]:
    """从 IBKR 查询真实持仓，检测是否存在 Straddle/Strangle"""
    positions = ib.positions()
    option_positions = [
        p for p in positions 
        if p.contract.symbol == symbol and p.contract.secType == "OPT"
    ]
    
    if not option_positions:
        return None
        
    # 按到期日分组
    from collections import defaultdict
    expiry_groups = defaultdict(list)
    for p in option_positions:
        expiry_groups[p.contract.lastTradeDateOrContractMonth].append(p)
        
    # 寻找匹配的 Call/Put 对
    # 这里的简化逻辑：找同一到期日，数量相等且方向相同的 Call 和 Put
    for expiry, pos_list in expiry_groups.items():
        calls = [p for p in pos_list if p.contract.right == 'C']
        puts = [p for p in pos_list if p.contract.right == 'P']
        
        if calls and puts:
            # 简单匹配第一个对子
            call_pos = calls[0]
            put_pos = puts[0]
            
            # 检查数量是否匹配 (符号相同表示同向)
            if call_pos.position == put_pos.position:
                logger.info(f"✅ 检测到组合持仓: {expiry} Call:{call_pos.contract.strike} Put:{put_pos.contract.strike}")
                
                # 读取本地保存的 entry_iv，如果没找到则用当前 IV 估算或设为 0
                local_pos = load_local_position()
                entry_iv = local_pos.entry_iv if local_pos else 0.0
                entry_date = local_pos.entry_date if local_pos else ""
                entry_price = local_pos.entry_price if local_pos else 0.0
                
                return VolatilityPosition(
                    symbol=symbol,
                    strike_call=call_pos.contract.strike,
                    strike_put=put_pos.contract.strike,
                    expiry=expiry,
                    contracts=int(call_pos.position),  # 正=Long, 负=Short
                    entry_iv=entry_iv,
                    entry_price=entry_price,
                    entry_date=entry_date
                )

    return None


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


def calculate_historical_volatility(prices: List[float], days: int = 20) -> float:
    """计算历史波动率 (年化)"""
    if len(prices) < days + 1:
        return 0.25
    returns = []
    for i in range(1, min(days + 1, len(prices))):
        ret = math.log(prices[-i] / prices[-i-1])
        returns.append(ret)
    if len(returns) < 2:
        return 0.25
    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(252)


async def get_option_greeks(ib: IB, option: Option) -> Tuple[float, float]:
    """获取期权价格和 IV"""
    # 重新 qualifiy 确保 conId 正确
    # options 应该是已经 qualify 过的，所以直接 reqMktData
    ticker = ib.reqMktData(option, "106", False, False)
    await asyncio.sleep(2)
    
    price = ticker.last or ticker.close or ((ticker.bid or 0) + (ticker.ask or 0)) / 2
    iv = 0.0
    if ticker.modelGreeks and ticker.modelGreeks.impliedVol:
        iv = ticker.modelGreeks.impliedVol
    elif ticker.lastGreeks and ticker.lastGreeks.impliedVol:
        iv = ticker.lastGreeks.impliedVol
        
    ib.cancelMktData(option)
    return price, iv


async def open_straddle(ib: IB, stock: Stock, direction: str, price: float) -> Optional[VolatilityPosition]:
    """开仓 Straddle (同Strike)"""
    logger.info(f"📦 正在开仓 Straddle ({direction})...")
    
    # 获取期权链参数
    chains = await ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)
    if not chains:
        logger.error("无法获取期权链参数")
        return None
        
    chain = next((c for c in chains if c.exchange == "SMART"), chains[0])
    
    # 过滤出未来的到期日
    import datetime as dt
    target_date = (datetime.now() + dt.timedelta(days=30)).strftime("%Y%m%d")
    
    # 优先找30天后的，如果没有则找最近的
    valid_expirations = sorted([e for e in chain.expirations if e > target_date])
    if not valid_expirations:
        valid_expirations = sorted([e for e in chain.expirations if e > datetime.now().strftime("%Y%m%d")])
    
    if not valid_expirations:
        logger.error("无可用到期日")
        return None

    # 遍历到期日，寻找合适的合约
    call = None
    put = None
    expiry = None
    strike = 0.0

    for exp in valid_expirations[:3]: # 只尝试最近的3个有效到期日
        # 使用 reqContractDetails 获取该到期日下的实际有效合约列表
        # 这样可以确保 Strike 是存在的
        temp_contract = Option(stock.symbol, exp, exchange="SMART")
        try:
            details = await ib.reqContractDetailsAsync(temp_contract)
        except Exception as e:
            logger.warning(f"获取合约详情失败 ({exp}): {e}")
            continue

        if not details:
            continue

        valid_contracts = [d.contract for d in details]
        
        # 分离 Call 和 Put
        calls = [c for c in valid_contracts if c.right == 'C']
        puts = [c for c in valid_contracts if c.right == 'P']
        
        if not calls or not puts:
            continue
            
        # 找 ATM Call
        best_call = min(calls, key=lambda c: abs(c.strike - price))
        strike_candidate = best_call.strike
        
        # 找对应的 Put
        best_put = next((p for p in puts if p.strike == strike_candidate), None)
        
        if best_call and best_put:
            call = best_call
            put = best_put
            expiry = exp
            strike = strike_candidate
            break
    
    if not call or not put:
        logger.error("无法找到匹配的 Straddle 合约")
        return None

    # 获取数据
    call_p, call_iv = await get_option_greeks(ib, call)
    put_p, put_iv = await get_option_greeks(ib, put)
    avg_iv = (call_iv + put_iv) / 2
    
    total_cost = (call_p + put_p) * 100 * NUM_CONTRACTS
    
    action = "SELL" if direction == "short" else "BUY"
    contracts_sign = -1 if direction == "short" else 1
    
    if SIMULATION_MODE:
        logger.info(f"[模拟] {action} Call+Put @ {strike} ({expiry}), IV={avg_iv:.1%}, 总价=${total_cost:.2f}")
    else:
        # 下单
        c_order = MarketOrder(action, NUM_CONTRACTS)
        p_order = MarketOrder(action, NUM_CONTRACTS)
        
        c_trade = ib.placeOrder(call, c_order)
        p_trade = ib.placeOrder(put, p_order)
        
        # 简单的等待逻辑
        MAX_WAIT = 10
        for _ in range(MAX_WAIT):
            if c_trade.isDone() and p_trade.isDone():
                break
            await asyncio.sleep(1)
            
        logger.info(f"✅ 订单提交完成: {action} Straddle")
        
    return VolatilityPosition(
        symbol=SYMBOL,
        strike_call=strike,
        strike_put=strike,
        expiry=expiry,
        contracts=contracts_sign * NUM_CONTRACTS,
        entry_iv=avg_iv,
        entry_price=call_p + put_p,
        current_value=total_cost,
        entry_date=datetime.now().strftime("%Y-%m-%d")
    )



async def close_position(ib: IB, stock: Stock, position: VolatilityPosition, reason: str):
    """平仓"""
    logger.info(f"🔻 正在平仓 ({reason})...")
    
    if SIMULATION_MODE:
        logger.info(f"[模拟] 已平仓, 释放持仓")
        clear_position()
        return

    # 构造合约
    call = Option(position.symbol, position.expiry, position.strike_call, "C", "SMART")
    put = Option(position.symbol, position.expiry, position.strike_put, "P", "SMART")
    await ib.qualifyContractsAsync(call)
    await ib.qualifyContractsAsync(put)
    
    qty = abs(position.contracts)
    # 平仓方向与持仓方向相反
    action = "BUY" if position.contracts < 0 else "SELL"
    
    c_order = MarketOrder(action, qty)
    p_order = MarketOrder(action, qty)
    
    c_trade = ib.placeOrder(call, c_order)
    p_trade = ib.placeOrder(put, p_order)
    
    while not (c_trade.isDone() and p_trade.isDone()):
        await asyncio.sleep(1)
        
    logger.info("✅ 平仓完成")
    clear_position()


async def close_all_positions(ib: IB):
    """一键平仓"""
    print("\n🔥 一键平仓模式")
    await cancel_all_option_orders(ib, SYMBOL)
    pos = await load_position_from_ibkr(ib, SYMBOL)
    if pos:
        stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
        await ib.qualifyContractsAsync(stock)
        await close_position(ib, stock, pos, "一键平仓指令")
    else:
        print("📭 未检测到相关持仓")
        clear_position()


def print_status_report(state: StrategyState, action: str, reason: str):
    """打印状态报告"""
    pos = state.position
    hv = state.hv_20d
    iv = state.current_iv
    
    print("\n" + "=" * 60)
    print(f"📊 波动率策略状态报告 - {SYMBOL}")
    print(f"⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    print(f"\n【市场状态】")
    print(f"  当前价格: ${state.current_price:.2f}")
    print(f"  历史波动率 (HV20): {hv:.1%}")
    print(f"  隐含波动率 (IV):   {iv:.1%}")
    if hv > 0:
        print(f"  IV/HV 比率: {iv/hv:.2f}x")
    
    # 简单的 IV 计量条
    bar_len = 40
    iv_ratio = min(iv / 0.60, 1.0) # 假设 60% IV 满格
    filled = int(iv_ratio * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)
    print(f"  [{bar}]")
    print(f"  Low: {IV_LOW_THRESHOLD:.0%} | High: {IV_HIGH_THRESHOLD:.0%}")
    
    if pos:
        print(f"\n【持仓详情】")
        type_str = "Short Straddle (做空波动率)" if pos.contracts < 0 else "Long Straddle (做多波动率)"
        print(f"  类型: {type_str}")
        print(f"  数量: {abs(pos.contracts)} 张")
        print(f"  行权: Call ${pos.strike_call} / Put ${pos.strike_put}")
        print(f"  到期: {pos.expiry} ({pos.get_days_to_expiry()}天)")
        print(f"  建仓 IV: {pos.entry_iv:.1%}")
        
        # 估算 PnL
        pnl = pos.current_value - (pos.entry_price * abs(pos.contracts) * 100)
        # 如果是 Short，PnL = 卖出得钱 - 当前买回花费
        if pos.contracts < 0:
            pnl = (pos.entry_price * abs(pos.contracts) * 100) - pos.current_value
            
        pnl_pct = 0.0
        cost_basis = pos.entry_price * abs(pos.contracts) * 100
        if cost_basis > 0:
            pnl_pct = pnl / cost_basis
            
        print(f"  当前价值: ${pos.current_value:.2f}")
        print(f"  浮动盈亏: ${pnl:+.2f} ({pnl_pct:+.1%})")
    
    print(f"\n【决策】")
    print(f"  👉 动作: {action}")
    print(f"  📝 原因: {reason}")
    print("=" * 60)


async def run_strategy_check(ib: IB, continuous: bool = False):
    """运行策略检查核心逻辑"""
    logger.info(f"启动检查... 模式={'连续' if continuous else '单次'}")
    
    stock = Stock(SYMBOL, EXCHANGE, CURRENCY)
    stock = (await ib.qualifyContractsAsync(stock))[0]
    
    # 状态初始化
    state = StrategyState()
    
    # 获取历史数据计算 HV
    hist_prices = await get_historical_prices(ib, stock, IV_LOOKBACK_DAYS + 10)
    if hist_prices:
        state.price_history = hist_prices
        state.hv_20d = calculate_historical_volatility(hist_prices, IV_LOOKBACK_DAYS)
    
    while True:
        # 1. 基础数据更新
        state.current_price = await get_stock_price(ib, stock)
        
        # 2. 获取持仓 (优先从 IBKR 加载)
        state.position = await load_position_from_ibkr(ib, SYMBOL)
        if not state.position and SIMULATION_MODE:
             # 模拟模式下如果没有真实持仓，尝试加载本地模拟持仓
             state.position = load_local_position()
        
        # 3. 获取 ATM IV
        # 为了获取 IV，如果是持仓状态，用持仓的 Option；否则找 ATM
        iv_sample = 0.0
        if state.position:
            # 更新持仓价值
            call = Option(state.position.symbol, state.position.expiry, state.position.strike_call, "C", "SMART")
            put = Option(state.position.symbol, state.position.expiry, state.position.strike_put, "P", "SMART")
            await ib.qualifyContractsAsync(call)
            await ib.qualifyContractsAsync(put)
            
            cp, civ = await get_option_greeks(ib, call)
            pp, piv = await get_option_greeks(ib, put)
            iv_sample = (civ + piv) / 2
            
            state.position.current_value = (cp + pp) * 100 * abs(state.position.contracts)
        else:
            # 无持仓，找 ATM 估算当前 IV
            chains = await ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)
            if chains:
                chain = next((c for c in chains if c.exchange == "SMART"), chains[0])
                # 找下个月的
                valid_exp = [e for e in chain.expirations if e > datetime.now().strftime("%Y%m%d")]
                if valid_exp:
                    exp = valid_exp[1] if len(valid_exp) > 1 else valid_exp[0]
                    strike = min(chain.strikes, key=lambda x: abs(x - state.current_price))
                    atm_opt = Option(SYMBOL, exp, strike, "C", "SMART")
                    await ib.qualifyContractsAsync(atm_opt)
                    _, iv_sample = await get_option_greeks(ib, atm_opt)
        
        state.current_iv = iv_sample
        
        # 4. 决策逻辑
        action = "HOLD"
        reason = "观察中"
        
        if state.position:
            # 持仓管理
            days = state.position.get_days_to_expiry()
            
            # 计算 PnL Pct
            cost = state.position.entry_price * abs(state.position.contracts) * 100
            if state.position.contracts < 0: # Short
                pnl = cost - state.position.current_value
            else: # Long
                pnl = state.position.current_value - cost
            
            pnl_pct = pnl / cost if cost > 0 else 0
            
            entry_iv = state.position.entry_iv
            
            # 修复：如果从 IBKR 加载的仓位没有 Entry IV (为0)，则重置为当前 IV
            # 避免 entry_iv=0 导致 exit 逻辑 (current > 0*1.2) 误触发
            if entry_iv == 0.0 and state.current_iv > 0:
                logger.warning(f"⚠️ 缺失 Entry IV，重置为当前 IV: {state.current_iv:.1%} 以继续监控")
                state.position.entry_iv = state.current_iv
                entry_iv = state.current_iv
                save_position(state.position)
            
            # 止损
            if pnl_pct < -STOP_LOSS_PCT:
                action = "CLOSE"
                reason = f"触发止损 ({pnl_pct:.1%})"
            # 到期
            elif days <= 1:
                action = "CLOSE"
                reason = "临近到期"
            # 止盈 (基于 IV 回归)
            elif state.position.contracts < 0: # Short Straddle (盼 IV 跌)
                if state.current_iv < entry_iv * 0.8:
                    action = "CLOSE"
                    reason = f"IV 显著回落 ({entry_iv:.1%} -> {state.current_iv:.1%})"
            elif state.position.contracts > 0: # Long Straddle (盼 IV 涨)
                if state.current_iv > entry_iv * 1.2:
                    action = "CLOSE"
                    reason = f"IV 显著上升 ({entry_iv:.1%} -> {state.current_iv:.1%})"
            
            # 执行平仓
            if action == "CLOSE":
                await close_position(ib, stock, state.position, reason)
                state.position = None
            
        else:
            # 开仓逻辑
            if state.current_iv > IV_HIGH_THRESHOLD:
                action = "OPEN_SHORT"
                reason = f"IV {state.current_iv:.1%} > {IV_HIGH_THRESHOLD:.1%} (偏高)"
                new_pos = await open_straddle(ib, stock, "short", state.current_price)
                if new_pos:
                    save_position(new_pos)
                    
            elif state.current_iv < IV_LOW_THRESHOLD:
                action = "OPEN_LONG"
                reason = f"IV {state.current_iv:.1%} < {IV_LOW_THRESHOLD:.1%} (偏低)"
                new_pos = await open_straddle(ib, stock, "long", state.current_price)
                if new_pos:
                    save_position(new_pos)

        # 5. 报告
        print_status_report(state, action, reason)
        
        if not continuous:
            break
            
        await asyncio.sleep(CHECK_INTERVAL_SEC)


async def main():
    ib = await connect_ib()
    
    try:
        if RUN_MODE == "close_all":
            await close_all_positions(ib)
        elif RUN_MODE == "continuous":
            await run_strategy_check(ib, continuous=True)
        else: 
            await run_strategy_check(ib, continuous=False)
            
    except KeyboardInterrupt:
        print("\n🛑 用户手动停止")
    except Exception as e:
        logger.error(f"发生错误: {e}", exc_info=True)
    finally:
        ib.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
