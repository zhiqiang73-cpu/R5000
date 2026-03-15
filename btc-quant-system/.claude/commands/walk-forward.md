# Walk-Forward 验证

## 任务
运行Walk-Forward验证，防止过拟合，确保策略稳健性。

## 核心原理

```
传统回测的问题：
  用全部数据调参 → 在同一数据上验证
  结果：过拟合，实盘失效

Walk-Forward方法：
  数据分成多段 → 每段分别调参和验证
  结果：模拟真实的"先优化后交易"过程
```

## 执行步骤

1. **数据划分**
   ```python
   # 假设有6个月数据
   # 4折验证：每折用2个月训练，1个月测试
   
   folds = [
       {"train": ["月1", "月2"], "test": "月3"},
       {"train": ["月2", "月3"], "test": "月4"},
       {"train": ["月3", "月4"], "test": "月5"},
       {"train": ["月4", "月5"], "test": "月6"},
   ]
   ```

2. **每折执行**
   ```python
   fold_results = []
   
   for fold in folds:
       # 用训练集统计参数
       params = optimize_params(train_data=fold["train"])
       
       # 用测试集验证（完全独立）
       test_result = backtest(
           data=fold["test"],
           params=params,
           cost=0.0015
       )
       
       fold_results.append(test_result)
   ```

3. **汇总验证**
   ```python
   # 关键：只用测试集结果评估！
   
   all_test_trades = concat([f.trades for f in fold_results])
   
   # 计算综合指标
   combined_sharpe = calculate_sharpe(all_test_trades)
   combined_max_dd = calculate_max_drawdown(all_test_trades)
   combined_net_return = calculate_net_return(all_test_trades)
   
   # 检查一致性
   fold_sharpes = [f.sharpe for f in fold_results]
   sharpe_std = std(fold_sharpes)
   ```

4. **通过标准检查**
   ```python
   # 所有fold必须盈利
   for i, fold in enumerate(fold_results):
       assert fold.net_return > 0, f"Fold {i+1} 亏损"
   
   # 综合指标
   assert combined_sharpe > 0.8, f"综合Sharpe不足: {combined_sharpe}"
   assert combined_max_dd < 0.20, f"最大回撤过大: {combined_max_dd}"
   
   # 稳定性
   assert sharpe_std < 0.5, f"Fold间波动过大: {sharpe_std}"
   ```

5. **参数稳健性测试**
   ```python
   # 对每个核心参数做±20%扰动
   core_params = [
       "impact_threshold",
       "liq_count_threshold",
       "stop_multiplier",
       "rr_threshold"
   ]
   
   for param in core_params:
       perturbations = [0.8, 0.9, 1.0, 1.1, 1.2]
       results = []
       
       for mult in perturbations:
           perturbed_params = params.copy()
           perturbed_params[param] *= mult
           result = backtest(test_data, perturbed_params)
           results.append(result.sharpe)
       
       sharpe_std = std(results)
       assert sharpe_std < 0.25, f"参数 {param} 敏感度过高: {sharpe_std}"
   ```

## 输出报告

```markdown
# Walk-Forward 验证报告

## 总体结果
- 综合Sharpe: X.XX
- 综合最大回撤: X.X%
- 综合净收益: X.X%

## 各Fold结果
| Fold | 训练期 | 测试期 | Sharpe | 回撤 | 净收益 |
|------|--------|--------|--------|------|--------|
| 1    | M1-M2  | M3     | 0.XX   | X%   | X%     |
| 2    | M2-M3  | M4     | 0.XX   | X%   | X%     |
| ...  | ...    | ...    | ...    | ...  | ...    |

## 参数稳健性
| 参数 | 扰动后Sharpe标准差 | 状态 |
|------|---------------------|------|
| impact_threshold | 0.XX | ✅/❌ |
| ...  | ...    | ...  |

## 结论
[通过/不通过] + 原因分析
```

## 命令行用法

```bash
# 运行Walk-Forward验证
python -m backtest.walk_forward --folds 4 --train-months 2 --test-months 1

# 运行参数稳健性测试
python -m backtest.param_sensitivity --params all --perturbation 0.2
```

## 通过标准
- [ ] 所有Fold净收益 > 0
- [ ] 综合Sharpe > 0.8
- [ ] 综合最大回撤 < 20%
- [ ] Fold间Sharpe标准差 < 0.5
- [ ] 核心参数扰动后Sharpe标准差 < 0.25
