"""
通用仓位管理模块 - 支持每日定时运行

提供功能：
1. 仓位持久化（JSON 文件存储）
2. 智能调仓决策
3. 单次运行模式支持

使用方法：
    from position_manager import PositionManager, AdjustmentAction

    pm = PositionManager("iron_condor", SYMBOL)
    state = pm.load_state()

    if state:
        action = pm.check_adjustment(state, current_price, current_iv)
        if action == AdjustmentAction.TAKE_PROFIT:
            ...
    else:
        pm.save_state(new_state)
"""
import os
import json
import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any
from dataclasses import asdict

logger = logging.getLogger(__name__)

# 状态文件存储目录
STATE_DIR = os.path.join(os.path.dirname(__file__), ".states")


class AdjustmentAction(Enum):
    """调仓动作"""
    HOLD = "hold"                    # 持有不动
    TAKE_PROFIT = "take_profit"      # 止盈平仓
    STOP_LOSS = "stop_loss"          # 止损平仓
    ROLL_OUT = "roll_out"            # 展期（近期到期）
    ROLL_UP = "roll_up"              # 上移行权价（上涨趋势）
    ROLL_DOWN = "roll_down"          # 下移行权价（下跌趋势）
    DELTA_ADJUST = "delta_adjust"    # Delta 调整
    CLOSE_EXPIRED = "close_expired"  # 到期平仓
    OPEN_NEW = "open_new"            # 开新仓


class PositionManager:
    """仓位管理器"""

    def __init__(self, strategy_name: str, symbol: str):
        self.strategy_name = strategy_name
        self.symbol = symbol
        self.state_file = os.path.join(
            STATE_DIR, f"{strategy_name}_{symbol.lower()}.json")

        # 确保目录存在
        os.makedirs(STATE_DIR, exist_ok=True)

    def load_state(self) -> Optional[Dict[str, Any]]:
        """加载仓位状态"""
        if not os.path.exists(self.state_file):
            logger.info(f"无现有仓位: {self.state_file}")
            return None

        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                logger.info(f"加载仓位: {state.get('position', {})}")
                return state
        except Exception as e:
            logger.error(f"加载状态失败: {e}")
            return None

    def save_state(self, state: Dict[str, Any]):
        """保存仓位状态"""
        state['last_updated'] = datetime.now().isoformat()
        state['strategy'] = self.strategy_name
        state['symbol'] = self.symbol

        try:
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2, default=str)
            logger.info(f"保存仓位: {self.state_file}")
        except Exception as e:
            logger.error(f"保存状态失败: {e}")

    def clear_state(self):
        """清除仓位状态（平仓后调用）"""
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
            logger.info(f"清除仓位: {self.state_file}")

    def get_days_to_expiry(self, expiry: str) -> int:
        """计算到期剩余天数"""
        try:
            expiry_date = datetime.strptime(expiry, "%Y%m%d")
            return (expiry_date.date() - datetime.now().date()).days
        except:
            return 999


