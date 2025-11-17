# IBKR Toolkit

一个用于连接 Interactive Brokers API 的 Python 工具包，提供账户管理、持仓查询、数据导出等功能。

> ⚠️ **安全说明**: 本工具仅提供**只读查询功能**，不包含任何交易下单操作，确保账户安全。

## 功能特性

- ✅ 连接 IBKR TWS/Gateway
- ✅ 获取账户持仓信息
- ✅ 获取账户摘要和总盈亏
- ✅ 支持多种数据导出格式（CSV, JSON, Excel）
- ✅ **Notion 集成** - 自动同步持仓数据到 Notion 数据库
- ✅ 完整的日志记录
- ✅ 模块化设计，易于扩展
- 🔒 只读模式，无交易功能

## 项目结构

```
ibkr-toolkit/
├── src/
│   └── ibkr_toolkit/           # 核心包
│       ├── config/             # 配置管理
│       ├── client/             # IBKR 客户端
│       ├── services/           # 业务服务
│       │   ├── portfolio_service.py
│       │   ├── export_service.py
│       │   ├── notion_service.py    # Notion 集成
│       │   └── market_data_service.py
│       ├── models/             # 数据模型
│       └── utils/              # 工具函数
├── scripts/                    # 执行脚本
│   ├── fetch_positions_with_greeks.py      # 获取持仓和Greeks脚本
│   ├── fetch_account_summary.py # 获取账户摘要脚本
│   ├── sync_positions_with_greeks_to_notion.py  # 同步持仓和Greeks到Notion
│   ├── check_trading_permissions.py  # 检查工具包安全性
│   └── sync_to_notion.py       # 同步到 Notion
├── docs/                       # 文档目录
│   └── NET_DEPOSITS.md         # 净入金获取指南
├── NOTION_SYNC_GUIDE.md        # Notion 集成指南
├── data/                       # 数据导出目录
├── logs/                       # 日志目录
├── pyproject.toml              # 项目配置
└── README.md                   # 项目文档
```

## 安装

### 1. 克隆项目

```bash
git clone <repository-url>
cd ibkr-toolkit
```

### 2. 安装依赖

使用 uv（推荐）：

```bash
uv sync
```

或使用 pip：

```bash
pip install -e .
```

### 3. 配置环境变量（可选）

项目已配置默认使用 **IB Gateway 模拟账户** (端口 4002)，通常无需修改。

如需使用其他端口，编辑 `.env` 文件：

```bash
# IB Gateway Paper Trading (模拟) - 默认配置
IBKR_PORT=4002

# TWS Paper Trading (模拟)
# IBKR_PORT=7497

# IB Gateway Live Trading (实盘) - 谨慎使用
# IBKR_PORT=4001

# TWS Live Trading (实盘) - 谨慎使用
# IBKR_PORT=7496
```

## 使用前准备

### 启动 IB Gateway 或 TWS

1. 打开 **IB Gateway** 或 **TWS**（Trader Workstation）
2. 登录您的账户（**建议先使用模拟账户测试**）
3. 确保 API 连接已启用：
   - 点击右上角 **齿轮图标 ⚙️** → `API` → `Settings`
   - ✅ 勾选 "Enable ActiveX and Socket Clients"
   - 📝 确认端口号：
     - **IB Gateway 模拟**: `4002` ✅ 推荐
     - **IB Gateway 实盘**: `4001` ⚠️ 谨慎
     - TWS 模拟: `7497`
     - TWS 实盘: `7496` ⚠️ 谨慎
   - ✅ 勾选 "Allow connections from localhost only"
   - 添加信任 IP: `127.0.0.1`

## 快速开始

### 1. 获取持仓数据（含 Greeks）

#### 查看持仓和 Greeks 数据

```bash
uv run scripts/fetch_positions_with_greeks.py
```

输出示例：

```
Position Summary:
  - Total positions: 17
  - Total market value: $134,475.36
  - Unrealized P&L: $17,765.27
  - Realized P&L: $0.00
  - Total P&L: $17,765.27
  - P&L percentage: 7.77%

Account Performance (based on net deposits):
  - Net deposits: $100,000.00
  - Total return: $15,000.00
  - Total return %: 15.00%
```

