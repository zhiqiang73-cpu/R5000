# 验证第2层：冲击检测 + 分类

## 任务
验证冲击检测和分类层的有效性。

## 前置条件
- 第1层验证已通过
- 已有至少7天的清算流历史数据

## 执行步骤

1. **检查模块存在**
   ```bash
   ls src/layers/classifier.py
   ```

2. **运行单元测试**
   ```bash
   python -m pytest tests/test_layer2.py -v --tb=short
   ```

3. **验证标准检查**

   ### 3.1 冲击检测召回率
   - 用历史数据标注已知的显著价格变动
   - 检查算法能否检测到
   - 要求：召回率 > 80%
   
   ```python
   # 检查逻辑
   known_impacts = load_labeled_impacts()  # 人工标注的冲击事件
   detected_impacts = run_impact_detection(historical_data)
   recall = len(set(known_impacts) & set(detected_impacts)) / len(known_impacts)
   assert recall > 0.80, f"召回率不足: {recall:.2%}"
   ```

   ### 3.2 清算分类准确性（核心）
   - 统计"有清算"的冲击中，趋势延续的比例
   - 统计"无清算"的冲击中，价格回归的比例
   
   ```python
   # 分类后续价格变动
   impacts_with_liq = [i for i in impacts if i.liquidation_count >= 5]
   impacts_no_liq = [i for i in impacts if i.liquidation_count <= 2]
   
   # 有清算 → 应该趋势延续
   trend_continuation_rate = calculate_continuation_rate(impacts_with_liq)
   assert trend_continuation_rate > 0.60, f"趋势延续率不足: {trend_continuation_rate:.2%}"
   
   # 无清算 → 应该回归
   mean_reversion_rate = calculate_reversion_rate(impacts_no_liq)
   assert mean_reversion_rate > 0.55, f"回归率不足: {mean_reversion_rate:.2%}"
   ```

   ### 3.3 分类提升
   - 对比使用清算分类 vs 随机分类
   - 要求：提升 > 10%
   
   ```python
   accuracy_with_liq = (trend_continuation_rate + mean_reversion_rate) / 2
   accuracy_random = 0.50
   improvement = accuracy_with_liq - accuracy_random
   assert improvement > 0.10, f"分类提升不足: {improvement:.2%}"
   ```

   ### 3.4 样本分布
   - 检查各类别样本是否足够
   - "有清算"样本 >= 50
   - "无清算"样本 >= 50
   - "不确定"样本记录但不用于验证

4. **生成验证报告**
   ```bash
   python -m reports.generate --layer 2 --output reports/layer2_validation.md
   ```

5. **输出结果**
   - 如果全部通过：`✅ 第2层验证通过，可以开发第3层`
   - 如果失败：分析原因，特别关注清算阈值是否需要调整

## 通过标准
- [ ] 冲击检测召回率 > 80%
- [ ] 有清算→趋势延续 > 60%
- [ ] 无清算→价格回归 > 55%
- [ ] 分类提升 vs 随机 > 10%
- [ ] 各类别样本 >= 50

## 注意事项
- 不要过度优化阈值，保持简单
- 清算数据是核心，CVD等只是辅助
- 如果验证不通过，优先检查数据质量
