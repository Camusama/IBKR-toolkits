"""
Demo 5 (ib_async): paper trade example with order status logging.  # 示例说明
Defaults to a small market order on paper gateway. Change parameters cautiously.  # 默认小单，谨慎修改
"""
import asyncio  # 异步
import os  # 环境变量
import math  # 数值校验
from typing import Optional  # 可选类型

from ib_async import IB, Stock, LimitOrder, MarketOrder  # ib_async 组件

IB_HOST = os.getenv("IB_HOST", "127.0.0.1")  # 主机
IB_PORT = int(os.getenv("IB_PORT", "7497"))  # 端口：纸 7497，实 7497
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "15"))  # 客户端 ID
IB_MARKET_DATA_TYPE = int(
    os.getenv("IB_MARKET_DATA_TYPE", "1"))  # 1 live, 3 delayed
IB_FALLBACK_PRICE_ENV = os.getenv("IB_FALLBACK_PRICE", "280")  # 无行情时的手动价格
AUTO_CANCEL_PENDING = os.getenv(
    "IB_AUTO_CANCEL_PENDING", "false").lower() == "true"  # 是否自动撤未成交单

SYMBOL = os.getenv("IB_SYMBOL", "AAPL")  # 标的
EXCHANGE = os.getenv("IB_EXCHANGE", "SMART")  # 交易所
CURRENCY = os.getenv("IB_CURRENCY", "USD")  # 货币

ORDER_ACTION = os.getenv("IB_ORDER_ACTION", "BUY")  # 买卖方向
ORDER_TYPE = os.getenv("IB_ORDER_TYPE", "LMT")  # 订单类型 MKT/LMT
ORDER_QTY = float(os.getenv("IB_ORDER_QTY", "1"))  # 数量
LIMIT_OFFSET = float(os.getenv("IB_LIMIT_OFFSET", "0.02"))  # 限价偏移
RESTING_OFFSET = float(os.getenv("IB_RESTING_OFFSET", "0.05"))  # 挂单偏移
RESTING_QTY = float(os.getenv("IB_RESTING_QTY", str(ORDER_QTY)))  # 挂单数量


async def connect_ib() -> IB:  # 连接 IB
    ib = IB()  # 实例
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)  # 异步连接
    ib.reqMarketDataType(IB_MARKET_DATA_TYPE)  # 设置行情类型
    return ib  # 返回


def build_contract() -> Stock:  # 构建合约
    return Stock(SYMBOL, EXCHANGE, CURRENCY)  # 返回合约


def _action_upper(action: str) -> str:
    return action.upper()


def _validate_limit_price(price: float, context: str) -> float:
    if price is None or not math.isfinite(price) or price <= 0:
        raise RuntimeError(
            f"Invalid limit price ({price}) for {context}; check offsets or fallback price.")
    return price


async def fetch_last_price(ib: IB, contract: Stock, wait_sec: float = 1.0) -> Optional[float]:
    def _read_price():
        price = ticker.last or ticker.close
        if price is not None and not math.isfinite(price):
            return None
        return price

    # 首次尝试当前行情类型
    ticker = ib.reqMktData(contract, "", False, False)
    await asyncio.sleep(wait_sec)
    price = _read_price()
    ib.cancelMktData(contract)
    if price is not None:
        return price

    # 若未取到且当前为实时，则尝试延迟行情
    if IB_MARKET_DATA_TYPE == 1:
        ib.reqMarketDataType(3)  # 切换到延迟
        ticker = ib.reqMktData(contract, "", False, False)
        await asyncio.sleep(wait_sec)
        price = _read_price()
        ib.cancelMktData(contract)
        # 恢复原设定
        ib.reqMarketDataType(IB_MARKET_DATA_TYPE)
    return price


def parse_fallback_price() -> Optional[float]:
    if not IB_FALLBACK_PRICE_ENV:
        return None
    try:
        val = float(IB_FALLBACK_PRICE_ENV)
        return val if math.isfinite(val) else None
    except ValueError:
        return None


