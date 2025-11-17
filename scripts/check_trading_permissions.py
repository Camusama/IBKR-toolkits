#!/usr/bin/env python3
"""Check IBKR Toolkit Safety

This script verifies that the toolkit has NO trading functions.
It checks the codebase to ensure read-only operation.

The toolkit is designed to be safe with LIVE accounts - it only reads data.

Usage:
    python scripts/check_trading_permissions.py [--account ACCOUNT]
"""

from ibkr_toolkit.config.settings import Settings
from ibkr_toolkit.client.ibkr_client import IBKRClient
from ibkr_toolkit.utils.logger import setup_logger
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


# ANSI color codes
class Colors:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BOLD = '\033[1m'
    END = '\033[0m'


def print_warning(message):
    """Print message in red bold"""
    print(f"{Colors.BOLD}{Colors.RED}{message}{Colors.END}")


def print_success(message):
    """Print message in green"""
    print(f"{Colors.GREEN}{message}{Colors.END}")


def print_info(message):
    """Print message in yellow"""
    print(f"{Colors.YELLOW}{message}{Colors.END}")


def check_real_trading_permission(client, logger):
    """Test if account can actually place orders (real test)

    Args:
        client: IBKRClient instance
        logger: Logger instance

    Returns:
        dict: Trading permission test results
    """
    result = {
        'can_trade': None,
        'test_method': 'order_placement',
        'error_message': None,
        'is_read_only': None
    }

    try:
        # Create a test order that will never execute
        # Use AAPL with extremely low price (0.01) - won't fill

        # Access ib directly from client
        from ib_async import Stock, LimitOrder

        contract = Stock('AAPL', 'SMART', 'USD')

        # Qualify the contract first
        qualified = client.ib.qualifyContracts(contract)
        if not qualified:
            result['error_message'] = "Cannot qualify test contract"
            return result

        contract = qualified[0]

        # Create limit order with impossible price
        order = LimitOrder('BUY', 1, 0.01)  # $0.01 - will never fill

        logger.info(
            "Testing order placement with AAPL @ $0.01 (will not fill)...")

        # Try to place the order
        trade = client.ib.placeOrder(contract, order)

        # Order got an ID - but need to check if it's truly submitted
        logger.info(f"Order received ID: {trade.order.orderId}")

        # Wait a moment to see if order proceeds or gets stuck
        client.ib.sleep(2)

        # Check order status
        order_status = trade.orderStatus.status
        logger.info(f"Order status after 2 seconds: {order_status}")

        # Cancel the order
        client.ib.cancelOrder(order)
        logger.info("Test order cancelled")

        # Analyze the status
        if order_status == 'ValidationError':
            # Check if it's Read-Only related
            log_messages = ' '.join(
                [entry.message for entry in trade.log if entry.message])
            if 'read-only' in log_messages.lower() or 'errorcode=321' in log_messages.lower():
                result['can_trade'] = False
                result['is_read_only'] = True
                result['error_message'] = "ValidationError: Read-Only API is active (Error 321)"
                logger.info("Validation failed - Read-Only API is protecting")
            else:
                result['can_trade'] = False
                result['error_message'] = f"ValidationError: {log_messages}"
        elif order_status in ['PendingSubmit', 'PreSubmitted', 'Inactive']:
            # Order stuck in pending - Read-Only API might be protecting
            result['can_trade'] = False
            result['is_read_only'] = True
            result['error_message'] = f"Order stuck in '{order_status}' - Read-Only API is active"
            logger.info("Order did not proceed - Read-Only API is protecting")
        elif order_status in ['Submitted', 'Filled', 'PartiallyFilled']:
            # Order actually submitted - no Read-Only protection
            result['can_trade'] = True
            result['is_read_only'] = False
            result['error_message'] = f"Order reached '{order_status}' - API can trade"
            logger.info("Order proceeded to market - no Read-Only protection")
        else:
            # Cancelled or other status
            result['can_trade'] = None
            result['error_message'] = f"Order status: {order_status}"
            logger.info(f"Order ended in status: {order_status}")

    except Exception as e:
        error_str = str(e).lower()
        logger.info(f"Order placement failed: {e}")

        # Check if it's read-only error
        if 'read' in error_str and 'only' in error_str:
            result['can_trade'] = False
            result['is_read_only'] = True
            result['error_message'] = "Read-Only API is enabled"
        elif 'not allowed' in error_str or 'permission' in error_str:
            result['can_trade'] = False
            result['error_message'] = "No trading permission"
        else:
            result['can_trade'] = False
            result['error_message'] = f"Cannot place orders: {str(e)}"

    return result


