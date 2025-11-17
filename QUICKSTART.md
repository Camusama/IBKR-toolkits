# 快速开始指南

## 5 分钟上手 IBKR Toolkit

### 第一步：安装依赖

```bash
# 使用 uv（推荐）
uv sync

# 或使用 pip
pip install -e .
```

### 第二步：启动 IB Gateway 或 TWS

1. 打开 **IB Gateway** 或 **Trader Workstation (TWS)**
2. 登录您的账户（**建议先使用模拟账户**）
3. 启用 API 连接：
   - 点击右上角 **齿轮图标 ⚙️** (Settings)
   - 选择 `API` → `Settings`
   - ✅ 勾选 "Enable ActiveX and Socket Clients"
   - 📝 确认端口号：
     - **IB Gateway 模拟账户**: `4002` ✅ **推荐**
     - **IB Gateway 实盘账户**: `4001` ⚠️ **谨慎**
     - TWS 模拟账户: `7497`
     - TWS 实盘账户: `7496` ⚠️ **谨慎**
   - ✅ 勾选 "Allow connections from localhost only"
   - 添加信任 IP: `127.0.0.1`

### 第三步：配置环境变量（可选）

项目已配置默认使用 **IB Gateway 模拟账户** (端口 4002)。

如需修改配置，编辑 `.env` 文件：

```bash
# 查看当前配置
cat .env

# 如果使用 TWS 模拟账户，修改端口
echo "IBKR_PORT=7497" >> .env

# 如果有多个账户，可以指定默认账户（可选）
# 不指定则自动使用第一个可用账户
echo "IBKR_ACCOUNT=U1231231" >> .env

# 或者直接编辑
nano .env
```

### 第四步：运行示例

```bash
# 运行示例脚本，查看完整演示
python example.py
```

或者直接获取持仓：

```bash
# 查看持仓和Greeks数据
uv run scripts/fetch_positions_with_greeks.py

# 指定账户
uv run scripts/fetch_positions_with_greeks.py --account U1234567

# 自定义Greeks等待时间（默认15秒）
uv run scripts/fetch_positions_with_greeks.py --wait-greeks 20
```

**🆕 获取账户摘要（含总盈亏）：**

```bash
# 获取账户总价值、盈亏等关键信息
uv run python scripts/fetch_account_summary.py

# 导出为 JSON
uv run python scripts/fetch_account_summary.py --format json

# 指定账户
uv run python scripts/fetch_account_summary.py --account U1234567
```

输出示例：
```
ACCOUNT SUMMARY
======================================================================
Account: DU123456
Currency: USD
----------------------------------------------------------------------
Net Liquidation (Total Value): $52,341.28
Net Deposits (Est.):            $45,000.00
Total P&L:                      $7,341.28
Total P&L %:                    16.31%
----------------------------------------------------------------------
Cash Balance:                   $12,500.00
Stock Market Value:             $38,500.00
Option Market Value:            $1,341.28
----------------------------------------------------------------------
Unrealized P&L:                 $5,200.00
Realized P&L:                   $2,141.28
Buying Power:                   $65,000.00
======================================================================
```

> **💡 提示**: IBKR实时API不提供历史入金数据。推荐使用 `--net-deposits` 参数手动输入实际充值金额：
> 
> ```bash
> uv run python scripts/fetch_account_summary.py --net-deposits 95000
> ```
> 
> 如何查找实际入金：IBKR客户端 → 关键数据 → 开户以来 → 查看总入金
> 
> 详见：[docs/FLEX_QUERY_SETUP.md](docs/FLEX_QUERY_SETUP.md)

### 第五步：查看导出的数据

```bash
# 查看 data 目录
ls -lh data/

# 打开 CSV 文件
open data/positions_*.csv  # macOS
# 或
cat data/positions_*.csv   # Linux
```

## 常见问题排查

### ❌ 连接失败

**错误**: `Connection refused` 或 `连接超时`

**解决方案**:

1. 确认 TWS/Gateway 已启动并登录
2. 检查 API 连接已启用（见第二步）
3. 确认端口号正确：
   - 模拟账户: `7497`
   - 实盘账户: `7496`（谨慎！）
4. 检查防火墙是否阻止连接

### ℹ️ 没有持仓数据

这是正常的，如果：

- 账户是新开的模拟账户
- 还没有进行任何模拟交易

**解决方案**:

1. 在 TWS 中进行一些模拟交易
2. 或使用有持仓的账户

### ⚠️ Excel 导出失败

**错误**: `导出 Excel 需要安装 pandas`

**解决方案**:

```bash
pip install pandas openpyxl
```

## 在代码中使用

### 最简示例

```python
from ibkr_toolkit import IBKRClient, PortfolioService

# 连接并获取持仓（自动使用默认账户）
with IBKRClient() as client:
    service = PortfolioService(client)

    # 获取默认账户
    account = client.get_default_account()

    # 获取持仓
    summary = service.get_position_summary(account=account)

    if summary:
        print(f"总持仓: {summary.total_positions}")
        print(f"总盈亏: ${summary.total_pnl:.2f}")
```

### 完整示例

查看 `example.py` 文件，包含：

- ✅ 连接管理
- ✅ 账户查询
- ✅ 持仓获取
- ✅ 数据导出
- ✅ 错误处理

## 下一步

- 📖 阅读完整 [README.md](README.md)
- 🔍 查看源代码文档
- 🚀 根据需求扩展功能
- 📊 集成飞书多维表格（即将支持）

## 安全提醒

> ⚠️ 本工具仅包含**只读查询功能**，不支持任何交易操作。
>
> ✅ 建议先在**模拟账户**上测试所有功能。

---

**祝你使用愉快！** 🎉

如有问题，请查看 [README.md](README.md) 或提交 Issue。
