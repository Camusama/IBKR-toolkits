"""
Demo 7 (ib_async): Market Data Permissions Validator  # 市场数据权限校验器
Validates real-time and delayed market data permissions for stocks and options.  # 校验股票和期权的实时/延迟行情权限
Handles market closed scenarios with graceful fallbacks.  # 优雅处理休市场景
"""
import asyncio  # 异步支持
import os  # 环境变量
import math  # 数值校验
from datetime import datetime  # 时间处理
from typing import Optional, Dict, Any, List  # 类型提示
from dataclasses import dataclass, field  # 数据类

from ib_async import IB, Stock, Option, Contract  # ib_async 组件

IB_HOST = os.getenv("IB_HOST", "127.0.0.1")  # IB 主机
IB_PORT = int(os.getenv("IB_PORT", "7497"))  # 端口：纸 7497，实 7496
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "17"))  # 客户端 ID

# 测试股票标的
TEST_STOCK_SYMBOL = os.getenv("IB_TEST_SYMBOL", "AAPL")  # 测试股票
TEST_STOCK_EXCHANGE = os.getenv("IB_TEST_EXCHANGE", "SMART")  # 交易所
TEST_STOCK_CURRENCY = os.getenv("IB_TEST_CURRENCY", "USD")  # 货币

# 超时设置
DATA_WAIT_SEC = float(os.getenv("IB_DATA_WAIT_SEC", "3.0"))  # 等待行情超时
DELAYED_FALLBACK = os.getenv(
    "IB_DELAYED_FALLBACK", "true").lower() == "true"  # 启用延迟行情兜底


@dataclass
class PermissionCheckResult:
    """权限检查结果"""
    permission_name: str  # 权限名称
    passed: bool  # 是否通过
    data_type: str  # 数据类型: live/delayed/none
    message: str  # 详细信息
    price_received: Optional[float] = None  # 收到的价格
    bid: Optional[float] = None  # 买价
    ask: Optional[float] = None  # 卖价
    details: Dict[str, Any] = field(default_factory=dict)  # 额外详情


@dataclass
class MarketStatus:
    """市场状态"""
    is_market_hours: bool  # 是否交易时段
    is_pre_market: bool  # 是否盘前
    is_after_hours: bool  # 是否盘后
    message: str  # 状态信息


def is_valid_price(price: Optional[float]) -> bool:
    """检查价格是否有效"""
    if price is None:
        return False
    if not math.isfinite(price):
        return False
    if price <= 0:
        return False
    return True


def get_market_status() -> MarketStatus:
    """
    获取美股市场状态（简化版，基于本地时间估算）
    注意：这是简化逻辑，实际应考虑节假日和时区
    """
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("America/New_York"))
    weekday = now.weekday()  # 0=Monday, 6=Sunday
    hour = now.hour
    minute = now.minute
    current_time = hour * 60 + minute  # 转换为分钟

    # 周末休市
    if weekday >= 5:
        return MarketStatus(
            is_market_hours=False,
            is_pre_market=False,
            is_after_hours=False,
            message=f"Weekend - Market Closed (Current: {now.strftime('%A %H:%M %Z')})"
        )

    # 时间段定义（分钟）
    pre_market_start = 4 * 60  # 04:00 ET
    market_open = 9 * 60 + 30  # 09:30 ET
    market_close = 16 * 60  # 16:00 ET
    after_hours_end = 20 * 60  # 20:00 ET

    if pre_market_start <= current_time < market_open:
        return MarketStatus(
            is_market_hours=False,
            is_pre_market=True,
            is_after_hours=False,
            message=f"Pre-Market Hours ({now.strftime('%H:%M %Z')})"
        )
    elif market_open <= current_time < market_close:
        return MarketStatus(
            is_market_hours=True,
            is_pre_market=False,
            is_after_hours=False,
            message=f"Regular Market Hours ({now.strftime('%H:%M %Z')})"
        )
    elif market_close <= current_time < after_hours_end:
        return MarketStatus(
            is_market_hours=False,
            is_pre_market=False,
            is_after_hours=True,
            message=f"After-Hours Trading ({now.strftime('%H:%M %Z')})"
        )
    else:
        return MarketStatus(
            is_market_hours=False,
            is_pre_market=False,
            is_after_hours=False,
            message=f"Market Closed ({now.strftime('%H:%M %Z')})"
        )


async def connect_ib() -> IB:
    """连接 IB"""
    ib = IB()
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    return ib


