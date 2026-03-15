# 分析清算数据

## 任务
分析清算数据的统计特征，验证"清算作为分类器"的假设。

## 核心假设

```
假设：清算数据可以区分"真突破"和"过度反应"

验证方法：
  1. 收集冲击事件
  2. 按清算量分组
  3. 统计后续价格行为
  4. 计算分类准确率
```

## 执行步骤

1. **加载数据**
   ```python
   # 加载冲击事件和清算数据
   impacts = load_impacts(start, end)
   liquidations = load_liquidations(start, end)
   ```

2. **匹配清算与冲击**
   ```python
   for impact in impacts:
       # 找到冲击后60秒内的清算
       impact_liqs = get_liquidations_in_window(
           liquidations,
           start=impact.timestamp,
           end=impact.timestamp + 60_000  # 60秒
       )
       
       # 筛选与冲击方向相关的清算
       if impact.direction == "down":
           # 下跌：关注多头被清算
           relevant_liqs = [l for l in impact_liqs if l.side == "SELL"]
       else:
           relevant_liqs = [l for l in impact_liqs if l.side == "BUY"]
       
       impact.liq_count = len(relevant_liqs)
       impact.liq_volume = sum(l.quantity for l in relevant_liqs)
   ```

3. **分组统计**
   ```python
   # 按清算量分组
   high_liq = [i for i in impacts if i.liq_count >= 5]
   low_liq = [i for i in impacts if i.liq_count <= 2]
   mid_liq = [i for i in impacts if 2 < i.liq_count < 5]
   
   print(f"高清算冲击: {len(high_liq)}")
   print(f"低清算冲击: {len(low_liq)}")
   print(f"中等清算冲击: {len(mid_liq)}")
   ```

4. **后续价格分析**
   ```python
   def analyze_subsequent_price(impact, window_minutes=5):
       """分析冲击后的价格行为"""
       
       future_prices = get_prices(
           start=impact.timestamp + 60_000,  # 观察期后
           end=impact.timestamp + window_minutes * 60_000
       )
       
       max_continuation = max(future_prices) - impact.price if impact.direction == "up" \
                          else impact.price - min(future_prices)
       
       max_reversion = impact.price - min(future_prices) if impact.direction == "up" \
                       else max(future_prices) - impact.price
       
       # 判定
       if max_continuation > impact.amplitude * 0.5:
           return "continuation"  # 趋势延续
       elif max_reversion > impact.amplitude * 0.5:
           return "reversion"  # 均值回归
       else:
           return "neutral"  # 无明显方向
   ```

5. **统计结果**
   ```python
   # 高清算组
   high_liq_continuation = sum(1 for i in high_liq 
                                if analyze_subsequent_price(i) == "continuation")
   high_liq_continuation_rate = high_liq_continuation / len(high_liq)
   
   # 低清算组
   low_liq_reversion = sum(1 for i in low_liq 
                           if analyze_subsequent_price(i) == "reversion")
   low_liq_reversion_rate = low_liq_reversion / len(low_liq)
   
   print(f"高清算→趋势延续率: {high_liq_continuation_rate:.2%}")
   print(f"低清算→均值回归率: {low_liq_reversion_rate:.2%}")
   ```

6. **可视化**
   ```python
   # 清算量分布
   plot_histogram(
       [i.liq_count for i in impacts],
       title="冲击后清算数量分布",
       xlabel="清算笔数",
       ylabel="频次"
   )
   
   # 清算量 vs 后续价格变动
   plot_scatter(
       x=[i.liq_count for i in impacts],
       y=[get_price_change_after(i, minutes=5) for i in impacts],
       title="清算量 vs 后续价格变动",
       xlabel="清算笔数",
       ylabel="5分钟后价格变动%"
   )
   ```

7. **验证假设**
   ```python
   # 核心验证
   assert high_liq_continuation_rate > 0.60, \
       f"高清算趋势延续率不足: {high_liq_continuation_rate:.2%}"
   
   assert low_liq_reversion_rate > 0.55, \
       f"低清算回归率不足: {low_liq_reversion_rate:.2%}"
   
   # 分类提升
   random_accuracy = 0.50
   our_accuracy = (high_liq_continuation_rate + low_liq_reversion_rate) / 2
   improvement = our_accuracy - random_accuracy
   
   assert improvement > 0.10, \
       f"分类提升不足: {improvement:.2%}"
   
   print("✅ 清算分类假设验证通过")
   ```

## 输出报告

```markdown
# 清算数据分析报告

## 数据概览
- 分析期间: YYYY-MM-DD 至 YYYY-MM-DD
- 总冲击事件: XXX
- 总清算事件: XXX

## 清算分布
[直方图]

## 分类准确率
| 组别 | 样本数 | 预期行为 | 实际率 | 状态 |
|------|--------|----------|--------|------|
| 高清算 (>=5) | XX | 趋势延续 | XX% | ✅/❌ |
| 低清算 (<=2) | XX | 均值回归 | XX% | ✅/❌ |

## 清算阈值优化
[不同阈值的准确率曲线]

最优阈值: X笔

## 结论
[假设验证/不验证] + 分析
```

## 命令行用法

```bash
# 分析最近7天
python -m analysis.liquidation --days 7

# 分析指定日期
python -m analysis.liquidation --start 2024-01-01 --end 2024-01-31

# 输出详细报告
python -m analysis.liquidation --days 30 --output reports/liq_analysis.md --verbose
```

## 关键发现模板

运行后应能回答：

1. 清算数量的分布是什么样的？（均值、中位数、分位数）
2. "高清算"和"低清算"的最佳分界点是什么？
3. 分类准确率是否足够支撑策略？
4. 不同时段（亚洲盘/欧美盘）是否有差异？
5. 清算量与价格变动的相关性如何？
