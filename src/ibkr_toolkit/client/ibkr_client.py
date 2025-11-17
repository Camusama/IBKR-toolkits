"""IBKR 客户端模块

提供与 Interactive Brokers TWS/Gateway 的连接管理。
注意：本客户端仅用于查询数据，不包含任何交易功能。
"""

import asyncio
from typing import Optional
from ib_async import IB, util
from ..config.settings import Settings
from ..utils.logger import setup_logger


class IBKRClient:
    """IBKR API 客户端封装类"""
    
    def __init__(self, settings: Optional[Settings] = None):
        """初始化客户端
        
        Args:
            settings: 配置对象，如果为 None 则从环境变量加载
        """
        self.settings = settings or Settings.from_env()
        self.ib = IB()
        self.logger = setup_logger("ibkr_client")
        self._connected = False
    
    async def connect(self) -> bool:
        """连接到 IBKR TWS/Gateway
        
        Returns:
            连接是否成功
        """
        try:
            self.logger.info(
                f"正在连接到 IBKR: {self.settings.ibkr_host}:{self.settings.ibkr_port}"
            )
            
            await self.ib.connectAsync(
                host=self.settings.ibkr_host,
                port=self.settings.ibkr_port,
                clientId=self.settings.ibkr_client_id,
                timeout=self.settings.ibkr_timeout,
                readonly=True,  # 只读模式，确保安全
            )
            
            self._connected = True
            self.logger.info("成功连接到 IBKR")
            
            # 获取账户信息
            accounts = self.ib.managedAccounts()
            self.logger.info(f"可用账户: {accounts}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"连接 IBKR 失败: {e}")
            self._connected = False
            return False
    
    def connect_sync(self) -> bool:
        """同步方式连接到 IBKR
        
        Returns:
            连接是否成功
        """
        return util.run(self.connect())
    
    async def disconnect(self) -> None:
        """断开与 IBKR 的连接"""
        if self._connected:
            self.ib.disconnect()
            self._connected = False
            self.logger.info("已断开 IBKR 连接")
    
    def disconnect_sync(self) -> None:
        """同步方式断开连接"""
        util.run(self.disconnect())
    
    @property
    def is_connected(self) -> bool:
        """检查是否已连接
        
        Returns:
            是否已连接
        """
        return self._connected and self.ib.isConnected()
    
    def get_accounts(self) -> list[str]:
        """获取管理的账户列表
        
        Returns:
            账户列表
        """
        if not self.is_connected:
            self.logger.warning("未连接到 IBKR")
            return []
        return self.ib.managedAccounts()
    
    def get_default_account(self) -> Optional[str]:
        """获取默认账户
        
        优先使用配置文件中指定的账户，如果未指定则返回第一个可用账户。
        
        Returns:
            账户ID，如果没有可用账户则返回 None
        """
        if not self.is_connected:
            self.logger.warning("未连接到 IBKR")
            return None
        
        # 如果配置中指定了账户，使用指定的账户
        if self.settings.ibkr_account:
            accounts = self.get_accounts()
            if self.settings.ibkr_account in accounts:
                self.logger.info(f"使用配置中指定的账户: {self.settings.ibkr_account}")
                return self.settings.ibkr_account
            else:
                self.logger.warning(
                    f"配置的账户 {self.settings.ibkr_account} 不在可用账户列表中: {accounts}"
                )
        
        # 使用第一个可用账户
        accounts = self.get_accounts()
        if accounts:
            default_account = accounts[0]
            self.logger.info(f"使用默认账户（第一个可用账户）: {default_account}")
            return default_account
        
        self.logger.warning("没有可用的账户")
        return None
    
    def get_positions(self, account: Optional[str] = None):
        """获取持仓信息
        
        Args:
            account: 指定账户，如果为 None 则获取所有账户
            
        Returns:
            持仓列表
        """
        if not self.is_connected:
            self.logger.warning("未连接到 IBKR")
            return []
        
        positions = self.ib.positions(account=account)
        self.logger.info(f"获取到 {len(positions)} 个持仓")
        return positions
    
    def get_portfolio_items(self, account: Optional[str] = None):
        """获取投资组合项目
        
        Args:
            account: 指定账户
            
        Returns:
            投资组合项目列表
        """
        if not self.is_connected:
            self.logger.warning("未连接到 IBKR")
            return []
        
        portfolio = self.ib.portfolio(account=account)
        self.logger.info(f"获取到 {len(portfolio)} 个投资组合项目")
        return portfolio
    
    def __enter__(self):
        """上下文管理器入口"""
        self.connect_sync()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.disconnect_sync()

