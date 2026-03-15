"""
诊断脚本：对比当前参数和历史记录
"""

print("=" * 60)
print("1. 检查DEFAULT_CONFIG参数")
print("=" * 60)

import sys
sys.path.insert(0, '.')
from src.backtest.engine import DEFAULT_CONFIG

# 关键参数对比
params_to_check = [
    "impact_window_ms",
    "impact_cooldown_ms",
    "min_price_change",
    "volume_surge_threshold",
    "classification_wait_ms",
    "liq_count_threshold",
    "liq_count_low",
    "liq_ratio_threshold",
    "liq_ratio_low",
    "liq_value_min",
    "liq_value_breakout_min",
]

print("\n当前参数值:")
for param in params_to_check:
    value = DEFAULT_CONFIG.get(param)
    print(f"  {param}: {value}")

print("\n历史记录 (来自MEMORY.md):")
print("  volume_surge_threshold: 2.0 -> 1.5 (已修改)")
print("  liq_count_low: 2 -> 4 (已修改)")
print("  classification_wait_ms: 45000 -> 30000 (已修改)")
print("  impact_cooldown_ms: 60000 -> 30000 (已修改)")