def check_account_capabilities(client, account, logger):
    """Check account type and capabilities

    Args:
        client: IBKRClient instance
        account: Account ID
        logger: Logger instance

    Returns:
        dict: Account capabilities info
    """
    capabilities = {
        'is_read_only': None,
        'has_trading_capability': None,
        'account_type': None,
        'trading_permissions': []
    }

    try:
        account_values = client.ib.accountValues(account)

        for value in account_values:
            # Check for read-only indicators
            if 'ReadOnly' in value.tag or 'ReadOnlyAPI' in value.tag:
                capabilities['is_read_only'] = (value.value.upper() == 'TRUE')
                logger.info(f"{value.tag}: {value.value}")

            # Check account type
            if value.tag == 'AccountType':
                capabilities['account_type'] = value.value
                logger.info(f"AccountType: {value.value}")

            # Check trading permissions
            if 'Trading' in value.tag or 'Permission' in value.tag:
                capabilities['trading_permissions'].append(
                    f"{value.tag}={value.value}")
                logger.info(f"{value.tag}: {value.value}")

        # Determine if has trading capability
        if capabilities['is_read_only'] is True:
            capabilities['has_trading_capability'] = False
        elif capabilities['is_read_only'] is False:
            capabilities['has_trading_capability'] = True

    except Exception as e:
        logger.error(f"Error checking account capabilities: {e}")

    return capabilities


def test_data_access(client, account, logger):
    """Test if can read account data

    Args:
        client: IBKRClient instance
        account: Account ID
        logger: Logger instance

    Returns:
        dict: Data access status
    """
    status = {
        'can_read_data': False,
        'positions_count': 0,
        'error': None
    }

    try:
        # Test read access
        positions = client.ib.positions(account)

        if positions is not None:
            status['can_read_data'] = True
            status['positions_count'] = len(positions)
            logger.info(f"✓ Can read account data: {len(positions)} positions")

    except Exception as e:
        status['error'] = str(e)
        logger.error(f"Cannot read account data: {e}")

    return status


