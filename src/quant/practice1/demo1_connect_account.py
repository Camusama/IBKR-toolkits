"""
IBKR demo 1 (ib_async): connect to IB Gateway/TWS and print account summary and positions.  # 示例说明
This is read-only; no orders are sent.  # 仅查询不下单
"""
import asyncio  # 异步支持
import os  # 环境变量

from ib_async import IB  # ib_async 客户端

IB_HOST = os.getenv("IB_HOST", "127.0.0.1")  # IB 主机
IB_PORT = int(os.getenv("IB_PORT", "7497"))  # 端口：纸 7497，实 7497
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "11"))  # 客户端 ID


async def connect_ib() -> IB:  # 连接 IB
    ib = IB()  # 创建实例
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)  # 异步连接
    return ib  # 返回连接


async def print_account_and_positions(ib: IB) -> None:  # 打印账户与持仓
    print("=== Account Summary ===")  # 标题
    summaries = await ib.accountSummaryAsync()  # 账户摘要
    for row in summaries:  # 遍历摘要
        print(f"{row.tag}: {row.value} {row.currency}")  # 输出键值

    print("\n=== Open Positions ===")  # 标题
    positions = await ib.reqPositionsAsync()  # 请求持仓
    if not positions:  # 如果无持仓
        print("No open positions.")  # 提示
    for pos in positions:  # 遍历持仓
        contract = pos.contract  # 合约信息
        print(  # 打印持仓详情
            f"{contract.symbol} {contract.secType} @ {contract.exchange} | "
            f"position {pos.position} | avgCost {pos.avgCost}"
        )


async def main() -> None:  # 主入口
    ib = await connect_ib()  # 连接
    try:
        await print_account_and_positions(ib)  # 打印信息
    finally:
        ib.disconnect()  # 断开


if __name__ == "__main__":  # 脚本入口
    asyncio.run(main())  # 运行
