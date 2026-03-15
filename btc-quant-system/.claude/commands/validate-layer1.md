# 验证第1层：环境过滤

## 任务
验证环境过滤层的有效性。

## 执行步骤

1. **检查模块存在**
   ```bash
   ls src/layers/environment.py
   ```

2. **运行单元测试**
   ```bash
   python -m pytest tests/test_layer1.py -v --tb=short
   ```

3. **验证标准检查**

   ### 3.1 休眠有效性
   - 统计休眠时段内如果强行交易的净收益
   - 要求：亏损占比 > 55%
   
   ```python
   # 检查逻辑
   hibernation_periods = get_hibernation_periods()
   forced_trades = simulate_trades_during(hibernation_periods)
   loss_ratio = sum(1 for t in forced_trades if t.pnl < 0) / len(forced_trades)
   assert loss_ratio > 0.55, f"休眠有效性不足: {loss_ratio:.2%}"
   ```

   ### 3.2 方向偏向有效性
   - 统计方向偏向与后续清算方向的一致率
   - 要求：一致率 > 55%
   
   ```python
   # 检查逻辑
   bias_signals = get_direction_bias_signals()
   actual_liquidations = get_subsequent_liquidations(window=4h)
   consistency = calculate_consistency(bias_signals, actual_liquidations)
   assert consistency > 0.55, f"方向有效性不足: {consistency:.2%}"
   ```

   ### 3.3 样本数量
   - 要求：至少300个验证样本
   
4. **生成验证报告**
   ```bash
   python -m reports.generate --layer 1 --output reports/layer1_validation.md
   ```

5. **输出结果**
   - 如果全部通过：`✅ 第1层验证通过，可以开发第2层`
   - 如果失败：分析原因，列出需要修复的问题

## 通过标准
- [ ] 休眠有效性 > 55%
- [ ] 方向偏向一致率 > 55%
- [ ] 样本数 >= 300