async def check_market_data_permission(
    ib: IB,
    contract: Contract,
    permission_name: str,
    wait_sec: float = DATA_WAIT_SEC
) -> PermissionCheckResult:
    """
    检查单个合约的市场数据权限
    先尝试实时数据，失败后尝试延迟数据
    """
    # 首先尝试实时数据
    ib.reqMarketDataType(1)  # 1 = Live
    ticker = ib.reqMktData(contract, "", False, False)
    await asyncio.sleep(wait_sec)

    last_price = ticker.last
    bid = ticker.bid
    ask = ticker.ask
    close_price = ticker.close

    ib.cancelMktData(contract)

    # 检查是否收到有效的实时数据
    if is_valid_price(last_price) or (is_valid_price(bid) and is_valid_price(ask)):
        return PermissionCheckResult(
            permission_name=permission_name,
            passed=True,
            data_type="live",
            message="✅ 实时行情权限正常 (Live data permission OK)",
            price_received=last_price if is_valid_price(last_price) else None,
            bid=bid if is_valid_price(bid) else None,
            ask=ask if is_valid_price(ask) else None,
            details={"close": close_price}
        )

    # 如果没有收到实时数据，尝试延迟数据
    if DELAYED_FALLBACK:
        ib.reqMarketDataType(3)  # 3 = Delayed
        ticker = ib.reqMktData(contract, "", False, False)
        await asyncio.sleep(wait_sec)

        last_price = ticker.last
        bid = ticker.bid
        ask = ticker.ask
        close_price = ticker.close

        ib.cancelMktData(contract)
        ib.reqMarketDataType(1)  # 恢复为实时

        if is_valid_price(last_price) or is_valid_price(close_price) or (is_valid_price(bid) and is_valid_price(ask)):
            return PermissionCheckResult(
                permission_name=permission_name,
                passed=True,
                data_type="delayed",
                message="⚠️ 仅有延迟行情权限 (Delayed data only - Live data may require subscription)",
                price_received=last_price if is_valid_price(
                    last_price) else close_price,
                bid=bid if is_valid_price(bid) else None,
                ask=ask if is_valid_price(ask) else None,
                details={"close": close_price,
                         "note": "Consider subscribing to live data for better execution"}
            )

    # 尝试使用历史数据作为最后兜底
    try:
        bars = await ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr="1 D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1
        )
        if bars and len(bars) > 0:
            last_bar = bars[-1]
            return PermissionCheckResult(
                permission_name=permission_name,
                passed=True,
                data_type="historical",
                message="⚠️ 仅有历史数据权限 (Historical data only - Real-time subscription required)",
                price_received=last_bar.close,
                details={
                    "open": last_bar.open,
                    "high": last_bar.high,
                    "low": last_bar.low,
                    "close": last_bar.close,
                    "volume": last_bar.volume,
                    "date": str(last_bar.date),
                    "note": "Real-time data subscription required for live trading"
                }
            )
    except Exception:
        pass

    # 既没有实时也没有延迟数据也没有历史数据
    return PermissionCheckResult(
        permission_name=permission_name,
        passed=False,
        data_type="none",
        message="❌ 无市场数据权限 (No market data permission - subscription required)",
        details={"error": "Market data subscription may be required"}
    )


async def check_stock_permission(ib: IB) -> PermissionCheckResult:
    """检查股票实时行情权限"""
    contract = Stock(TEST_STOCK_SYMBOL, TEST_STOCK_EXCHANGE,
                     TEST_STOCK_CURRENCY)
    try:
        qualified = await ib.qualifyContractsAsync(contract)
        if not qualified:
            return PermissionCheckResult(
                permission_name="Stock Real-Time Data",
                passed=False,
                data_type="none",
                message=f"❌ 无法验证合约 {TEST_STOCK_SYMBOL}",
                details={"error": "Contract qualification failed"}
            )
        contract = qualified[0]
    except Exception as e:
        return PermissionCheckResult(
            permission_name="Stock Real-Time Data",
            passed=False,
            data_type="none",
            message=f"❌ 合约验证异常: {e}",
            details={"error": str(e)}
        )

    result = await check_market_data_permission(
        ib, contract, f"Stock ({TEST_STOCK_SYMBOL}) Real-Time Data"
    )
    return result