def check_iron_condor_adjustment(
    current_price: float,
    short_put_strike: float,
    short_call_strike: float,
    entry_price: float,
    pnl_pct: float,
    days_to_expiry: int,
    profit_target: float = 0.50,
    stop_loss: float = 1.0,
    roll_days: int = 5,
    danger_zone_pct: float = 0.02
) -> AdjustmentAction:
    """
    Iron Condor 调仓决策

    参数:
        current_price: 当前股价
        short_put_strike: 卖出 Put 行权价
        short_call_strike: 卖出 Call 行权价
        entry_price: 建仓时股价
        pnl_pct: 当前盈亏比例
        days_to_expiry: 距到期天数
        profit_target: 止盈目标 (0.50 = 50%)
        stop_loss: 止损阈值 (1.0 = 100%)
        roll_days: 展期天数阈值
        danger_zone_pct: 危险区域比例

    返回:
        AdjustmentAction
    """
    # 1. 止盈检查
    if pnl_pct >= profit_target:
        logger.info(f"✅ 达到止盈目标 {pnl_pct:.1%}")
        return AdjustmentAction.TAKE_PROFIT

    # 2. 止损检查
    if pnl_pct <= -stop_loss:
        logger.info(f"🛑 触发止损 {pnl_pct:.1%}")
        return AdjustmentAction.STOP_LOSS

    # 3. 到期检查
    if days_to_expiry <= 0:
        logger.info("⏰ 期权已到期")
        return AdjustmentAction.CLOSE_EXPIRED

    # 4. 展期检查
    if days_to_expiry <= roll_days:
        logger.info(f"📅 距到期 {days_to_expiry} 天，建议展期")
        return AdjustmentAction.ROLL_OUT

    # 5. 价格危险区域检查
    # 如果价格接近卖出期权行权价，需要调整
    put_danger = short_put_strike * (1 + danger_zone_pct)
    call_danger = short_call_strike * (1 - danger_zone_pct)

    if current_price <= put_danger:
        logger.warning(f"⚠️ 价格接近 Put 行权价 ${short_put_strike:.2f}")
        return AdjustmentAction.ROLL_DOWN

    if current_price >= call_danger:
        logger.warning(f"⚠️ 价格接近 Call 行权价 ${short_call_strike:.2f}")
        return AdjustmentAction.ROLL_UP

    return AdjustmentAction.HOLD


def check_butterfly_adjustment(
    current_price: float,
    middle_strike: float,
    pnl_pct: float,
    days_to_expiry: int,
    profit_target: float = 0.50,
    stop_loss: float = 0.80,
    roll_days: int = 3
) -> AdjustmentAction:
    """
    Butterfly 调仓决策

    Butterfly 是精准策略，调仓机会较少
    """
    if pnl_pct >= profit_target:
        return AdjustmentAction.TAKE_PROFIT

    if pnl_pct <= -stop_loss:
        return AdjustmentAction.STOP_LOSS

    if days_to_expiry <= 0:
        return AdjustmentAction.CLOSE_EXPIRED

    if days_to_expiry <= roll_days:
        # Butterfly 一般不展期，直接平仓
        return AdjustmentAction.TAKE_PROFIT

    # 如果价格大幅偏离中点，考虑止损
    distance_pct = abs(current_price - middle_strike) / middle_strike
    if distance_pct > 0.05:  # 偏离 5%
        logger.warning(f"价格偏离中点 {distance_pct:.1%}")
        # 但不立即止损，等待回归

    return AdjustmentAction.HOLD


def check_calendar_adjustment(
    current_price: float,
    strike: float,
    pnl_pct: float,
    days_to_front_expiry: int,
    days_to_back_expiry: int,
    profit_target: float = 0.30,
    stop_loss: float = 0.50
) -> AdjustmentAction:
    """
    Calendar Spread 调仓决策

    关键点：近期期权到期前必须处理
    """
    if pnl_pct >= profit_target:
        return AdjustmentAction.TAKE_PROFIT

    if pnl_pct <= -stop_loss:
        return AdjustmentAction.STOP_LOSS

    # 近期期权到期
    if days_to_front_expiry <= 1:
        logger.info("近期期权即将到期，需要展期或平仓")
        return AdjustmentAction.ROLL_OUT

    # 价格偏离
    distance_pct = abs(current_price - strike) / strike
    if distance_pct > 0.05:
        logger.warning(f"价格偏离行权价 {distance_pct:.1%}")
        if pnl_pct < 0:
            return AdjustmentAction.STOP_LOSS

    return AdjustmentAction.HOLD


