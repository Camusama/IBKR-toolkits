"""
Demo 3 (async-friendly): offline MA crossover backtest using yfinance data.  # 示例说明
Runs without IB connectivity; useful for quick iteration.  # 无需 IB，可快速迭代
"""
import asyncio  # 异步支持
import os  # 环境变量
from typing import Tuple  # 类型提示

import pandas as pd  # 数据处理
import yfinance as yf  # 拉取行情

SYMBOL = os.getenv("YF_SYMBOL", "AAPL")  # 标的
PERIOD = os.getenv("YF_PERIOD", "60d")  # 历史区间
INTERVAL = os.getenv("YF_INTERVAL", "5m")  # K 线周期


async def fetch_yf_history() -> pd.DataFrame:  # 异步获取 yfinance 数据
    return await asyncio.to_thread(  # 在线程池执行同步下载
        yf.download, SYMBOL, period=PERIOD, interval=INTERVAL, progress=False
    )


def ma_crossover(df: pd.DataFrame, fast: int = 10, slow: int = 30) -> pd.DataFrame:  # 均线交叉
    out = df.copy()  # 复制数据
    out["fast"] = out["close"].rolling(fast).mean()  # 快线
    out["slow"] = out["close"].rolling(slow).mean()  # 慢线
    out["signal"] = 0  # 初始信号
    out.loc[out["fast"] > out["slow"], "signal"] = 1  # 多头
    out.loc[out["fast"] < out["slow"], "signal"] = -1  # 空头
    out["position"] = out["signal"].shift(1).fillna(0)  # 用前一根确定持仓
    out["ret"] = out["close"].pct_change().fillna(0)  # 价格收益
    out["strategy_ret"] = out["position"] * out["ret"]  # 策略收益
    out["equity"] = (1 + out["strategy_ret"]).cumprod()  # 权益曲线
    return out  # 返回结果


def compute_stats(df: pd.DataFrame) -> Tuple[float, float, float]:  # 绩效指标
    equity = df["equity"]  # 权益
    total_ret = equity.iloc[-1] - 1  # 总收益
    daily_factor = 252  # 年化因子
    vol = df["strategy_ret"].std() * (daily_factor**0.5)  # 年化波动
    sharpe = (df["strategy_ret"].mean() * daily_factor) / \
        vol if vol else 0.0  # 夏普
    max_dd = (equity / equity.cummax() - 1).min()  # 最大回撤
    return total_ret, max_dd, sharpe  # 返回指标


async def main() -> None:  # 主入口
    df = await fetch_yf_history()  # 拉数据
    df.index.name = "date"  # 索引命名
    df.rename(  # 统一列名
        columns={
            "Close": "close",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Volume": "volume",
        },
        inplace=True,
    )
    if df.empty:  # 无数据则报错
        raise RuntimeError(
            "No data returned by yfinance; adjust SYMBOL/period/interval.")
    result = ma_crossover(df, fast=10, slow=30)  # 跑策略
    total_ret, max_dd, sharpe = compute_stats(result)  # 计算指标
    print(f"Total return: {total_ret:.2%}")  # 打印收益
    print(f"Max drawdown: {max_dd:.2%}")  # 打印回撤
    print(f"Sharpe (rough): {sharpe:.2f}")  # 打印夏普
    print(result.tail()[["close", "fast", "slow",
          "signal", "position", "equity"]])  # 打印末尾


if __name__ == "__main__":  # 脚本入口
    asyncio.run(main())  # 运行
