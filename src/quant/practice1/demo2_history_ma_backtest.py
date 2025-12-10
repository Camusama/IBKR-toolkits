"""
IBKR demo 2 (ib_async): fetch historical bars and run a simple MA crossover backtest.  # 示例说明
Requires market data permissions for the chosen symbol.  # 需要行情权限
"""
import asyncio  # 异步支持
import os  # 读取环境变量
from typing import Tuple  # 类型提示

import pandas as pd  # 数据处理
from ib_async import IB, Stock, util  # ib_async 组件

IB_HOST = os.getenv("IB_HOST", "127.0.0.1")  # IB 网关主机
IB_PORT = int(os.getenv("IB_PORT", "7497"))  # IB 网关端口
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "12"))  # 客户端 ID

SYMBOL = os.getenv("IB_SYMBOL", "AAPL")  # 标的代码
EXCHANGE = os.getenv("IB_EXCHANGE", "SMART")  # 交易所
CURRENCY = os.getenv("IB_CURRENCY", "USD")  # 货币


async def connect_ib() -> IB:  # 建立 IB 连接
    ib = IB()  # 创建客户端实例
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)  # 异步连接
    return ib  # 返回连接实例


async def fetch_history(ib: IB, days: int = 180, bar_size: str = "1 day") -> pd.DataFrame:  # 拉取历史数据（默认半年日线）
    contract = Stock(SYMBOL, EXCHANGE, CURRENCY)  # 构建股票合约
    bars = await ib.reqHistoricalDataAsync(  # 异步请求历史数据
        contract=contract,  # 合约
        endDateTime="",  # 结束时间为空表示当前
        durationStr=f"{days} D",  # 持续时长
        barSizeSetting=bar_size,  # K 线周期
        whatToShow="TRADES",  # 成交数据
        useRTH=False,  # 包含盘前盘后
        formatDate=1,  # 日期格式
    )
    df = util.df(bars)  # 转为 DataFrame
    df.set_index("date", inplace=True)  # 设置索引为时间
    return df  # 返回数据


# 均线交叉策略：快线上穿买入做多，快线下穿做空
def ma_crossover(df: pd.DataFrame, fast: int = 10, slow: int = 30) -> pd.DataFrame:
    out = df.copy()  # 拷贝数据
    out["fast"] = out["close"].rolling(fast).mean()  # 计算快线
    out["slow"] = out["close"].rolling(slow).mean()  # 计算慢线
    out["signal"] = 0  # 新增常数列，给所有行设置初值 0（pandas 允许直接赋常数给整列）
    # loc[row_filter, column]：按条件选中行并赋值；快线上穿则 signal=1
    out.loc[out["fast"] > out["slow"], "signal"] = 1  # 快线上穿 => 做多信号
    # 快线下穿则 signal=-1
    out.loc[out["fast"] < out["slow"], "signal"] = -1  # 快线下穿 => 做空/平多信号
    # shift(1) 将信号向下平移 1 行，用上一根 K 的信号作为本根的持仓；fillna(0) 将首行缺失填 0
    # 在当前代码里，ret 是 close 的日涨跌幅（close-to-close），position 是上一根的信号，所以含义是：
    # 在第 t 根 K 生成的信号，用来持有第 t+1 根的区间收益。并不是“当日有 signal 当日 close 买入算第二天收益”，
    # 而是“上一根判断，下一根吃到的 close-to-close 收益”（简化假设：下一根一开盘就拿到头寸，持有到下一根收盘）。
    out["position"] = out["signal"].shift(1).fillna(0)  # 持仓使用前一根信号
    # pct_change() 计算相邻行涨跌幅（此处是日线收益）；fillna(0) 把第一行缺失收益设为 0
    out["ret"] = out["close"].pct_change().fillna(0)  # 单期收益
    out["strategy_ret"] = out["position"] * out["ret"]  # 策略收益
    # cumprod() 累积乘积，将 (1+收益) 连乘得到权益曲线
    out["equity"] = (1 + out["strategy_ret"]).cumprod()  # 权益曲线
    return out  # 返回结果


def compute_stats(df: pd.DataFrame) -> Tuple[float, float, float]:  # 计算绩效指标
    equity = df["equity"]  # 权益序列
    total_ret = equity.iloc[-1] - 1  # 总收益
    daily_factor = 252  # 年化因子
    vol = df["strategy_ret"].std() * (daily_factor**0.5)  # 年化波动
    sharpe = (df["strategy_ret"].mean() * daily_factor) / \
        vol if vol else 0.0  # 夏普
    max_dd = (equity / equity.cummax() - 1).min()  # 最大回撤
    return total_ret, max_dd, sharpe  # 返回指标


async def main() -> None:  # 主入口
    ib = await connect_ib()  # 连接 IB
    try:
        df = await fetch_history(ib, days=180, bar_size="1 day")  # 拉取历史（日线、半年）
        result = ma_crossover(df, fast=10, slow=30)  # 执行策略
        total_ret, max_dd, sharpe = compute_stats(result)  # 计算指标
        print(f"Total return: {total_ret:.2%}")  # 输出总收益
        print(f"Max drawdown: {max_dd:.2%}")  # 输出回撤
        print(f"Sharpe (rough): {sharpe:.2f}")  # 输出夏普
        print(result.tail(10)[["close", "fast", "slow",
              "signal", "position", "equity"]])  # 打印末尾行
    finally:
        ib.disconnect()  # 断开连接


if __name__ == "__main__":  # 脚本入口
    asyncio.run(main())  # 运行协程