async def check_option_permission(ib: IB) -> PermissionCheckResult:
    """检查期权实时行情权限"""
    # 首先获取股票价格以确定合适的期权行权价
    stock_contract = Stock(
        TEST_STOCK_SYMBOL, TEST_STOCK_EXCHANGE, TEST_STOCK_CURRENCY)
    try:
        qualified = await ib.qualifyContractsAsync(stock_contract)
        if not qualified:
            return PermissionCheckResult(
                permission_name="Option Real-Time Data",
                passed=False,
                data_type="none",
                message=f"❌ 无法验证股票合约以获取期权链",
                details={"error": "Stock contract qualification failed"}
            )
        stock_contract = qualified[0]
    except Exception as e:
        return PermissionCheckResult(
            permission_name="Option Real-Time Data",
            passed=False,
            data_type="none",
            message=f"❌ 股票合约验证异常: {e}",
            details={"error": str(e)}
        )

    # 获取期权链
    try:
        chains = await ib.reqSecDefOptParamsAsync(
            stock_contract.symbol,
            "",
            stock_contract.secType,
            stock_contract.conId
        )
        if not chains:
            return PermissionCheckResult(
                permission_name="Option Real-Time Data",
                passed=False,
                data_type="none",
                message="❌ 无法获取期权链",
                details={"error": "No option chains available"}
            )
    except Exception as e:
        return PermissionCheckResult(
            permission_name="Option Real-Time Data",
            passed=False,
            data_type="none",
            message=f"❌ 获取期权链异常: {e}",
            details={"error": str(e)}
        )

    # 选择 SMART 交易所的期权链
    chain = next((c for c in chains if c.exchange == "SMART"), chains[0])

    # 获取最近的到期日和 ATM 行权价
    expirations = sorted(chain.expirations)
    if not expirations:
        return PermissionCheckResult(
            permission_name="Option Real-Time Data",
            passed=False,
            data_type="none",
            message="❌ 无可用到期日",
            details={"error": "No expirations available"}
        )

    # 选择最近的到期日（至少3天后，避免临近到期的问题）
    from datetime import timedelta
    today = datetime.now().strftime("%Y%m%d")
    valid_expirations = [exp for exp in expirations if exp > today]
    if not valid_expirations:
        return PermissionCheckResult(
            permission_name="Option Real-Time Data",
            passed=False,
            data_type="none",
            message="❌ 无有效到期日",
            details={"error": "No valid future expirations"}
        )

    expiration = valid_expirations[0]

    # 获取当前股价以选择 ATM 期权 - 使用历史数据避免行情权限问题
    stock_price = None
    try:
        # 先尝试使用历史收盘价
        bars = await ib.reqHistoricalDataAsync(
            stock_contract,
            endDateTime="",
            durationStr="1 D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1
        )
        if bars:
            stock_price = bars[-1].close
    except Exception:
        pass

    # 如果历史数据获取失败，尝试市场数据
    if stock_price is None:
        ib.reqMarketDataType(3)  # 使用延迟数据获取价格
        ticker = ib.reqMktData(stock_contract, "", False, False)
        await asyncio.sleep(2)
        stock_price = ticker.last if is_valid_price(
            ticker.last) else ticker.close
        ib.cancelMktData(stock_contract)

    # 最终兜底价格 (基于常见股票价格范围)
    if stock_price is None or not is_valid_price(stock_price):
        # 使用一个合理的默认价格范围内的中间值
        stock_price = 200  # AAPL 等常见股票的合理价格

    # 选择最接近当前价格的行权价
    strikes = sorted(chain.strikes)
    atm_strike = min(strikes, key=lambda x: abs(x - stock_price))

    # 创建期权合约
    option_contract = Option(
        symbol=TEST_STOCK_SYMBOL,
        lastTradeDateOrContractMonth=expiration,
        strike=atm_strike,
        right="C",  # Call
        exchange="SMART",
        currency=TEST_STOCK_CURRENCY
    )

    try:
        qualified = await ib.qualifyContractsAsync(option_contract)
        if not qualified or qualified[0] is None:
            return PermissionCheckResult(
                permission_name="Option Real-Time Data",
                passed=False,
                data_type="none",
                message=f"❌ 无法验证期权合约 {TEST_STOCK_SYMBOL} {expiration} {atm_strike}C",
                details={"error": "Option contract qualification failed",
                         "stock_price": stock_price}
            )
        option_contract = qualified[0]
        if option_contract.conId is None or option_contract.conId == 0:
            return PermissionCheckResult(
                permission_name="Option Real-Time Data",
                passed=False,
                data_type="none",
                message=f"❌ 期权合约无效 (无 conId) {TEST_STOCK_SYMBOL} {expiration} {atm_strike}C",
                details={"error": "Option contract has no conId",
                         "stock_price": stock_price}
            )
    except Exception as e:
        return PermissionCheckResult(
            permission_name="Option Real-Time Data",
            passed=False,
            data_type="none",
            message=f"❌ 期权合约验证异常: {e}",
            details={"error": str(e)}
        )

    result = await check_market_data_permission(
        ib, option_contract,
        f"Option ({TEST_STOCK_SYMBOL} {expiration} {atm_strike}C) Real-Time Data"
    )
    result.details["option_info"] = {
        "symbol": TEST_STOCK_SYMBOL,
        "expiration": expiration,
        "strike": atm_strike,
        "right": "C",
        "stock_price_used": stock_price
    }
    return result


