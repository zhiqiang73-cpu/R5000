"""
恢复参数到历史值
"""
import re

print("正在恢复参数...")

# 读取engine.py
with open('src/backtest/engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 参数替换规则
replacements = [
    ('"volume_surge_threshold": Decimal("1.5")', '"volume_surge_threshold": Decimal("2.0")'),
    ('"liq_count_low": 4', '"liq_count_low": 2'),
    ('"classification_wait_ms": 30_000', '"classification_wait_ms": 45_000'),
    ('"impact_cooldown_ms": 30_000', '"impact_cooldown_ms": 60_000'),
]

changes_made = []
for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        changes_made.append(f"✓ {old} -> {new}")
    else:
        changes_made.append(f"✗ 未找到: {old}")

# 写回
with open('src/backtest/engine.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("\n参数恢复结果:")
for change in changes_made:
    print(change)

# 验证
print("\n验证恢复后的参数:")
from src.backtest.engine import DEFAULT_CONFIG
print(f"  volume_surge_threshold: {DEFAULT_CONFIG['volume_surge_threshold']}")
print(f"  liq_count_low: {DEFAULT_CONFIG['liq_count_low']}")
print(f"  classification_wait_ms: {DEFAULT_CONFIG['classification_wait_ms']}")
print(f"  impact_cooldown_ms: {DEFAULT_CONFIG['impact_cooldown_ms']}")

print("\n✅ 参数已恢复到历史值！")
