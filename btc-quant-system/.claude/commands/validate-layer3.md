# 验证第3层：执行 + 风控

## 任务
验证执行层和风控层的有效性。

## 前置条件
- 第1层和第2层验证已通过
- 已有至少30天的历史数据用于回测

## 执行步骤

1. **检查模块存在**
   ```bash
   ls src/layers/executor.py
   ls src/risk/position.py
   ls src/risk/circuit_breaker.py
   ```

2. **运行单元测试**
   ```bash
   python -m pytest tests/test_layer3.py -v --tb=short
   ```

3. **回测验证**

   ### 3.1 均值回归策略验证
   ```python
   # 只回测均值回归信号
   mr_signals = filter_signals(signals, strategy="mean_reversion")
   mr_results = backtest(mr_signals, cost=0.0015)  # 0.15%成本
   
   assert mr_results.net_expectation > 0, "均值回归净期望为负"
   assert mr_results.avg_rr_ratio > 1.3, "均值回归盈亏比不足"
   assert mr_results.win_rate > 0.45, "均值回归胜率过低"
   ```

   ### 3.2 趋势跟随策略验证
   ```python
   # 只回测趋势跟随信号
   tf_signals = filter_signals(signals, strategy="trend_follow")
   tf_results = backtest(tf_signals, cost=0.0015)
   
   assert tf_results.net_expectation > 0, "趋势跟随净期望为负"
   assert tf_results.avg_rr_ratio > 1.8, "趋势跟随盈亏比不足"
   assert tf_results.win_rate > 0.40, "趋势跟随胜率过低"
   ```

   ### 3.3 综合验证
   ```python
   all_results = backtest(all_signals, cost=0.0015)
   
   assert all_results.sharpe_ratio > 0.8, f"Sharpe不足: {all_results.sharpe_ratio}"
   assert all_results.max_drawdown < 0.15, f"最大回撤过大: {all_results.max_drawdown}"
   assert all_results.win_rate > 0.42, f"综合胜率不足: {all_results.win_rate}"
   ```

   ### 3.4 信号分级验证
   ```python
   # A/B/C三级都应该有正期望
   for grade in ['A', 'B', 'C']:
       grade_signals = filter_signals(signals, grade=grade)
       grade_results = backtest(grade_signals, cost=0.0015)
       assert grade_results.net_expectation > 0, f"{grade}级净期望为负"
   
   # A级收益应该 > B级 > C级
   assert results_A.avg_return > results_B.avg_return > results_C.avg_return
   ```

   ### 3.5 风控验证
   ```python
   # 模拟极端行情
   extreme_periods = [
       "2020-03-12",  # COVID崩盘
       "2022-05-09",  # LUNA崩盘
       "2024-08-05",  # 日元套利崩盘
   ]
   
   for period in extreme_periods:
       period_results = backtest(signals, period=period)
       assert period_results.max_drawdown < 0.20, f"{period}回撤过大"
       # 熔断应该触发
       assert period_results.circuit_breaker_triggered, f"{period}熔断未触发"
   ```

4. **统计报告**
   ```bash
   python -m reports.generate --layer 3 --output reports/layer3_validation.md
   ```

   报告应包含：
   - 总交易数
   - 胜率
   - 平均盈亏比
   - 净期望值
   - Sharpe Ratio
   - 最大回撤
   - 月度收益分布
   - 各等级信号占比

5. **输出结果**
   - 如果全部通过：`✅ 第3层验证通过，可以进入集成测试`
   - 如果失败：分析原因，检查入场/止损逻辑

## 通过标准
- [ ] 均值回归净期望 > 0
- [ ] 趋势跟随净期望 > 0
- [ ] 综合Sharpe > 0.8
- [ ] 最大回撤 < 15%
- [ ] 综合胜率 > 42%
- [ ] A/B/C三级均有正期望
- [ ] 极端行情熔断正确触发

## 注意事项
- 回测必须扣除真实成本（0.15%）
- 回测必须包含滑点模拟
- 不要在回测中使用未来数据
