# 检查参数

## 任务
检查系统参数数量和来源，防止过拟合。

## 核心原则

```
参数越多，过拟合风险越高。

经验法则：
  样本数 >= 参数数 × 30
  
本系统要求：
  参数数 <= 15
  每个参数必须有逻辑或统计来源
```

## 执行步骤

1. **扫描代码中的参数**
   ```bash
   python -m utils.param_scanner --src src/
   ```

2. **参数清单检查**

   ### 第1层参数
   | 参数 | 当前值 | 来源 | 状态 |
   |------|--------|------|------|
   | `FR_ZSCORE_THRESHOLD` | ±2.0 | 统计学标准 | ✅ |
   | `OI_CHANGE_THRESHOLD` | 3% | 历史分布75分位 | ✅ |
   | `VOLUME_RATIO_MIN` | 0.5 | 经验值 | ⚠️ 需验证 |
   | `VOLUME_RATIO_ACTIVE` | 1.3 | 历史分布70分位 | ✅ |

   ### 第2层参数
   | 参数 | 当前值 | 来源 | 状态 |
   |------|--------|------|------|
   | `IMPACT_THRESHOLD` | 0.15% | 动态，基于近期波动 | ✅ |
   | `VOLUME_SURGE_RATIO` | 2.0 | 历史分布75分位 | ✅ |
   | `LIQ_COUNT_MIN` | 5 | 清算分布分析 | ✅ |
   | `LIQ_RATIO_MIN` | 15% | 清算分布分析 | ✅ |
   | `OBSERVE_WINDOW` | 45秒 | 经验值 | ⚠️ 需验证 |

   ### 第3层参数
   | 参数 | 当前值 | 来源 | 状态 |
   |------|--------|------|------|
   | `MR_RR_MIN` | 1.3 | 成本覆盖计算 | ✅ |
   | `TF_RR_MIN` | 1.8 | 成本覆盖计算 | ✅ |
   | `MR_DEVIATION_MIN` | 0.04% | 手续费覆盖 | ✅ |
   | `TF_PULLBACK_PCT` | 25% | 经验值 | ⚠️ 需验证 |
   | `STOP_BUFFER_ATR` | 0.5 | 波动率分析 | ✅ |
   | `TIME_STOP_MR` | 180秒 | 持仓时间分布 | ✅ |
   | `TIME_STOP_TF` | 600秒 | 持仓时间分布 | ✅ |

3. **统计汇总**
   ```
   总参数数: X
   有明确来源: X
   需要验证: X
   
   状态: [通过/需要减少参数/需要补充来源]
   ```

4. **来源验证**
   
   对于每个标记为"需验证"的参数：
   ```python
   # 示例：验证OBSERVE_WINDOW
   windows = [30, 45, 60, 75, 90]
   results = []
   
   for w in windows:
       accuracy = test_classification_accuracy(observe_window=w)
       results.append((w, accuracy))
   
   # 选择最优，但检查敏感度
   best_window = max(results, key=lambda x: x[1])
   sensitivity = std([r[1] for r in results])
   
   if sensitivity > 0.05:
       print(f"警告: OBSERVE_WINDOW敏感度高: {sensitivity}")
   ```

5. **生成报告**
   ```bash
   python -m utils.param_report --output reports/parameters.md
   ```

## 输出格式

```markdown
# 参数审计报告

## 总结
- 总参数数: 15
- 限制: <= 15
- 状态: ✅ 通过

## 详细清单
[表格]

## 敏感性分析
[图表]

## 建议
- 参数X可以移除（与Y高度相关）
- 参数Z需要更多样本验证
```

## 警告规则

- 🔴 参数数 > 15: 必须减少
- 🟡 参数数 > 12: 审查必要性
- 🟡 有参数无明确来源: 需要补充
- 🟡 有参数敏感度 > 0.05: 需要验证

## 命令行用法

```bash
# 扫描参数
python -m utils.param_scanner

# 完整审计
python -m utils.param_audit

# 敏感性测试
python -m utils.param_sensitivity --param OBSERVE_WINDOW --range 30,90,15
```
