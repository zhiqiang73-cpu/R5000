"""
Layer2 数据类型定义
冲击事件和分类结果的数据结构
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class ImpactEvent:
    """
    冲击事件

    当检测到价格快速变动+成交量激增时创建
    """
    direction: str  # "up" 或 "down"
    magnitude: Decimal  # 价格变动幅度（比例，如0.005表示0.5%）
    start_time: int  # 毫秒时间戳，冲击开始时间
    start_price: Decimal  # 冲击开始时的价格
    end_price: Optional[Decimal]  # 冲击结束时的价格（检测时为None）
    volume_surge: Optional[Decimal]  # 成交量激增倍数
    trigger_reason: str  # 触发原因："price_change" | "volume_surge"

    def __post_init__(self):
        """验证数据有效性"""
        if self.direction not in ("up", "down"):
            raise ValueError(f"direction must be 'up' or 'down', got {self.direction}")
        if self.magnitude < 0:
            raise ValueError("magnitude must be non-negative")


@dataclass
class ClassificationResult:
    """
    冲击分类结果

    判断冲击是"过度反应"还是"真突破"
    """
    category: str  # "过度反应" | "真突破" | "不确定"
    strategy: str  # "均值回归" | "趋势跟随" | "放弃"
    direction: str  # 建议交易方向："BUY" | "SELL"
    confidence: Decimal  # 信心度 0.0-1.0
    liq_count: int  # 相关清算数量
    liq_ratio: Decimal  # 清算量占比
    liq_value: Decimal  # 清算总价值（USD）
    reason: str  # 分类原因

    # 补充信号加成（可选）
    cvd_follows: Optional[bool] = None  # CVD是否跟随
    depth_recovered: Optional[bool] = None  # 订单簿是否恢复
    speed_decayed: Optional[bool] = None  # 成交速度是否衰减

    def __post_init__(self):
        """验证数据有效性"""
        if self.category not in ("过度反应", "真突破", "不确定"):
            raise ValueError(f"Invalid category: {self.category}")
        if self.strategy not in ("均值回归", "趋势跟随", "放弃"):
            raise ValueError(f"Invalid strategy: {self.strategy}")
        if not (Decimal("0") <= self.confidence <= Decimal("1")):
            raise ValueError(f"confidence must be 0-1, got {self.confidence}")