"""第1-3层模块导出"""

from .environment import EnvironmentResult

# classifier (Layer 2) 单独导入，避免编码问题污染 Layer 1 测试
try:
    from .classifier import ImpactEvent, ClassificationResult, detect_impact, classify_impact
    _classifier_available = True
except SyntaxError:
    _classifier_available = False

__all__ = [
    "EnvironmentResult",
]
if _classifier_available:
    __all__ += ["ImpactEvent", "ClassificationResult", "detect_impact", "classify_impact"]