💡 **提示**: 设置环境变量 `NET_DEPOSITS` (账户总入金) 可以查看账户总收益率。

```bash
# 在 .env 文件中配置
NET_DEPOSITS=100000
```

#### 指定账户

```bash
uv run scripts/fetch_positions_with_greeks.py --account DU123456
```

#### 自定义等待时间

```bash
# 自定义Greeks数据等待时间（默认15秒）
uv run scripts/fetch_positions_with_greeks.py --wait-greeks 20
```

### 2. 获取账户摘要（含总盈亏）

获取账户总览信息，包括账户总价值、盈亏等关键指标：

```bash
# 获取账户摘要
uv run python scripts/fetch_account_summary.py

# 导出为 JSON
uv run python scripts/fetch_account_summary.py --format json

# 指定账户
uv run python scripts/fetch_account_summary.py --account DU123456
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

### 3. 同步到 Notion

将持仓数据自动同步到 Notion 数据库，方便在 Notion 中分析和管理。

#### 配置

首先在 `.env` 文件中配置 Notion 凭证：

```bash
# Notion Integration
NOTION_API_KEY=ntn_xxxxxxxxxxxxx
NOTION_NOTES_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

详细配置步骤请参考 [NOTION_SYNC_GUIDE.md](NOTION_SYNC_GUIDE.md)

#### 测试连接

```bash
python -m scripts.sync_to_notion --test
```

#### 初始化数据库结构

首次使用时，初始化 Notion 数据库结构（添加所需列）：

```bash
python -m scripts.sync_to_notion --init
```

#### 同步持仓数据

```bash
# 同步所有持仓
python -m scripts.sync_to_notion

# 同步指定账户
python -m scripts.sync_to_notion --account U1234567
```

输出示例：

```
[INFO] Testing Notion connection...
[INFO] Successfully connected to database: Portfolio Positions
[INFO] Checking database structure...
[INFO] Database structure is up to date
[INFO] Connecting to IBKR...
[INFO] Fetched 17 positions
[INFO] Total Market Value: $134,331.51
[INFO] Total P&L: $17,803.51 (7.79%)
[INFO] Syncing to Notion...
============================================================
SYNC RESULTS
============================================================
[INFO] ✓ Successfully synced: 17 positions
============================================================
```

📖 **完整使用指南：** [NOTION_SYNC_GUIDE.md](NOTION_SYNC_GUIDE.md)

### 4. 同步持仓和期权 Greeks 到 Notion

使用增强版同步脚本，包含期权 Greeks（Delta、Gamma、Theta、Vega）和杠杆分析功能：

```bash
# 同步持仓和Greeks到Notion
uv run scripts/sync_positions_with_greeks_to_notion.py

# 自定义Greeks数据等待时间（默认15秒）
uv run scripts/sync_positions_with_greeks_to_notion.py --wait-greeks 20

# 保留更多历史记录（默认5条）
uv run scripts/sync_positions_with_greeks_to_notion.py --max-records 10
```

**Greeks 缓存功能**

为了在市场关闭时仍能计算杠杆和风险指标，脚本会自动缓存期权 Greeks 数据：

- ✅ 市场开盘时：自动获取最新 Greeks 数据并保存到 `data/greeks_cache.json`
- 📦 市场关闭时：自动从缓存读取 Greeks 数据（最长 48 小时有效）
- 🔄 混合模式：部分期权获取成功时，自动从缓存补充缺失的 Greeks
- 🔁 智能重试：Greeks 获取失败时自动重试，最多等待额外 20 秒
- 🔍 详细日志：显示每个期权的获取状态（成功/失败/从缓存恢复）

**重要提示：**

- 首次使用或有新期权持仓时，建议在**美股交易时间**运行脚本以获取完整 Greeks
- 美股交易时间：美东时间 9:30 AM - 4:00 PM（延迟数据会在收盘后 15-20 分钟内可用）
- 缓存文件位置：`data/greeks_cache.json`

这样即使在周末或非交易时间运行脚本，也能获得完整的持仓分析和杠杆计算。

#### Windows 一键同步（推荐）

