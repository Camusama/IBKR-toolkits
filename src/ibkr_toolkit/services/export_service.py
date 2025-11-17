"""数据导出服务模块

支持将持仓数据导出为多种格式：CSV, JSON, Excel
"""

import json
import csv
from pathlib import Path
from datetime import datetime
from typing import Optional
from ..models.position import PositionSummary
from ..utils.logger import setup_logger


class ExportService:
    """数据导出服务类"""

    def __init__(self, output_dir: str = "data"):
        """初始化导出服务

        Args:
            output_dir: 输出目录
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logger = setup_logger("export_service")

    def export_to_csv(
        self,
        summary: PositionSummary,
        filename: Optional[str] = None
    ) -> Path:
        """导出为 CSV 格式

        Args:
            summary: 持仓汇总对象
            filename: 输出文件名，如果为 None 则自动生成

        Returns:
            导出文件的路径
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"positions_{timestamp}.csv"

        filepath = self.output_dir / filename

        try:
            with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
                # 写入汇总信息
                writer = csv.writer(f)
                writer.writerow(['持仓汇总报告'])
                writer.writerow(
                    ['生成时间', summary.update_time.strftime('%Y-%m-%d %H:%M:%S')])
                writer.writerow(['持仓数量', summary.total_positions])
                writer.writerow(['总市值', f"{summary.total_market_value:.2f}"])
                writer.writerow(
                    ['未实现盈亏', f"{summary.total_unrealized_pnl:.2f}"])
                writer.writerow(['已实现盈亏', f"{summary.total_realized_pnl:.2f}"])
                writer.writerow(['总盈亏', f"{summary.total_pnl:.2f}"])
                writer.writerow(['盈亏比例', f"{summary.total_pnl_percent:.2f}%"])
                writer.writerow([])  # 空行

                # 写入持仓明细表头
                writer.writerow([
                    '代码', '类型', '交易所', '货币', '持仓数量',
                    '平均成本', '市场价格', '市值', '未实现盈亏',
                    '已实现盈亏', '盈亏比例(%)', '账户'
                ])

                # 写入持仓数据
                for pos in summary.positions:
                    writer.writerow([
                        pos.symbol,
                        pos.contract_type,
                        pos.exchange,
                        pos.currency,
                        f"{pos.position:.2f}",
                        f"{pos.avg_cost:.2f}",
                        f"{pos.market_price:.2f}",
                        f"{pos.market_value:.2f}",
                        f"{pos.unrealized_pnl:.2f}",
                        f"{pos.realized_pnl:.2f}",
                        f"{pos.pnl_percent:.2f}",
                        pos.account or ''
                    ])

            self.logger.info(f"成功导出 CSV 文件: {filepath}")
            return filepath

        except Exception as e:
            self.logger.error(f"导出 CSV 失败: {e}")
            raise

    def export_to_json(
        self,
        summary: PositionSummary,
        filename: Optional[str] = None,
        pretty: bool = True
    ) -> Path:
        """导出为 JSON 格式

        Args:
            summary: 持仓汇总对象
            filename: 输出文件名，如果为 None 则自动生成
            pretty: 是否格式化输出

        Returns:
            导出文件的路径
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"positions_{timestamp}.json"

        filepath = self.output_dir / filename

        try:
            data = summary.to_dict()

            with open(filepath, 'w', encoding='utf-8') as f:
                if pretty:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                else:
                    json.dump(data, f, ensure_ascii=False)

            self.logger.info(f"成功导出 JSON 文件: {filepath}")
            return filepath

        except Exception as e:
            self.logger.error(f"导出 JSON 失败: {e}")
            raise

    def export_to_excel(
        self,
        summary: PositionSummary,
        filename: Optional[str] = None
    ) -> Path:
        """导出为 Excel 格式（需要安装 openpyxl 或 xlsxwriter）

        Args:
            summary: 持仓汇总对象
            filename: 输出文件名，如果为 None 则自动生成

        Returns:
            导出文件的路径
        """
        try:
            import pandas as pd
        except ImportError:
            self.logger.error(
                "导出 Excel 需要安装 pandas: pip install pandas openpyxl")
            raise ImportError(
                "请安装 pandas 和 openpyxl: pip install pandas openpyxl")

        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"positions_{timestamp}.xlsx"

        filepath = self.output_dir / filename

        try:
            # 创建 Excel writer
            with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
                # 汇总信息
                summary_data = {
                    '项目': ['生成时间', '持仓数量', '总市值', '未实现盈亏',
                           '已实现盈亏', '总盈亏', '盈亏比例'],
                    '值': [
                        summary.update_time.strftime('%Y-%m-%d %H:%M:%S'),
                        summary.total_positions,
                        f"{summary.total_market_value:.2f}",
                        f"{summary.total_unrealized_pnl:.2f}",
                        f"{summary.total_realized_pnl:.2f}",
                        f"{summary.total_pnl:.2f}",
                        f"{summary.total_pnl_percent:.2f}%"
                    ]
                }
                df_summary = pd.DataFrame(summary_data)
                df_summary.to_excel(writer, sheet_name='汇总', index=False)

                # 持仓明细
                positions_data = []
                for pos in summary.positions:
                    positions_data.append({
                        '代码': pos.symbol,
                        '类型': pos.contract_type,
                        '交易所': pos.exchange,
                        '货币': pos.currency,
                        '持仓数量': pos.position,
                        '平均成本': pos.avg_cost,
                        '市场价格': pos.market_price,
                        '市值': pos.market_value,
                        '未实现盈亏': pos.unrealized_pnl,
                        '已实现盈亏': pos.realized_pnl,
                        '盈亏比例(%)': pos.pnl_percent,
                        '账户': pos.account or ''
                    })

                df_positions = pd.DataFrame(positions_data)
                df_positions.to_excel(writer, sheet_name='持仓明细', index=False)

            self.logger.info(f"成功导出 Excel 文件: {filepath}")
            return filepath

        except Exception as e:
            self.logger.error(f"导出 Excel 失败: {e}")
            raise

    def export(
        self,
        summary: PositionSummary,
        format: str = "csv",
        filename: Optional[str] = None
    ) -> Path:
        """通用导出方法

        Args:
            summary: 持仓汇总对象
            format: 导出格式 (csv, json, excel)
            filename: 输出文件名

        Returns:
            导出文件的路径
        """
        format = format.lower()

        if format == "csv":
            return self.export_to_csv(summary, filename)
        elif format == "json":
            return self.export_to_json(summary, filename)
        elif format in ["excel", "xlsx"]:
            return self.export_to_excel(summary, filename)
        else:
            raise ValueError(f"不支持的导出格式: {format}")
