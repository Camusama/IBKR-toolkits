"""
Demo 6 (ib_async): list open orders and optionally cancel selected ones.
Run on paper/live gateway with proper permissions.
"""

import asyncio
import os
from typing import List

from ib_async import IB, Position

IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
# 对齐 demo5，便于查看同一 clientId 的挂单
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "15"))
IB_FETCH_ALL_OPEN = os.getenv(
    "IB_FETCH_ALL_OPEN", "true").lower() == "true"  # 是否请求所有客户端的挂单


async def connect_ib() -> IB:
    ib = IB()
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    return ib


async def fetch_open_trades(ib: IB) -> List:
    # 请求并刷新挂单；可选跨 clientId
    trades: List = []
    if IB_FETCH_ALL_OPEN:
        trades.extend(await ib.reqAllOpenOrdersAsync())
    else:
        trades.extend(await ib.reqOpenOrdersAsync())
    # openTrades 比 reqOpenOrders 更快更新（当前 client），合并去重
    existing_ids = {t.order.orderId for t in trades}
    for t in ib.openTrades():
        if t.order.orderId not in existing_ids:
            trades.append(t)
            existing_ids.add(t.order.orderId)
    return trades


async def fetch_positions(ib: IB) -> List[Position]:
    await ib.reqPositionsAsync()
    return ib.positions()


def print_open_trades(trades: List) -> None:
    if not trades:
        print("No open orders.")
        return
    print("Open orders:")
    for idx, trade in enumerate(trades):
        o = trade.order
        c = trade.contract
        status = trade.orderStatus.status
        price = getattr(o, "lmtPrice", None) or getattr(
            o, "auxPrice", None) or ""
        print(
            f"[{idx}] id={o.orderId} {o.action} {o.totalQuantity} {getattr(c, 'symbol', '')} "
            f"type={o.orderType} tif={getattr(o, 'tif', 'DAY')} price={price} status={status}"
        )


def print_positions(positions: List[Position]) -> None:
    if not positions:
        print("No positions.")
        return
    print("Positions:")
    for pos in positions:
        c = pos.contract
        print(
            f"  {getattr(c, 'symbol', '')} {pos.position} @ {pos.avgCost} "
            f"acct={pos.account}"
        )


def parse_selection(max_idx: int, raw: str) -> List[int]:
    raw = raw.strip()
    if not raw:
        return []
    picks: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            val = int(part)
        except ValueError:
            print(f"Skip invalid entry: {part}")
            continue
        if 0 <= val < max_idx:
            picks.append(val)
        else:
            print(f"Skip out-of-range index: {val}")
    # remove duplicates while preserving order
    seen = set()
    uniq: List[int] = []
    for v in picks:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


async def cancel_selected(ib: IB, trades: List, indices: List[int]) -> None:
    if not indices:
        print("No cancellation requested.")
        return
    for idx in indices:
        trade = trades[idx]
        ib.cancelOrder(trade.order)
        print(f"Sent cancel for orderId={trade.order.orderId}")
    await asyncio.sleep(1)  # allow cancel responses
    for idx in indices:
        status = trades[idx].orderStatus.status
        print(f"Order [{idx}] cancel result: {status}")


async def main() -> None:
    ib = await connect_ib()
    try:
        trades = await fetch_open_trades(ib)
        positions = await fetch_positions(ib)
        print_open_trades(trades)
        print_positions(positions)
        if trades:
            raw = await asyncio.to_thread(
                input, "Enter indices to cancel (comma-separated, blank to skip): "
            )
            indices = parse_selection(len(trades), raw)
            await cancel_selected(ib, trades, indices)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
