"""
Demo 4 (ib_async): subscribe to live market data for a symbol and stream a few updates.  # 示例说明
Run on paper/live gateway with market data permissions.  # 需行情权限
"""
import asyncio  # 异步
import os  # 环境变量
import time  # 计时
from typing import Optional  # 可选类型

from ib_async import IB, Stock  # ib_async 组件

IB_HOST = os.getenv("IB_HOST", "127.0.0.1")  # 主机
IB_PORT = int(os.getenv("IB_PORT", "7497"))  # 端口
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "14"))  # 客户端 ID

SYMBOL = os.getenv("IB_SYMBOL", "AAPL")  # 标的
EXCHANGE = os.getenv("IB_EXCHANGE", "SMART")  # 交易所
CURRENCY = os.getenv("IB_CURRENCY", "USD")  # 货币

DURATION_SEC = int(os.getenv("STREAM_DURATION", "10"))  # 流式时长


async def connect_ib() -> IB:  # 连接 IB
    ib = IB()  # 实例
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)  # 异步连接
    return ib  # 返回


async def stream_quotes(ib: IB, duration_sec: int = DURATION_SEC) -> None:  # 流式行情
    contract = Stock(SYMBOL, EXCHANGE, CURRENCY)  # 合约
    # ensure conId populated
    contract = (await ib.qualifyContractsAsync(contract))[0]
    ticker = ib.reqMktData(contract, "", False, False)  # 订阅
    # 提示
    print(f"Subscribed to {SYMBOL}. Streaming for {duration_sec} seconds ...")

    start = time.time()  # 开始时间
    last_print: Optional[float] = None  # 上次打印价格
    try:
        while time.time() - start < duration_sec:  # 循环直到超时
            await asyncio.sleep(0.5)  # 间隔
            if ticker.last != last_print and ticker.last is not None:  # 新价格
                last_print = ticker.last  # 更新
                print(  # 打印行情
                    f"Last: {ticker.last} | bid: {ticker.bid} x {ticker.bidSize} | "
                    f"ask: {ticker.ask} x {ticker.askSize}"
                )
    finally:
        ib.cancelMktData(contract)  # 取消订阅
        print("Unsubscribed.")  # 提示


async def main() -> None:  # 主入口
    ib = await connect_ib()  # 连接
    try:
        await stream_quotes(ib, duration_sec=DURATION_SEC)  # 流式
    finally:
        ib.disconnect()  # 断开


if __name__ == "__main__":  # 脚本入口
    asyncio.run(main())  # 运行
