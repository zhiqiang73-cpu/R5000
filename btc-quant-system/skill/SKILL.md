---
name: quant-trading-dev
description: "加密货币量化交易系统开发skill。当用户涉及以下任务时使用：开发交易策略、回测系统、数据采集、清算分析、信号生成、风控模块、订单执行、WebSocket连接、Binance API、资金费率分析、仓位管理。关键词包括：量化、交易、回测、清算、liquidation、CVD、订单流、aggTrade、资金费率、funding rate、止损、止盈、Sharpe、盈亏比、胜率。即使用户没有明确说'量化'，只要涉及自动化交易逻辑开发，都应使用此skill。"
---

# 加密货币量化交易系统开发

## 核心原则

### 1. 永远问"你在赢谁"

```
在写任何代码之前，必须能回答：
- 这个信号能帮我识别谁在被迫交易？
- 我的对手是谁？他们为什么会亏钱给我？
- 我的边缘有多大？能覆盖手续费吗？
```

### 2. 成本优先

```python
# 所有收益计算必须扣除真实成本
TAKER_FEE = 0.0005  # 0.05%单边
TOTAL_COST = 0.0015  # 0.15%双边（含滑点）

def calculate_net_pnl(gross_pnl, position_value):
    cost = position_value * TOTAL_COST
    return gross_pnl - cost
```

### 3. 模块化验证

```
每个模块必须独立验证通过才能继续。
不通过 → 停下来修复
不允许 → 跳过验证继续开发
```

---

## 数据源速查

### Binance WebSocket

```python
# 清算流（核心）
WSS_LIQUIDATION = "wss://fstream.binance.com/ws/!forceOrder@arr"

# 逐笔成交
WSS_AGGTRADE = "wss://fstream.binance.com/ws/btcusdt@aggTrade"

# 深度数据
WSS_DEPTH = "wss://fstream.binance.com/ws/btcusdt@depth@100ms"

# K线
WSS_KLINE = "wss://fstream.binance.com/ws/btcusdt@kline_1m"
```

### REST API

```python
# 资金费率
GET_FUNDING = "/fapi/v1/fundingRate"

# 持仓量
GET_OI = "/fapi/v1/openInterest"

# 历史K线
GET_KLINES = "/fapi/v1/klines"
```

### 连接模板

```python
import asyncio
import websockets
import json

async def connect_liquidation_stream():
    uri = "wss://fstream.binance.com/ws/!forceOrder@arr"
    
    async with websockets.connect(uri) as ws:
        while True:
            try:
                msg = await ws.recv()
                data = json.loads(msg)
                yield parse_liquidation(data)
            except websockets.ConnectionClosed:
                await asyncio.sleep(1)
                continue

def parse_liquidation(data):
    """解析清算数据"""
    o = data.get("o", {})
    return {
        "timestamp": o.get("T"),
        "symbol": o.get("s"),
        "side": o.get("S"),  # BUY=空头被清算, SELL=多头被清算
        "quantity": float(o.get("q", 0)),
        "price": float(o.get("p", 0)),
        "avg_price": float(o.get("ap", 0)),
    }
```

---

## 常用计算

### CVD (Cumulative Volume Delta)

```python
def calculate_cvd(trades):
    """
    计算累积成交量差
    正值 = 买入主导
    负值 = 卖出主导
    """
    cvd = 0
    for trade in trades:
        if trade['is_buyer_maker']:
            # 买方是maker = 卖方是taker = 卖出
            cvd -= trade['quantity']
        else:
            # 卖方是maker = 买方是taker = 买入
            cvd += trade['quantity']
    return cvd
```

### VWAP (Volume Weighted Average Price)

```python
def calculate_vwap(trades):
    """计算成交量加权平均价"""
    total_value = sum(t['price'] * t['quantity'] for t in trades)
    total_volume = sum(t['quantity'] for t in trades)
    return total_value / total_volume if total_volume > 0 else 0
```

### ATR (Average True Range)

```python
def calculate_atr(candles, period=14):
    """计算平均真实波动范围"""
    true_ranges = []
    for i in range(1, len(candles)):
        high = candles[i]['high']
        low = candles[i]['low']
        prev_close = candles[i-1]['close']
        
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        true_ranges.append(tr)
    
    return sum(true_ranges[-period:]) / period
```

### 资金费率Z-Score

```python
def calculate_fr_zscore(current_fr, history_7d):
    """计算资金费率的Z分数"""
    mean_fr = sum(history_7d) / len(history_7d)
    std_fr = (sum((x - mean_fr)**2 for x in history_7d) / len(history_7d)) ** 0.5
    
    if std_fr == 0:
        return 0
    
    return (current_fr - mean_fr) / std_fr
```

---

## 回测框架

### 事件驱动回测

```python
class BacktestEngine:
    def __init__(self, initial_capital=10000, cost=0.0015):
        self.capital = initial_capital
        self.cost = cost
        self.positions = []
        self.trades = []
        self.equity_curve = []
    
    def run(self, events):
        for event in events:
            # 更新持仓
            self._update_positions(event)
            
            # 生成信号（由策略决定）
            signal = self.strategy.on_event(event)
            
            if signal:
                self._execute(signal, event)
            
            # 记录权益
            equity = self._calculate_equity(event)
            self.equity_curve.append({
                'timestamp': event.timestamp,
                'equity': equity
            })
    
    def _execute(self, signal, event):
        # 计算成本
        cost = signal.position_value * self.cost
        
        # 记录交易
        self.trades.append({
            'timestamp': event.timestamp,
            'side': signal.side,
            'price': event.price,
            'quantity': signal.quantity,
            'cost': cost
        })
```

### 绩效指标