在 Windows 10/11 上，双击即可执行批处理脚本：

```batch
# 双击运行
scripts\quick_sync.bat
```

功能：

- ✅ 自动检测 IBKR Gateway 是否运行
- ✅ 自动同步持仓和 Greeks 到 Notion
- ✅ 显示执行结果和错误信息
- ⚡ 20 秒等待时间（平衡速度与数据完整性）

**创建桌面快捷方式：**

1. 右键桌面 → 新建 → 快捷方式
2. 位置填入你的项目路径：
   ```
   C:\path\to\ibkr-toolkit\scripts\quick_sync.bat
   ```
3. 命名为 `📊 IBKR Sync` 或任何你喜欢的名字
4. 完成！双击桌面图标即可一键同步

**日常工作流程：**

```
1. 晚上开盘前启动 IBKR Gateway（登录）
   └─ 等待 1-2 分钟让 Gateway 完全启动

2. 双击桌面快捷方式或 quick_sync.bat
   └─ 自动检查连接 → 获取持仓 → 计算 Greeks → 同步 Notion

3. 完成！总耗时约 30-40 秒
```

### 5. 检查工具包安全性

验证工具包代码中没有交易功能：

```bash
uv run scripts/check_trading_permissions.py
```

**输出示例：**

```
✅ TOOLKIT IS SAFE - NO TRADING FUNCTIONS DETECTED
   This toolkit is READ-ONLY

✅ Can read account data
   Account: U1234567

• This toolkit contains NO trading functions
• Safe to use with live accounts for data analysis
• Only performs READ operations
```

**说明：**

- ✅ 本工具包**仅包含只读功能**，无任何交易下单代码
- ✅ 可以安全用于真实账户的数据分析
- ✅ 只执行读取操作：账户信息、持仓数据、Greeks 数据
- ✅ 无法下单、修改或取消任何订单

---

**⚠️ 重要说明：净入金数据的获取**

IBKR 实时 TWS API **不提供历史充值/提现记录**。虽然在 IBKR 客户端的"关键数据"界面可以看到总入金，但这个数据**没有对应的实时 API**。

**推荐使用方法：**

**方法 1：在 `.env` 文件中配置（最推荐）**

```bash
# 1. 复制示例配置文件
cp .env.example .env

# 2. 编辑 .env 文件，设置你的实际入金金额
#    NET_DEPOSITS=95000

# 3. 直接运行，自动读取配置
uv run python scripts/fetch_account_summary.py
```

**方法 2：使用命令行参数**

```bash
# 临时指定入金金额（优先级高于 .env）
uv run python scripts/fetch_account_summary.py --net-deposits 95000
```

**方法 3：不配置则使用估算（不准确，不推荐）**

```bash
uv run python scripts/fetch_account_summary.py
```

**如何查找你的实际净入金：**

1. **IBKR 客户端** → 关键数据 → 选择"开户以来" → 查看总入金
2. **Client Portal** → Performance & Reports → Activity Statement → Deposits & Withdrawals
3. **银行转账记录** - 查看所有转账到 IBKR 的记录

**未来计划：**

我们可以集成 IBKR Flex Query API 来自动获取入金数据。详见：[docs/FLEX_QUERY_SETUP.md](docs/FLEX_QUERY_SETUP.md)

### 在代码中使用

```python
from ibkr_toolkit import IBKRClient, PortfolioService, ExportService
from ibkr_toolkit.config import Settings

# 加载配置
settings = Settings.from_env()

# 连接到 IBKR
with IBKRClient(settings) as client:
    # 获取持仓服务
    portfolio_service = PortfolioService(client)

    # 获取持仓汇总
    summary = portfolio_service.get_position_summary()

    if summary:
        print(f"总持仓: {summary.total_positions}")
        print(f"总市值: {summary.total_market_value:.2f}")
        print(f"总盈亏: {summary.total_pnl:.2f}")

        # 导出数据
        export_service = ExportService()
        output_file = export_service.export_to_csv(summary)
        print(f"已导出到: {output_file}")
```

## API 文档

### IBKRClient

连接和管理 IBKR API。

