"""
Layer2: 冲击检测 + 清算分类

第2层决策逻辑：
1. 检测价格冲击（价格变动 + 成交量激增）
2. 等待45秒观察清算数据
3. 用清算数据判断是"过度反应"还是"真突破"

导出接口：
- detect_impact: 检测是否发生冲击
- classify_impact: 异步分类冲击类型
- classify_impact_sync: 同步分类冲击类型
- ImpactEvent: 冲击事件数据结构
- ClassificationResult: 分类结果数据结构
"""

from .impact_detector import detect_impact, get_impact_details
from .liquidation_classifier import classify_impact, classify_impact_sync
from .supplementary_signals import get_supplementary_bonus, get_supplementary_signals
from .types import ClassificationResult, ImpactEvent

__all__ = [
    # 核心函数
    "detect_impact",
    "classify_impact",
    "classify_impact_sync",
    # 辅助函数
    "get_impact_details",
    "get_supplementary_bonus",
    "get_supplementary_signals",
    # 数据类型
    "ImpactEvent",
    "ClassificationResult",
]