def print_fills(trade) -> None:
    if not trade.fills:
        print("No fills recorded.")
        return
    print("Fills:")
    for f in trade.fills:
        exe = f.execution
        print(
            f"  time={exe.time} price={exe.price} qty={exe.shares} "
            f"avg={exe.avgPrice} exchange={exe.exchange}"
        )


async def place_primary_order(
    ib: IB, contract: Stock, order_type: str, action: str, qty: float
):
    action_up = _action_upper(action)
    if order_type.upper() == "LMT":  # 限价单
        last_price = await fetch_last_price(ib, contract)
        if last_price is None:
            fallback = parse_fallback_price()
            if fallback is None:
                raise RuntimeError(
                    "No market data to derive limit price and no IB_FALLBACK_PRICE provided.")
            last_price = fallback
        limit_price = last_price * \
            (1 - LIMIT_OFFSET) if action_up == "BUY" else last_price * \
            (1 + LIMIT_OFFSET)
        limit_price = _validate_limit_price(limit_price, "primary order")
        order = LimitOrder(action_up, qty, round(limit_price, 2))  # 构建限价单
    else:  # 市价单
        order = MarketOrder(action_up, qty)  # 构建市价单

    trade = ib.placeOrder(contract, order)  # 下单
    print(f"Submitted {order_type} {action} {qty} {SYMBOL}.")  # 打印提交
    return trade


async def place_resting_limit_order(
    ib: IB, contract: Stock, action: str, qty: float, offset: float
):
    action_up = _action_upper(action)
    last_price = await fetch_last_price(ib, contract)
    if last_price is None:
        fallback = parse_fallback_price()
        if fallback is None:
            raise RuntimeError(
                "No market data to derive limit price for resting order and no IB_FALLBACK_PRICE provided.")
        last_price = fallback
    price = last_price * \
        (1 - offset) if action_up == "BUY" else last_price * (1 + offset)
    price = _validate_limit_price(price, "resting order")
    order = LimitOrder(action_up, qty, round(price, 2))
    trade = ib.placeOrder(contract, order)
    print(
        f"Placed resting LMT {action_up} {qty} {SYMBOL} @ {order.lmtPrice} "
        f"(offset {offset:.2%}); orderId={trade.order.orderId}"
    )
    return trade


async def submit_order(
    ib: IB, order_type: str = ORDER_TYPE, action: str = ORDER_ACTION, qty: float = ORDER_QTY
) -> None:  # 提交订单
    contract = build_contract()  # 合约
    contract = (await ib.qualifyContractsAsync(contract))[0]  # 合约认证并返回含 conId

    trade = await place_primary_order(ib, contract, order_type, action, qty)

    # Wait briefly for fills; cancel if still pending to keep the demo clean.  # 等待成交，未成则撤
    await asyncio.sleep(2)  # 等待
    status = trade.orderStatus.status  # 状态
    filled = trade.orderStatus.filled  # 成交量
    print(f"Status: {status} | filled: {filled}")  # 输出状态
    print_fills(trade)  # 成交明细
    if status not in {"Filled", "Cancelled"}:  # 未成交
        if AUTO_CANCEL_PENDING:
            ib.cancelOrder(trade.order)  # 撤单
            await asyncio.sleep(1)  # 等待撤单
            print(
                f"Cancelled outstanding order; final status: {trade.orderStatus.status}")
        else:
            print("Pending order left open (AUTO_CANCEL_PENDING=false).")

    # Place an additional resting limit order to demonstrate an open order.
    try:
        await place_resting_limit_order(
            ib, contract, action, RESTING_QTY, RESTING_OFFSET
        )
    except Exception as exc:
        print(f"Skip placing resting limit order: {exc}")


async def main() -> None:  # 主入口
    ib = await connect_ib()  # 连接
    try:
        await submit_order(ib)  # 提交订单
    finally:
        ib.disconnect()  # 断开


if __name__ == "__main__":  # 脚本入口
    asyncio.run(main())  # 运行