```python
client = IBKRClient(settings)
client.connect_sync()          # 连接
client.is_connected            # 检查连接状态
client.get_accounts()          # 获取账户列表
client.get_positions()         # 获取持仓
client.disconnect_sync()       # 断开连接
```

### PortfolioService

管理投资组合和持仓。

```python
service = PortfolioService(client)
positions = service.get_positions()              # 获取持仓列表
summary = service.get_position_summary()         # 获取持仓汇总
```

### ExportService

导出数据到各种格式。

```python
export_service = ExportService(output_dir="data")
export_service.export_to_csv(summary)            # 导出 CSV
export_service.export_to_json(summary)           # 导出 JSON
export_service.export_to_excel(summary)          # 导出 Excel
export_service.export(summary, format="csv")     # 通用导出
```

## 配置说明

### 环境变量

| 变量             | 说明                    | 默认值        |
| ---------------- | ----------------------- | ------------- |
| `IBKR_HOST`      | IBKR 主机地址           | `127.0.0.1`   |
| `IBKR_PORT`      | IBKR 端口               | `4002` (模拟) |
| `IBKR_CLIENT_ID` | 客户端 ID               | 随机          |
| `IBKR_TIMEOUT`   | 连接超时（秒）          | `10`          |
| `IBKR_ACCOUNT`   | 指定账户 ID（可选）     | 第一个账户    |
| `NET_DEPOSITS`   | 总入金金额（充值-提现） | 无（需配置）  |
| `DATA_DIR`       | 数据导出目录            | `data`        |
| `EXPORT_FORMAT`  | 默认导出格式            | `csv`         |
| `LOG_DIR`        | 日志目录                | `logs`        |
| `LOG_LEVEL`      | 日志级别                | `INFO`        |

### 端口说明

| 应用           | 账户类型 | 端口   | 说明                      |
| -------------- | -------- | ------ | ------------------------- |
| **IB Gateway** | 模拟账户 | `4002` | ✅ **默认配置，推荐使用** |
| **IB Gateway** | 实盘账户 | `4001` | ⚠️ 谨慎使用               |
| TWS            | 模拟账户 | `7497` | 适用于 Trader Workstation |
| TWS            | 实盘账户 | `7496` | ⚠️ 谨慎使用               |

## 数据格式

### 持仓数据字段

- `symbol`: 股票代码
- `contract_type`: 合约类型（STK, OPT, FUT 等）
- `exchange`: 交易所
- `currency`: 货币
- `position`: 持仓数量
- `avg_cost`: 平均成本
- `market_price`: 市场价格
- `market_value`: 市值
- `unrealized_pnl`: 未实现盈亏
- `realized_pnl`: 已实现盈亏
- `pnl_percent`: 盈亏比例（%）

## 常见问题

### 1. 连接失败

**问题**: `连接 IBKR 失败: [Errno 61] Connection refused`

**解决方案**:

- 确保 TWS/Gateway 正在运行
- 检查端口配置是否正确
- 确保 API 连接已启用
- 检查防火墙设置

### 2. 没有持仓数据

**问题**: `没有找到持仓数据`

**解决方案**:

- 确保账户中有持仓
- 等待数据完全加载（可能需要几秒钟）
- 检查是否指定了正确的账户

### 3. Excel 导出失败

**问题**: `导出 Excel 需要安装 pandas`

**解决方案**:

```bash
pip install pandas openpyxl
```

## 项目打包

打包项目为 ZIP 文件，方便分享和部署：

```bash
# 打包项目（自动排除敏感文件和系统文件）
uv run python scripts/package_project.py

# 自定义文件名
uv run python scripts/package_project.py --output my-project.zip
```

**自动排除：**

- ✅ `.gitignore` 中的文件（logs, data, .env 等）
- ✅ macOS 系统文件（.DS_Store, \_\_MACOSX 等）
- ✅ Python 缓存文件（**pycache**, \*.pyc 等）

详见：[docs/PACKAGING.md](docs/PACKAGING.md)

## 扩展功能

### 与飞书多维表格集成（计划中）

未来版本将支持直接导出到飞书多维表格，用于：

- 📊 生成实时图表
- 🤖 AI 数据分析
- 📱 移动端访问
- 👥 团队协作