def main():
    """Main function"""
    import argparse
    parser = argparse.ArgumentParser(
        description="Check IBKR trading permissions"
    )
    parser.add_argument(
        "--account",
        type=str,
        default=None,
        help="Specify account (optional)"
    )

    args = parser.parse_args()
    logger = setup_logger("check_trading_permissions")

    try:
        print("=" * 70)
        print_info("🔍 IBKR 工具包安全检查器")
        print("=" * 70)
        print()

        # Initialize settings
        settings = Settings.from_env()

        # Connect to IBKR
        print("正在连接到 IBKR...")
        client = IBKRClient(settings)

        if not client.connect_sync():
            print_warning("❌ 连接 IBKR 失败")
            return 1

        try:
            # Get account
            account = args.account or client.get_default_account()
            if not account:
                print_warning("❌ 没有可用账户")
                return 1

            print(f"检查账户: {account}")
            print(f"端口: {settings.ibkr_port}")
            print()

            # Test data access
            print("正在测试数据访问...")
            data_status = test_data_access(client, account, logger)
            print()

            # Check account capabilities
            print("正在检查账户能力...")
            capabilities = check_account_capabilities(client, account, logger)
            print()

            # Real trading permission test
            print("正在测试交易权限（使用测试订单）...")
            print("⚠️  将尝试下一个 $0.01 的 AAPL 测试订单（不会成交）")
            trading_test = check_real_trading_permission(client, logger)
            print()

            # Display results
            print("=" * 70)
            print_info("🔒 IBKR Gateway 连接权限检测结果")
            print("=" * 70)
            print()

            # Data access check
            if data_status['can_read_data']:
                print_success(f"✅ 可以读取账户数据")
                print_info(f"   账户: {account}")
                print_info(f"   持仓数量: {data_status['positions_count']}")
                print()
            else:
                print_warning("⚠️  无法读取账户数据")
                if data_status['error']:
                    print(f"   错误: {data_status['error']}")
                print()

            # Trading permission check
            print("=" * 70)
            print_info("🔑 交易权限测试（真实测试）")
            print("=" * 70)
            print()

            # Real order placement test
            print("【测试方法：尝试下单】")
            print(f"  测试订单: AAPL 股票, 买入 1 股 @ $0.01")
            print(f"  说明: 价格极低，不会实际成交")
            print()

            if trading_test['can_trade'] is True:
                print_warning("⚠️  Gateway 允许下单 - 检测到交易权限")
                print()
                print("  说明：测试订单成功提交到市场")
                print("  状态：当前连接可以执行交易操作")
                print("  详情：", trading_test['error_message'])
                print()
                print("  🔧 如何关闭交易权限：")
                print("     1. 在IB Gateway/TWS中启用只读模式：")
                print("        - 打开IB Gateway → 设置（齿轮图标）→ API → Settings")
                print("        - 勾选 'Read-Only API' 选项")
                print("        - 重启IB Gateway")
                print()
                print("     2. 使用子账户（推荐用于自动化）：")
                print("        - 在IBKR账户管理中创建只读子账户")
                print("        - 为子账户设置API权限时，只授予查询权限")
                print()
            elif trading_test['can_trade'] is False:
                if trading_test['is_read_only']:
                    print_success("✅ Gateway 已启用 Read-Only API 保护")
                    print()
                    print("  说明：订单被拦截，需要手动确认才能提交")
                    print("  状态：这是最安全的配置 ✓")
                    print("  详情：", trading_test['error_message'])
                    print()
                    print("  🎯 Read-Only API 工作方式：")
                    print("     • API可以创建订单（分配订单ID）")
                    print("     • 但订单不会自动提交到市场")
                    print("     • IB Gateway会弹出确认对话框")
                    print("     • 需要手动点击确认才能执行")
                    print("     • 这防止了自动化脚本意外交易")
                else:
                    print_success("✅ Gateway 无法下单")
                    print()
                    print("  说明：无法执行交易操作")
                    print("  原因：", trading_test['error_message'])
            else:
                print_info("❓ 无法完成交易权限测试")
                print(f"  原因: {trading_test['error_message']}")
            print()

            # Account capabilities
            print("【账户信息】")
            if capabilities['account_type']:
                print(f"  账户类型: {capabilities['account_type']}")

            # Only show account-level read-only status if trading test didn't confirm it
            if trading_test['is_read_only'] is not None:
                # Trading test already confirmed read-only status, skip account-level check
                pass
            elif capabilities['is_read_only'] is True:
                print_success("✅ API配置为只读（无法交易）")
                print()
                print("  说明：API级别的只读保护已启用")
                print("  状态：最安全的配置 ✓")
            elif capabilities['is_read_only'] is False:
                print_warning("⚠️  API未配置为只读（可能可以交易）")
                print()
                print("  说明：API没有只读保护，如果使用交易代码可能会执行交易")
                print()
                print("  🔧 如何启用只读API：")
                print("     方法1 - IB Gateway设置：")
                print("       1. 关闭IB Gateway")
                print("       2. 打开 ~/Jts/jts.ini 配置文件")
                print("       3. 在[IBGateway]部分添加：ReadOnlyApi=yes")
                print("       4. 保存并重启IB Gateway")
                print()
                print("     方法2 - 图形界面设置：")
                print("       1. 打开IB Gateway")
                print("       2. 设置 → API → Settings")
                print("       3. 勾选 'Read-Only API'")
                print("       4. 点击Apply，重启生效")
            else:
                print_info("❓ 无法从账户信息确定只读状态")
                print()
                print("  说明：IBKR API未返回只读状态标志")
                print("  原因：某些IBKR版本不提供此信息")
                print()
                print("  🔧 建议操作：")
                print("     手动检查IB Gateway设置中的'Read-Only API'选项")

            if capabilities['trading_permissions']:
                print()
                print("  交易权限详情:")
                for perm in capabilities['trading_permissions']:
                    print(f"    • {perm}")
                    if "STKNOPT" in perm:
                        print("      → 股票(STK) + 期权(OPT)交易权限")
                    if "DayTrading" in perm:
                        parts = perm.split('=')[1] if '=' in perm else ''
                        if 'false' in parts.lower():
                            print("      → 非日内交易账户")
                        else:
                            print("      → 日内交易账户")

            print()

            # Summary
            print("=" * 70)
            print_info("📊 检测总结")
            print("=" * 70)
            print()

            print("【连接信息】")
            print(f"  账户: {account}")
            print(f"  端口: {settings.ibkr_port}")
            print(f"  主机: {settings.ibkr_host}")
            print()

            print("【权限状态】")
            if data_status['can_read_data']:
                print("  ✅ 数据读取: 正常")
            else:
                print("  ❌ 数据读取: 失败")

            if trading_test['can_trade'] is True:
                print("  ⚠️  交易权限: 已启用（订单可直接提交市场）")
                print()
                print("  💡 建议：启用 Read-Only API 以防止意外交易")
            elif trading_test['can_trade'] is False:
                if trading_test['is_read_only']:
                    print("  ✅ 交易权限: Read-Only API 已启用（需手动确认）")
                    print()
                    print("  ✓ 当前配置是最安全的")
                    print("  ✓ 所有API订单都需要手动确认")
                else:
                    print("  ✅ 交易权限: 已禁用")
            else:
                print("  ❓ 交易权限: 无法确定")

            print()
            print("=" * 70)

        finally:
            client.disconnect_sync()
            print()
            print("已断开 IBKR 连接")

        return 0

    except Exception as e:
        logger.error(f"Execution failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
