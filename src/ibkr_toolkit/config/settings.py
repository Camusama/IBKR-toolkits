"""配置管理模块"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# 尝试加载 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # 如果没有安装 python-dotenv，跳过
    pass


@dataclass
class Settings:
    """应用配置类"""

    # IBKR 连接配置
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4002  # IB Gateway Paper Trading 端口（TWS 用 7497，实盘用 4001/7496）
    ibkr_client_id: int = 2  # 客户端ID（避免冲突，改为2）
    ibkr_timeout: int = 10  # 连接超时时间（秒）
    ibkr_account: Optional[str] = None  # 指定账户，None 则使用第一个可用账户
    
    # 账户配置
    net_deposits: Optional[float] = None  # 总入金金额（充值总额 - 提现总额）

    # 数据导出配置
    data_dir: Path = Path("data")
    export_format: str = "csv"  # 支持：csv, json, excel

    # 日志配置
    log_dir: Path = Path("logs")
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量加载配置

        Returns:
            配置实例
        """
        # 使用随机 clientId 避免冲突（范围: 10-1000）
        import random
        default_client_id = random.randint(10, 1000)
        
        # Read net deposits from env (None if not set or invalid)
        net_deposits_str = os.getenv("NET_DEPOSITS")
        net_deposits = None
        if net_deposits_str:
            try:
                net_deposits = float(net_deposits_str)
            except ValueError:
                pass  # Invalid value, keep as None
        
        return cls(
            ibkr_host=os.getenv("IBKR_HOST", "127.0.0.1"),
            ibkr_port=int(os.getenv("IBKR_PORT", "4002")),
            ibkr_client_id=int(os.getenv("IBKR_CLIENT_ID", str(default_client_id))),
            ibkr_timeout=int(os.getenv("IBKR_TIMEOUT", "10")),
            ibkr_account=os.getenv("IBKR_ACCOUNT"),  # None if not set
            net_deposits=net_deposits,  # Net deposits amount
            data_dir=Path(os.getenv("DATA_DIR", "data")),
            export_format=os.getenv("EXPORT_FORMAT", "csv"),
            log_dir=Path(os.getenv("LOG_DIR", "logs")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

    def ensure_dirs(self) -> None:
        """确保必要的目录存在"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