```python
def calculate_metrics(trades, equity_curve):
    """计算绩效指标"""
    
    # 胜率
    wins = sum(1 for t in trades if t['pnl'] > 0)
    win_rate = wins / len(trades) if trades else 0
    
    # 盈亏比
    avg_win = mean([t['pnl'] for t in trades if t['pnl'] > 0]) or 0
    avg_loss = abs(mean([t['pnl'] for t in trades if t['pnl'] < 0])) or 1
    rr_ratio = avg_win / avg_loss
    
    # Sharpe Ratio（年化）
    returns = calculate_daily_returns(equity_curve)
    sharpe = (mean(returns) / std(returns)) * (252 ** 0.5) if std(returns) > 0 else 0
    
    # 最大回撤
    max_dd = calculate_max_drawdown(equity_curve)
    
    return {
        'total_trades': len(trades),
        'win_rate': win_rate,
        'rr_ratio': rr_ratio,
        'sharpe_ratio': sharpe,
        'max_drawdown': max_dd,
        'net_return': (equity_curve[-1]['equity'] / equity_curve[0]['equity']) - 1
    }
```

---

## 风控模板

### 仓位计算

```python
def calculate_position_size(
    grade: str,
    stop_distance: float,
    current_price: float,
    total_capital: float,
    max_leverage: float = 3.0
) -> float:
    """
    基于风险的仓位计算
    """
    risk_pct = {
        'A': 0.015,  # 1.5%
        'B': 0.010,  # 1.0%
        'C': 0.005,  # 0.5%
    }.get(grade, 0)
    
    risk_usd = total_capital * risk_pct
    position_usd = risk_usd / (stop_distance / current_price)
    
    # 杠杆限制
    max_position = total_capital * max_leverage
    position_usd = min(position_usd, max_position)
    
    return position_usd / current_price  # 返回数量
```

### 熔断检查

```python
class CircuitBreaker:
    def __init__(self, capital):
        self.capital = capital
        self.daily_loss = 0
        self.recent_trades = []
    
    def check(self) -> tuple[bool, str]:
        """返回 (是否触发熔断, 原因)"""
        
        # 当日亏损 > 3%
        if self.daily_loss > self.capital * 0.03:
            return True, "daily_loss_limit"
        
        # 最近10笔胜率 < 30%
        if len(self.recent_trades) >= 10:
            wins = sum(1 for t in self.recent_trades[-10:] if t['pnl'] > 0)
            if wins < 3:
                return True, "low_win_rate"
        
        # 连续3笔止损
        if len(self.recent_trades) >= 3:
            if all(t['pnl'] < 0 for t in self.recent_trades[-3:]):
                return True, "consecutive_losses"
        
        return False, None
```

---

## 禁止事项

在开发过程中，始终检查以下红线：

```
❌ 不要使用技术指标（RSI、MACD等）作为主要信号
❌ 不要在回测中使用未来数据
❌ 不要忽视手续费和滑点
❌ 不要过度优化参数（参数 <= 15个）
❌ 不要跳过验证步骤
❌ 不要在趋势中做均值回归
❌ 不要人工干预自动交易决策
```

---

## 验证清单

每个模块完成后，必须检查：

### 第1层
- [ ] 休眠有效性 > 55%
- [ ] 方向偏向一致率 > 55%
- [ ] 样本数 >= 300

### 第2层
- [ ] 有清算→趋势延续 > 60%
- [ ] 无清算→价格回归 > 55%
- [ ] 分类提升 vs 随机 > 10%

### 第3层
- [ ] 均值回归净期望 > 0
- [ ] 趋势跟随净期望 > 0
- [ ] Sharpe > 0.8
- [ ] 最大回撤 < 15%

### 集成
- [ ] Walk-Forward所有fold盈利
- [ ] 参数扰动±20%后Sharpe变化 < 0.25
- [ ] 极端行情存活

---

## 文件组织

```
项目根目录/
├── CLAUDE.md           # 必须阅读的大脑文档
├── src/
│   ├── layers/         # 三层逻辑
│   │   ├── environment.py
│   │   ├── classifier.py
│   │   └── executor.py
│   ├── data/           # 数据采集
│   └── risk/           # 风控
├── tests/              # 每层独立测试
└── reports/            # 验证报告
```

---

## 调试技巧

### 检查清算数据

```python
# 快速检查清算流是否正常
async def debug_liquidation_stream(duration=60):
    count = 0
    async for liq in connect_liquidation_stream():
        print(f"清算: {liq['side']} {liq['quantity']} @ {liq['price']}")
        count += 1
        if time.time() - start > duration:
            break
    print(f"总计 {count} 笔清算 / {duration}秒")
```

### 检查信号质量

```python
def debug_signals(signals, lookback_days=7):
    """快速检查信号质量"""
    
    print(f"总信号数: {len(signals)}")
    print(f"日均信号: {len(signals) / lookback_days:.1f}")
    
    by_grade = Counter(s['grade'] for s in signals)
    print(f"等级分布: {dict(by_grade)}")
    
    by_strategy = Counter(s['strategy'] for s in signals)
    print(f"策略分布: {dict(by_strategy)}")
```

---

## 常见问题

### Q: WebSocket断开怎么办？
A: 内置重连逻辑，指数退避（1s, 2s, 4s, ...max 30s）

### Q: 清算数据不够怎么办？
A: 正常。清算是稀疏事件。一天可能只有几十到几百笔。这是正确的。

### Q: 回测结果和实盘差异大怎么办？
A: 检查：1)滑点模型 2)成交假设 3)时间戳对齐 4)数据质量

### Q: 参数太多怎么办？
A: 回顾每个参数是否有逻辑来源。没有来源的参数考虑移除或硬编码。