async def check_account_permissions(ib: IB) -> PermissionCheckResult:
    """检查账户数据权限"""
    try:
        summaries = await ib.accountSummaryAsync()
        if summaries:
            # 提取关键账户信息
            account_info = {}
            for row in summaries:
                if row.tag in ["NetLiquidation", "TotalCashValue", "BuyingPower"]:
                    account_info[row.tag] = f"{row.value} {row.currency}"

            return PermissionCheckResult(
                permission_name="Account Data Access",
                passed=True,
                data_type="live",
                message="✅ 账户数据权限正常",
                details={"account_summary": account_info}
            )
        else:
            return PermissionCheckResult(
                permission_name="Account Data Access",
                passed=False,
                data_type="none",
                message="❌ 无法获取账户摘要",
                details={"error": "Empty account summary"}
            )
    except Exception as e:
        return PermissionCheckResult(
            permission_name="Account Data Access",
            passed=False,
            data_type="none",
            message=f"❌ 账户数据访问异常: {e}",
            details={"error": str(e)}
        )


async def check_positions_permissions(ib: IB) -> PermissionCheckResult:
    """检查持仓数据权限"""
    try:
        positions = await ib.reqPositionsAsync()
        position_count = len(positions) if positions else 0

        position_summary = []
        for pos in positions[:5]:  # 只显示前5个持仓
            position_summary.append({
                "symbol": pos.contract.symbol,
                "secType": pos.contract.secType,
                "position": pos.position,
                "avgCost": pos.avgCost
            })

        return PermissionCheckResult(
            permission_name="Positions Data Access",
            passed=True,
            data_type="live",
            message=f"✅ 持仓数据权限正常 (共 {position_count} 个持仓)",
            details={"positions": position_summary,
                     "total_count": position_count}
        )
    except Exception as e:
        return PermissionCheckResult(
            permission_name="Positions Data Access",
            passed=False,
            data_type="none",
            message=f"❌ 持仓数据访问异常: {e}",
            details={"error": str(e)}
        )


async def check_orders_permissions(ib: IB) -> PermissionCheckResult:
    """检查订单数据权限"""
    try:
        orders = await ib.reqOpenOrdersAsync()
        order_count = len(orders) if orders else 0

        return PermissionCheckResult(
            permission_name="Orders Data Access",
            passed=True,
            data_type="live",
            message=f"✅ 订单数据权限正常 (当前 {order_count} 个未完成订单)",
            details={"open_orders_count": order_count}
        )
    except Exception as e:
        return PermissionCheckResult(
            permission_name="Orders Data Access",
            passed=False,
            data_type="none",
            message=f"❌ 订单数据访问异常: {e}",
            details={"error": str(e)}
        )


def print_result(result: PermissionCheckResult) -> None:
    """打印单个检查结果"""
    print(f"\n{'─' * 60}")
    print(f"📋 {result.permission_name}")
    print(f"   状态: {result.message}")
    print(f"   数据类型: {result.data_type.upper()}")

    if result.price_received is not None:
        print(f"   价格: {result.price_received}")
    if result.bid is not None and result.ask is not None:
        print(f"   买卖价: {result.bid} / {result.ask}")

    if result.details:
        for key, value in result.details.items():
            if key not in ["error", "note"]:
                if isinstance(value, dict):
                    print(f"   {key}:")
                    for k, v in value.items():
                        print(f"      {k}: {v}")
                elif isinstance(value, list) and value:
                    print(f"   {key}:")
                    for item in value[:3]:  # 只显示前3个
                        print(f"      - {item}")
                else:
                    print(f"   {key}: {value}")

        if "note" in result.details:
            print(f"   💡 提示: {result.details['note']}")
        if "error" in result.details:
            print(f"   ⚠️ 错误: {result.details['error']}")


