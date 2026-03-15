# 运行回测

## 任务
运行完整的回测验证，生成绩效报告。

## 用法

### 基础回测
```bash
python -m backtest.run --start 2024-01-01 --end 2024-06-30
```

### 指定策略
```bash
# 只回测均值回归
python -m backtest.run --strategy mean_reversion

# 只回测趋势跟随
python -m backtest.run --strategy trend_follow
```

### 指定信号等级
```bash
python -m backtest.run --grade A      # 只A级
python -m backtest.run --grade A,B    # A和B级
```

## 执行步骤

1. **检查数据完整性**
   ```python
   data_check = verify_data_integrity(start, end)
   if not data_check.passed:
       print(f"数据缺失: {data_check.missing_periods}")
       return
   ```

2. **加载数据**
   ```python
   liquidations = load_liquidation_data(start, end)
   trades = load_trade_data(start, end)
   depth = load_depth_data(start, end)
   ```

3. **事件驱动回测**
   ```python
   engine = BacktestEngine(
       cost=0.0015,          # 0.15%成本
       slippage=0.0002,      # 0.02%滑点
       initial_capital=10000
   )
   
   for event in iterate_events(start, end):
       # 第1层：环境判断
       env = layer1.evaluate(event.timestamp)
       if env.status == "休眠":
           continue
       
       # 第2层：冲击检测和分类
       impact = layer2.detect_impact(event)
       if impact:
           classification = layer2.classify(impact, liquidations)
           
           # 第3层：执行
           if classification.strategy != "放弃":
               signal = layer3.generate_signal(classification, env)
               if signal:
                   engine.execute(signal)
       
       # 管理持仓
       engine.manage_positions(event.timestamp)
   ```

4. **计算绩效指标**
   ```python
   metrics = {
       "total_trades": engine.total_trades,
       "win_rate": engine.wins / engine.total_trades,
       "avg_rr_ratio": engine.total_profit / engine.total_loss,
       "net_return": engine.final_capital / engine.initial_capital - 1,
       "sharpe_ratio": calculate_sharpe(engine.daily_returns),
       "max_drawdown": calculate_max_drawdown(engine.equity_curve),
       "avg_trades_per_day": engine.total_trades / trading_days,
       "cost_ratio": engine.total_costs / engine.gross_profit
   }
   ```

5. **生成报告**
   - 绩效摘要
   - 月度收益表
   - 回撤曲线
   - 交易分布（按时间、按等级）
   - 最大盈利/亏损交易分析

## 输出
```
reports/backtest_{start}_{end}.md
reports/backtest_{start}_{end}_equity.png
reports/backtest_{start}_{end}_trades.csv
```

## 关键检查点
- [ ] 成本扣除正确（0.15%双边）
- [ ] 无未来数据泄露
- [ ] 时间止损正确执行
- [ ] 熔断规则正确触发