def check_strangle_adjustment(
    current_price: float,
    put_strike: float,
    call_strike: float,
    direction: str,  # "long" or "short"
    pnl_pct: float,
    days_to_expiry: int,
    profit_target: float = 0.50,
    stop_loss: float = 0.50
) -> AdjustmentAction:
    """
    Strangle 调仓决策

    Long: 等待突破
    Short: 防止突破
    """
    if pnl_pct >= profit_target:
        return AdjustmentAction.TAKE_PROFIT

    if pnl_pct <= -stop_loss:
        return AdjustmentAction.STOP_LOSS

    if days_to_expiry <= 0:
        return AdjustmentAction.CLOSE_EXPIRED

    if direction == "short":
        # 做空波动率，价格突破需要调整
        if current_price < put_strike or current_price > call_strike:
            logger.warning("⚠️ 价格突破！做空方需要止损")
            return AdjustmentAction.STOP_LOSS

        # 接近危险区域
        margin = (call_strike - put_strike) * 0.1
        if current_price < put_strike + margin:
            return AdjustmentAction.ROLL_DOWN
        if current_price > call_strike - margin:
            return AdjustmentAction.ROLL_UP

    if days_to_expiry <= 5 and direction == "short":
        # 短期内平仓收割 theta
        if pnl_pct > 0.2:
            return AdjustmentAction.TAKE_PROFIT

    return AdjustmentAction.HOLD


def check_ratio_spread_adjustment(
    current_price: float,
    long_strike: float,
    short_strike: float,
    pnl_pct: float,
    days_to_expiry: int,
    profit_target: float = 0.50,
    stop_loss: float = 0.50
) -> AdjustmentAction:
    """
    Ratio Spread 调仓决策

    关键：监控上方风险（裸卖期权）
    """
    if pnl_pct >= profit_target:
        return AdjustmentAction.TAKE_PROFIT

    if pnl_pct <= -stop_loss:
        return AdjustmentAction.STOP_LOSS

    if days_to_expiry <= 0:
        return AdjustmentAction.CLOSE_EXPIRED

    # 上方风险！价格超过卖出行权价
    if current_price > short_strike:
        logger.warning(
            f"⚠️ 价格 ${current_price:.2f} 超过卖出行权价 ${short_strike:.2f}！")
        # 超过 3% 必须止损
        if current_price > short_strike * 1.03:
            return AdjustmentAction.STOP_LOSS
        return AdjustmentAction.ROLL_UP

    # 接近最大盈利点
    if abs(current_price - short_strike) / short_strike < 0.01:
        if pnl_pct > 0.3:
            logger.info("接近最大盈利点，建议止盈")
            return AdjustmentAction.TAKE_PROFIT

    return AdjustmentAction.HOLD


def format_adjustment_report(
    strategy: str,
    symbol: str,
    action: AdjustmentAction,
    pnl: float,
    pnl_pct: float,
    days_to_expiry: int,
    details: dict
) -> str:
    """生成调仓报告"""
    report = []
    report.append("=" * 60)
    report.append(f"📋 {strategy} 每日检查报告 - {symbol}")
    report.append(f"⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append("=" * 60)

    report.append(f"📊 当前盈亏: ${pnl:+.2f} ({pnl_pct:+.1%})")
    report.append(f"📅 距到期: {days_to_expiry} 天")

    for key, value in details.items():
        report.append(f"   {key}: {value}")

    report.append("-" * 60)

    action_map = {
        AdjustmentAction.HOLD: "✅ 建议动作: 继续持有",
        AdjustmentAction.TAKE_PROFIT: "💰 建议动作: 止盈平仓",
        AdjustmentAction.STOP_LOSS: "🛑 建议动作: 止损平仓",
        AdjustmentAction.ROLL_OUT: "📅 建议动作: 展期（延后到期日）",
        AdjustmentAction.ROLL_UP: "⬆️ 建议动作: 上移行权价",
        AdjustmentAction.ROLL_DOWN: "⬇️ 建议动作: 下移行权价",
        AdjustmentAction.DELTA_ADJUST: "⚖️ 建议动作: 调整 Delta",
        AdjustmentAction.CLOSE_EXPIRED: "⏰ 建议动作: 到期平仓",
        AdjustmentAction.OPEN_NEW: "🆕 建议动作: 开立新仓",
    }

    report.append(action_map.get(action, f"❓ 未知动作: {action}"))
    report.append("=" * 60)

    return "\n".join(report)
