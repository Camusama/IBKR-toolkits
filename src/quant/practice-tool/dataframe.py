"""
DataFrame 基础用例（渐进式）。示例均可直接运行。
df 是 pandas 的 DataFrame（二维表格，类似 Excel/SQL 结果集），print(df) 时 pandas 会以行列形式对齐输出：
使用 DataFrame 的 __repr__ 生成表格化字符串。
"""
import pandas as pd


def demo_basic_creation():
    # 模拟 IB bars 结构的 OHLCV（开高低收量），索引为日期
    dates = pd.date_range("2024-01-01", periods=6, freq="D")
    df = pd.DataFrame(
        {
            "open": [100, 101, 102, 101, 103, 104],
            "high": [101, 102, 103, 102, 104, 105],
            "low": [99, 100, 101, 100, 102, 103],
            "close": [100.5, 101.2, 102.8, 101.5, 103.6, 104.1],
            "volume": [10_000, 12_500, 11_000, 13_000, 15_000, 14_000],
        },
        index=dates,
    )
    df.index.name = "date"  # 与 bars 相似的时间索引
    # pct_change (percentage change) 计算相邻行的涨跌幅；
    # fillna(0) (fill null with 0) 将首行的缺失收益填为 0
    df["ret"] = df["close"].pct_change().fillna(0)  # 简单收益
    print("=== demo_basic_creation ===")
    print(df)
    return df


def demo_filter_and_slice(df: pd.DataFrame):
    # 条件过滤与行切片：筛出成交量大于 12,000 的行；取前两行
    # df[...] 为布尔过滤；head(n) 取前 n 行
    high_vol = df[df["volume"] > 12_000]  # 成交量过滤
    head_two = df.head(2)  # 前两行
    print("=== demo_filter_and_slice ===")
    print(high_vol)
    print(head_two)
    return high_vol, head_two


def demo_rolling_and_ewm(df: pd.DataFrame):
    # rolling(n)：定义一个长度为 n 的滑动窗口。对窗口内的值再调用 mean/sum 等聚合。
    # ewm(span=2)：指数加权移动（Exponential Weighted Moving）。
    # 窗口不是固定截断，而是所有历史点都参与，只是越近权重越大、越远越小，权重按指数衰减。
    # 数据序列假设为[close_1, close_2, close_3] = [100, 102, 101]，则：
    # EMA_1 = 100（首个点直接取自身）
    # EMA_2 = alpha * close_2 + (1 - alpha) * EMA_1  alpha = 2/(2+1) = 0.6667）
    # = 0.6667 * 102 + 0.3333 * 100 = 101.33
    # EMA_3 = 0.6667 * 101 + 0.3333 * 101.33 = 101.11
    df = df.copy()
    # rolling(2).mean()：滑动窗口均值，窗口长度 2；用于简易均线
    df["ma2"] = df["close"].rolling(2).mean()  # 2 期均线
    # ewm(span=2).mean()：exponential weighted mean（指数加权均值），span 越小越敏感
    df["ema2"] = df["close"].ewm(span=2).mean()  # 2 期 EMA
    print("=== demo_rolling_and_ewm ===")
    print(df[["close", "ma2", "ema2"]])
    return df


def demo_resample(df: pd.DataFrame):
    # 按时间重采样（日线 -> 周线），对 OHLCV 做常见聚合
    # resample("W") 按周分箱；agg aggregate 聚合，映射列到聚合函数
    weekly = df.resample("W").agg(
        {
            "open": "first",   # 周开盘
            "high": "max",     # 周最高
            "low": "min",      # 周最低
            "close": "last",   # 周收盘
            "volume": "sum",   # 周成交量
        }
    )
    print("=== demo_resample ===")
    print(weekly)
    return weekly


def demo_groupby(df: pd.DataFrame):
    # 分组统计：为示例分配多个标的，汇总均价与成交量
    # groupby + agg 可对不同列应用不同聚合
    df = df.copy()
    df["symbol"] = ["A", "A", "B", "B", "A", "B"]  # 与 base 行数对应
    grouped = df.groupby("symbol").agg({"close": "mean", "volume": "sum"})
    print("=== demo_groupby ===")
    print(grouped)
    return grouped


def demo_merge():
    # 合并示例（左连接）：价格表左连接基本面表
    # merge(..., how="left") 类似 SQL LEFT JOIN
    prices = pd.DataFrame({"symbol": ["A", "B"], "close": [10, 20]})
    fundamentals = pd.DataFrame({"symbol": ["A", "B"], "pe": [15.0, 12.5]})
    merged = prices.merge(fundamentals, on="symbol", how="left")
    print("=== demo_merge ===")
    print(merged)
    return merged


def demo_missing_values():
    # 缺失值处理：ffill 向前填补空值；dropna 直接丢弃有空值的行
    # ffill = forward fill；dropna 删除包含 NaN 的行
    df = pd.DataFrame({"price": [1.0, None, 3.0], "volume": [10, 11, None]})
    df_ffill = df.ffill()  # 向前填充
    df_drop = df.dropna()  # 直接丢弃
    print("=== demo_missing_values ===")
    print("ffill:\n", df_ffill)
    print("dropna:\n", df_drop)
    return df_ffill, df_drop


def demo_apply():
    # apply 自定义列：按价格打标签
    # apply 对列逐元素应用函数（非矢量化时谨慎性能）
    df = pd.DataFrame({"price": [100, 105, 95]})
    df["tag"] = df["price"].apply(lambda x: "high" if x >= 100 else "low")
    print("=== demo_apply ===")
    print(df)
    return df


def demo_cumprod():
    # 累积乘积示例：将收益序列转为权益曲线
    # cumprod 计算前缀乘积，这里 1+ret 连乘得到权益曲线
    df = pd.DataFrame({"ret": [0.01, -0.02, 0.03]})
    df["equity"] = (1 + df["ret"]).cumprod()
    print("=== demo_cumprod ===")
    print(df)
    return df


def main():
    base = demo_basic_creation()  # 基础创建与简单收益
    # 下列用例基于 base（与 IB bars 形态一致）
    # demo_filter_and_slice(base)
    # demo_rolling_and_ewm(base)
    # demo_resample(base)
    # demo_groupby(base)
    # demo_merge()
    # demo_missing_values()
    # demo_apply()
    demo_cumprod()


if __name__ == "__main__":
    main()