### 其他计划功能

- [x] 账户信息查询（已完成）
- [x] 项目打包工具（已完成）
- [ ] 历史数据获取（Flex Queries 集成）
- [ ] 实时行情订阅（只读）
- [ ] 自动定时导出
- [ ] 数据分析报告
- [ ] Web Dashboard

## 🔒 安全与部署

### 安全检查

本工具包提供多层安全检查，确保服务器部署时的安全性。

#### 1. 检查 Gateway 交易权限

验证 IBKR Gateway 是否启用了 Read-Only API 保护：

```bash
uv run scripts/check_trading_permissions.py
```

**输出示例：**

```
✅ Gateway 已启用 Read-Only API 保护
  说明：订单被拦截，需要手动确认才能提交
  状态：这是最安全的配置 ✓
```

#### 2. 全面安全审计

在部署到 Linux 服务器前，运行完整的安全审计：

```bash
uv run scripts/security_audit.py
```

**检查项目：**

- ✅ Gateway 网络绑定（仅 localhost）
- ✅ 防火墙配置（阻止外部访问）
- ✅ SSH 安全配置（禁用密码登录）
- ✅ Gateway 进程用户（非 root）
- ✅ 敏感文件权限（.env, jts.ini）
- ✅ Gateway Read-Only API 状态
- ✅ 监控和告警配置

**输出示例：**

```
🔒 IBKR Gateway Security Audit
======================================================================

🔍 Checking Gateway Network Binding
✅ Gateway port 4001 is bound to localhost only

🔥 Checking Firewall Configuration
✅ UFW firewall is active
✅ Gateway ports are not explicitly allowed

📋 Security Audit Summary
✅ All security checks passed! ✓
🎉 Your configuration appears secure.
```

### 服务器部署安全指南

如果你计划将 IBKR Gateway 部署到 Linux 服务器上，**必须**阅读完整的安全指南：

📖 **[docs/SECURITY.md](docs/SECURITY.md)** - 完整的服务器安全部署指南

**关键安全措施（必做）：**

1. **Gateway 配置**

   - ✅ 启用 Read-Only API
   - ✅ 仅绑定到 127.0.0.1
   - ✅ 信任 IP 仅设置为 127.0.0.1

2. **IBKR 账户配置**

   - ✅ 创建只读子账户（强烈推荐）
   - ✅ 启用双因素认证 (2FA)
   - ✅ 禁用资金转账权限
   - ✅ 设置提款白名单

3. **系统安全**

   - ✅ 防火墙阻止外部访问 4001/4002 端口
   - ✅ SSH 仅允许密钥认证
   - ✅ Gateway 以非 root 用户运行
   - ✅ 文件系统权限正确设置

4. **监控与告警**
   - ✅ 监控异常连接
   - ✅ IBKR 账户通知
   - ✅ 日志审计
   - ✅ 入侵检测

**防御策略：**

即使服务器被攻破，攻击者也：

- ❌ 无法通过 API 下单（Read-Only API）
- ❌ 无法访问主账户（使用只读子账户）
- ❌ 无法转移资金（权限被禁用）
- ❌ 无法从外网连接 Gateway（防火墙 + localhost 绑定）

### 安全提醒

1. ✅ 本工具采用只读模式（`readonly=True`）
2. ✅ 不包含任何下单功能
3. ✅ Gateway Read-Only API 保护（验证：运行 `check_trading_permissions.py`）
4. ⚠️ 建议先在模拟账户测试
5. ⚠️ 不要分享您的 `.env` 配置文件
6. ⚠️ 定期检查 API 权限设置
7. 🔒 **服务器部署前必读：** [docs/SECURITY.md](docs/SECURITY.md)

## 依赖项

- Python >= 3.13
- ib-async >= 2.0.1 (导入时使用 `from ib_async import ...`)
- pandas (可选，用于 Excel 导出)
- openpyxl (可选，用于 Excel 导出)

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！

## 支持

如有问题，请提交 Issue 或联系开发者。

---

**免责声明**: 本工具仅供学习和研究使用。使用本工具进行实盘操作的风险由用户自行承担。