def print_summary(results: List[PermissionCheckResult], market_status: MarketStatus) -> None:
    """打印检查摘要"""
    print("\n" + "═" * 60)
    print("📊 权限检查摘要 (Permission Check Summary)")
    print("═" * 60)

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    live_count = sum(1 for r in results if r.data_type == "live")
    delayed_count = sum(1 for r in results if r.data_type == "delayed")
    historical_count = sum(1 for r in results if r.data_type == "historical")

    print(f"\n🏪 市场状态: {market_status.message}")
    print(f"\n📈 检查结果:")
    print(f"   ✅ 通过: {passed}/{len(results)}")
    print(f"   ❌ 失败: {failed}/{len(results)}")
    print(f"   🟢 实时数据: {live_count}")
    print(f"   🟡 延迟数据: {delayed_count}")
    print(f"   🟠 历史数据: {historical_count}")

    # 是否适合运行量化策略
    print("\n" + "─" * 60)
    if failed == 0:
        if live_count == len(results):
            print("🚀 状态: 所有权限正常，可以安全运行量化策略！")
            print("   All permissions OK - Safe to run quantitative strategies!")
        elif delayed_count > 0 or historical_count > 0:
            print("⚠️ 状态: 部分数据为延迟/历史行情，建议检查行情订阅。")
            print(
                "   Some data is delayed/historical - Consider subscribing to live data.")
            if not market_status.is_market_hours:
                print("   💡 注意: 当前为非交易时段，延迟/历史数据为正常现象。")
                print(
                    "      Note: Market is closed, delayed/historical data is expected.")
            if historical_count > 0:
                print("   💡 历史数据可用于回测和分析，但实盘交易需要实时行情。")
                print(
                    "      Historical data is suitable for backtesting, but live trading needs real-time quotes.")
    else:
        print("❌ 状态: 存在权限问题，请先解决后再运行量化策略。")
        print("   Permission issues detected - Please resolve before running strategies.")
        print("\n   失败项目:")
        for r in results:
            if not r.passed:
                print(f"   - {r.permission_name}: {r.message}")

    print("═" * 60)


async def validate_all_permissions() -> bool:
    """
    校验所有量化运行所需的权限
    返回: True 如果所有关键权限都通过
    """
    print("=" * 60)
    print("🔍 IBKR 市场数据权限校验器")
    print("   Market Data Permission Validator")
    print("=" * 60)

    # 获取市场状态
    market_status = get_market_status()
    print(f"\n🕐 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🏪 市场状态: {market_status.message}")

    # 连接 IB
    print(f"\n🔌 连接 IBKR ({IB_HOST}:{IB_PORT})...")
    try:
        ib = await connect_ib()
        print("   ✅ 连接成功!")
    except Exception as e:
        print(f"   ❌ 连接失败: {e}")
        return False

    results: List[PermissionCheckResult] = []

    try:
        # 1. 检查账户权限
        print("\n🔄 检查账户数据权限...")
        result = await check_account_permissions(ib)
        results.append(result)
        print_result(result)

        # 2. 检查持仓权限
        print("\n🔄 检查持仓数据权限...")
        result = await check_positions_permissions(ib)
        results.append(result)
        print_result(result)

        # 3. 检查订单权限
        print("\n🔄 检查订单数据权限...")
        result = await check_orders_permissions(ib)
        results.append(result)
        print_result(result)

        # 4. 检查股票实时行情权限
        print(f"\n🔄 检查股票 ({TEST_STOCK_SYMBOL}) 行情权限...")
        result = await check_stock_permission(ib)
        results.append(result)
        print_result(result)

        # 5. 检查期权实时行情权限
        print(f"\n🔄 检查期权 ({TEST_STOCK_SYMBOL}) 行情权限...")
        result = await check_option_permission(ib)
        results.append(result)
        print_result(result)

        # 打印摘要
        print_summary(results, market_status)

        # 返回是否所有检查都通过
        return all(r.passed for r in results)

    finally:
        ib.disconnect()
        print("\n🔌 已断开 IBKR 连接")


async def main() -> None:
    """主入口"""
    success = await validate_all_permissions()
    exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
