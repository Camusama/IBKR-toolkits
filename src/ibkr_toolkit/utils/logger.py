"""日志配置模块"""

import logging
import sys
from pathlib import Path
from datetime import datetime


def setup_logger(
    name: str = "ibkr_toolkit",
    level: int = logging.INFO,
    log_dir: str = "logs",
) -> logging.Logger:
    """设置日志记录器
    
    Args:
        name: 日志记录器名称
        level: 日志级别
        log_dir: 日志文件目录
        
    Returns:
        配置好的日志记录器
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # 避免重复添加处理器
    if logger.handlers:
        return logger
    
    # 创建日志目录
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)
    
    # 日志格式
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 控制台处理器 - 处理 Windows 编码问题
    try:
        # 在 Windows 上尝试使用正确的编码
        if sys.platform == 'win32':
            # 尝试设置控制台编码为 UTF-8
            try:
                sys.stdout.reconfigure(encoding='utf-8')
            except (AttributeError, OSError):
                # 如果不支持 reconfigure，使用系统编码
                pass
        console_handler = logging.StreamHandler(sys.stdout)
    except Exception:
        # 如果设置失败，使用默认处理器
        console_handler = logging.StreamHandler()

    console_handler.setLevel(level)

    # 创建自定义格式化器来处理 Unicode 字符
    class UnicodeSafeFormatter(logging.Formatter):
        def format(self, record):
            try:
                return super().format(record)
            except UnicodeEncodeError:
                # 如果编码失败，替换非 ASCII 字符
                record.msg = str(record.msg).encode('ascii', 'replace').decode('ascii')
                return super().format(record)

    unicode_formatter = UnicodeSafeFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(unicode_formatter)
    logger.addHandler(console_handler)
    
    # 文件处理器
    log_file = log_path / f"{name}_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger

