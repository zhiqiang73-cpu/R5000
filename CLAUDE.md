# BTCUSDT 短线量化交易系统

## 核心哲学：你在赢谁的钱？

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                  │
│     交易是零和游戏。你赚的每一分钱，都是别人亏的。              │
│                                                                  │
│     本系统的对手：被清算引擎强制平仓的高杠杆散户                │
│     他们的弱点：必须以任何价格成交，无法等待                    │
│     我们的优势：可以选择时机，等待他们被迫交易                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**绝对不要忘记这个前提。** 每一个信号、每一个参数、每一个决策，都要回答：
- "这个信号能帮我识别别人的被迫交易吗？"
- "我凭什么能赢？"

---

## 系统架构：3层决策链

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                  │
│  第1层：环境过滤                                                │
│  ──────────────                                                 │
│  目的：只在有优势的环境中交易                                    │
│  输入：资金费率、Open Interest、清算流、成交量                  │
│  输出：可交易(+方向偏向) 或 休眠                                │
│                                                                  │
│                         ↓                                        │
│                                                                  │
│  第2层：冲击检测 + 分类                                          │
│  ────────────────────────                                       │
│  目的：检测价格冲击，用清算数据判断是过度反应还是真突破          │
│  输入：aggTrade流、订单簿、Liquidation Stream                   │
│  输出：过度反应(均值回归) / 真突破(趋势跟随) / 放弃             │
│                                                                  │
│                         ↓                                        │
│                                                                  │
│  第3层：执行 + 风控                                              │
│  ────────────────────                                           │
│  目的：精确入场，严格止损，仓位管理                              │
│  输入：分类结果、市场状态、账户状态                             │
│  输出：订单指令、风控动作                                       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 第1层：环境过滤

### 目的
不是预测方向，而是判断"现在是否值得交易"。

### 核心逻辑

```python
# 伪代码
def evaluate_environment():
    
    # 1. 资金费率极端度（市场拥挤度）
    fr = get_funding_rate()
    fr_zscore = (fr - fr_mean_7d) / fr_std_7d
    
    if fr_zscore > +2.0:
        direction_bias = "偏空"  # 多头过热，容易被清算
        liquidation_side = "多头"
    elif fr_zscore < -2.0:
        direction_bias = "偏多"  # 空头过热
        liquidation_side = "空头"
    else:
        direction_bias = "中性"
        liquidation_side = None
    
    # 2. 资金费率结算时间（重要！）
    hours_to_settlement = get_hours_to_next_funding()
    
    if hours_to_settlement < 2:
        # 结算前2小时：套利者活跃，信号复杂
        fr_reliability = 0.6
    elif hours_to_settlement > 6:
        # 刚结算完：FR信息不足
        fr_reliability = 0.4
    else:
        fr_reliability = 1.0
    
    # 3. Open Interest变化（是否有人在加仓）
    oi_change_1h = get_oi_change(hours=1)
    oi_change_pct = oi_change_1h / current_oi
    
    if abs(oi_change_pct) > 0.03:  # OI变化超过3%
        activity_level = "高"
    elif abs(oi_change_pct) < 0.01:
        activity_level = "低"
    else:
        activity_level = "正常"
    
    # 4. 成交量状态
    volume_ratio = current_volume_1h / mean_same_hour_7d
    
    if volume_ratio < 0.5:
        return "休眠", "流动性不足"
    
    # 5. 实时清算流活跃度
    recent_liquidations = get_liquidations(minutes=30)
    liq_volume = sum(l.quantity for l in recent_liquidations)
    
    if liq_volume > threshold_high:
        market_stress = "高"
    elif liq_volume < threshold_low:
        market_stress = "低"
    else:
        market_stress = "正常"
    
    # 综合判断
    if volume_ratio < 0.5:
        return "休眠", "流动性真空"
    elif market_stress == "高" and activity_level == "高":
        return "可交易", direction_bias, "高波动环境"
    elif market_stress == "低" and activity_level == "低":
        return "休眠", "市场沉寂"
    else:
        return "可交易", direction_bias, "正常环境"
```

### 输出
- `status`: "可交易" | "休眠"
- `direction_bias`: "偏多" | "偏空" | "中性"
- `liquidation_side`: "多头" | "空头" | None
- `stop_multiplier`: 0.8 - 1.3（根据波动率调整）

### 验证标准
- [ ] 休眠时段内强行交易，净亏损占比 > 55%
- [ ] 方向偏向与后续清算方向一致率 > 55%

---

## 第2层：冲击检测 + 分类

### 目的
检测价格冲击，判断是"过度反应"还是"真突破"。

### 核心创新：用清算数据作为分类器

```
┌─────────────────────────────────────────────────────────────────┐
│  传统方法（你之前的v3）：                                        │
│  用CVD、成交速度、订单簿恢复来判断                              │
│  问题：这些都是"症状"，容易被伪造或误判                        │
│                                                                  │
│  新方法：                                                        │
│  用"是否触发清算"作为核心判断                                   │
│  原因：清算是确定性信号——交易所执行的强制平仓，无法伪造        │
└─────────────────────────────────────────────────────────────────┘
```

### 冲击检测

```python
def detect_impact():
    """检测是否发生价格冲击"""
    
    # 动态阈值：基于近期波动率
    recent_volatility = get_volatility(minutes=60)
    impact_threshold = max(0.15%, recent_volatility * 1.5)
    
    # 30秒内价格变动
    price_change_30s = abs(price_now - price_30s_ago) / price_30s_ago
    
    # 成交量激增
    volume_30s = get_volume(seconds=30)
    volume_baseline = get_volume_percentile(75, lookback_windows=60)
    volume_surge = volume_30s / volume_baseline
    
    if price_change_30s > impact_threshold and volume_surge > 2.0:
        direction = "up" if price_now > price_30s_ago else "down"
        return True, direction, price_change_30s
    
    return False, None, None
```

### 冲击分类（核心逻辑）

```python
def classify_impact(impact_direction, impact_time):
    """
    冲击发生后，等待30-60秒，用清算数据分类
    """
    
    # 等待观察窗口
    wait_seconds = 45  # 可调参数
    time.sleep(wait_seconds)
    
    # 收集清算数据
    liquidations = get_liquidations_since(impact_time)
    
    # 计算清算量（与冲击方向一致的）
    if impact_direction == "down":
        # 下跌冲击：关注多头清算
        relevant_liqs = [l for l in liquidations if l.side == "SELL"]  # 多头被强平
    else:
        # 上涨冲击：关注空头清算
        relevant_liqs = [l for l in liquidations if l.side == "BUY"]  # 空头被强平
    
    liq_volume = sum(l.quantity for l in relevant_liqs)
    liq_value = sum(l.quantity * l.price for l in relevant_liqs)
    
    # 获取同期总成交量作对比
    total_volume = get_volume_since(impact_time)
    liq_ratio = liq_volume / total_volume if total_volume > 0 else 0
    
    # 清算数量阈值
    LIQ_COUNT_THRESHOLD = 5      # 至少5笔清算
    LIQ_RATIO_THRESHOLD = 0.15   # 清算量占总成交量15%以上
    
    # 分类逻辑
    if len(relevant_liqs) >= LIQ_COUNT_THRESHOLD and liq_ratio > LIQ_RATIO_THRESHOLD:
        # 大量清算 → 真突破
        # 清算级联可能继续
        return "真突破", {
            "strategy": "趋势跟随",
            "direction": impact_direction,
            "confidence": min(liq_ratio * 3, 1.0),  # 清算越多，信心越高
            "liq_count": len(relevant_liqs),
            "liq_value": liq_value
        }
    
    elif len(relevant_liqs) <= 2 and liq_ratio < 0.05:
        # 几乎没有清算 → 过度反应
        # 可能是大户的市价单冲击，做市商会把价格推回来
        return "过度反应", {
            "strategy": "均值回归",
            "direction": "opposite_of_" + impact_direction,
            "confidence": 0.6,
            "liq_count": len(relevant_liqs)
        }
    
    else:
        # 中间状态 → 不确定
        return "不确定", {
            "strategy": "放弃",
            "reason": "清算信号不明确"
        }
```

### 补充信号（辅助判断，不是核心）

清算数据是核心，以下信号只用于增强信心：

```python
def get_supplementary_signals(impact_time):
    """补充信号，用于调整confidence"""
    
    signals = {}
    
    # 1. CVD后续行为
    cvd_before = get_cvd_at(impact_time)
    cvd_now = get_cvd_now()
    cvd_delta = cvd_now - cvd_before
    signals['cvd_follows'] = (cvd_delta > 0 and impact_direction == "up") or \
                             (cvd_delta < 0 and impact_direction == "down")
    
    # 2. 订单簿恢复
    depth_consumed = get_depth_consumed_during_impact()
    depth_recovered = get_current_depth() / depth_before_impact
    signals['depth_recovered'] = depth_recovered > 0.7
    
    # 3. 成交速度衰减
    speed_during_impact = get_trade_speed(impact_time, impact_time + 10)
    speed_after = get_trade_speed(impact_time + 30, impact_time + 45)
    signals['speed_decayed'] = speed_after / speed_during_impact < 0.5
    
    return signals
```

### 验证标准
- [ ] "有清算"的冲击中，趋势延续占比 > 60%
- [ ] "无清算"的冲击中，价格回归占比 > 55%
- [ ] 分类准确率提升相比随机 > 10%

---

## 第3层：执行 + 风控

### 均值回归执行

```python
def execute_mean_reversion(impact_data, env_data):
    """
    过度反应 → 均值回归策略
    """
    
    # 方向：与冲击相反
    if impact_data['impact_direction'] == "up":
        side = "SELL"
        entry_price = current_price  # 市价做空
        stop_loss = impact_high + atr_1min * 0.5 * env_data['stop_multiplier']
        target = vwap_3min  # 目标回归到VWAP
    else:
        side = "BUY"
        entry_price = current_price
        stop_loss = impact_low - atr_1min * 0.5 * env_data['stop_multiplier']
        target = vwap_3min
    
    # 盈亏比检查
    risk = abs(entry_price - stop_loss)
    reward = abs(target - entry_price)
    rr_ratio = reward / risk
    
    if rr_ratio < 1.3:
        return None, "盈亏比不足"
    
    # 偏离度检查
    deviation = abs(entry_price - vwap_3min) / vwap_3min
    if deviation < 0.04:  # 0.04%
        return None, "偏离太小，手续费吃掉利润"
    
    # 信号等级
    grade = calculate_grade(
        confidence=impact_data['confidence'],
        rr_ratio=rr_ratio,
        env_activity=env_data['activity_level']
    )
    
    # 仓位大小
    position_size = calculate_position(
        grade=grade,
        stop_distance=risk,
        total_capital=account.balance
    )
    
    return {
        "side": side,
        "entry_type": "MARKET",
        "quantity": position_size,
        "stop_loss": stop_loss,
        "take_profit": target,
        "time_stop": 180,  # 3分钟时间止损
        "grade": grade
    }
```

### 趋势跟随执行

```python
def execute_trend_follow(impact_data, env_data):
    """
    真突破 → 趋势跟随策略
    """
    
    # 方向：与冲击相同，但等回调入场
    if impact_data['impact_direction'] == "up":
        side = "BUY"
        # 等回调25%再入场
        pullback_target = impact_high - (impact_high - impact_start) * 0.25
        entry_type = "LIMIT"
        entry_price = pullback_target
        stop_loss = impact_start - atr_1min * 0.3 * env_data['stop_multiplier']
        # 目标：订单簿聚集点或ATR扩展
        target = find_resistance_cluster() or (impact_high + atr_5min * 1.5)
    else:
        side = "SELL"
        pullback_target = impact_low + (impact_start - impact_low) * 0.25
        entry_type = "LIMIT"
        entry_price = pullback_target
        stop_loss = impact_start + atr_1min * 0.3 * env_data['stop_multiplier']
        target = find_support_cluster() or (impact_low - atr_5min * 1.5)
    
    # 盈亏比检查
    risk = abs(entry_price - stop_loss)
    reward = abs(target - entry_price)
    rr_ratio = reward / risk
    
    if rr_ratio < 1.8:
        return None, "盈亏比不足"
    
    # 清算强度加成
    liq_bonus = min(impact_data['liq_count'] / 20, 0.3)  # 最多+30%信心
    
    grade = calculate_grade(
        confidence=impact_data['confidence'] + liq_bonus,
        rr_ratio=rr_ratio,
        env_activity=env_data['activity_level']
    )
    
    position_size = calculate_position(
        grade=grade,
        stop_distance=risk,
        total_capital=account.balance
    )
    
    return {
        "side": side,
        "entry_type": entry_type,
        "entry_price": entry_price,
        "quantity": position_size,
        "stop_loss": stop_loss,
        "take_profit": target,
        "entry_expiry": 180,  # 限价单3分钟有效
        "time_stop": 600,  # 10分钟时间止损
        "trailing_stop": True,  # 盈利后启用移动止损
        "grade": grade
    }
```

### 信号分级 + 仓位

```python
def calculate_grade(confidence, rr_ratio, env_activity):
    """
    A/B/C三级：不是质量好中差，是确定性高中低
    每一级都有正期望，只是仓位不同
    """
    
    score = 0
    
    # 信心分
    if confidence > 0.8:
        score += 3
    elif confidence > 0.6:
        score += 2
    elif confidence > 0.4:
        score += 1
    
    # 盈亏比分
    if rr_ratio > 2.5:
        score += 3
    elif rr_ratio > 2.0:
        score += 2
    elif rr_ratio > 1.5:
        score += 1
    
    # 环境分
    if env_activity == "高":
        score += 1
    
    # 分级
    if score >= 6:
        return "A"  # 风险1.5%
    elif score >= 4:
        return "B"  # 风险1.0%
    elif score >= 2:
        return "C"  # 风险0.5%
    else:
        return "SKIP"


def calculate_position(grade, stop_distance, total_capital):
    """凯利公式简化版"""
    
    risk_pct = {
        "A": 0.015,
        "B": 0.010,
        "C": 0.005
    }.get(grade, 0)
    
    risk_usd = total_capital * risk_pct
    position = risk_usd / stop_distance
    
    # 杠杆上限：3x
    max_position = total_capital * 3 / current_price
    
    return min(position, max_position)
```

### 风控规则

```python
class RiskManager:
    
    def __init__(self, total_capital):
        self.total_capital = total_capital
        self.daily_loss = 0
        self.recent_trades = []
    
    def check_circuit_breakers(self):
        """熔断检查"""
        
        # 1. 当日累计亏损 > 3%
        if self.daily_loss > self.total_capital * 0.03:
            return "停止当日交易", "daily_loss_limit"
        
        # 2. 最近10笔胜率 < 30%
        if len(self.recent_trades) >= 10:
            wins = sum(1 for t in self.recent_trades[-10:] if t.pnl > 0)
            if wins < 3:
                return "暂停1小时", "low_win_rate"
        
        # 3. 连续3笔止损
        if len(self.recent_trades) >= 3:
            last_3 = self.recent_trades[-3:]
            if all(t.pnl < 0 for t in last_3):
                return "暂停30分钟", "consecutive_losses"
        
        # 4. 单笔亏损 > 2%
        if self.recent_trades and self.recent_trades[-1].pnl < -self.total_capital * 0.02:
            return "暂停30分钟 + 人工确认", "large_single_loss"
        
        return None, None
    
    def check_before_entry(self, signal):
        """入场前检查"""
        
        # 环境与方向一致性
        if signal['side'] == "BUY" and env_data['direction_bias'] == "偏空":
            signal['grade'] = downgrade(signal['grade'])  # 降级
        
        # 单笔风险上限
        if signal['risk_usd'] > self.total_capital * 0.02:
            return False, "单笔风险超限"
        
        return True, None
```

### 验证标准
- [ ] 均值回归策略：净期望值 > 0（扣手续费后）
- [ ] 趋势跟随策略：净期望值 > 0（扣手续费后）
- [ ] 综合胜率 > 42%，平均盈亏比 > 1.5
- [ ] 熔断机制在极端行情中正确触发

---

## 数据源

### 免费实时数据（Binance WebSocket）

| 数据流 | 地址 | 用途 |
|--------|------|------|
| 实时清算 | `wss://fstream.binance.com/ws/!forceOrder@arr` | 核心分类器 |
| 逐笔成交 | `wss://fstream.binance.com/ws/btcusdt@aggTrade` | 订单流分析 |
| 深度数据 | `wss://fstream.binance.com/ws/btcusdt@depth@100ms` | 订单簿恢复 |
| K线 | `wss://fstream.binance.com/ws/btcusdt@kline_1m` | VWAP、ATR |

### REST API

| 接口 | 用途 | 频率 |
|------|------|------|
| `/fapi/v1/fundingRate` | 资金费率 | 每15分钟 |
| `/fapi/v1/openInterest` | 持仓量 | 每5分钟 |
| `/fapi/v1/ticker/24hr` | 24小时统计 | 每分钟 |

### 数据存储

```
data/
├── raw/
│   ├── liquidations/      # 清算流原始数据
│   ├── trades/            # aggTrade数据
│   └── depth/             # 订单簿快照
├── processed/
│   ├── impacts/           # 检测到的冲击事件
│   ├── classifications/   # 分类结果
│   └── trades/            # 交易记录
└── backtest/
    └── events/            # 事件驱动回测数据
```

---

## 成本模型

### 显性成本

```
Binance USDT-M 合约：
  Maker费率：0.02%
  Taker费率：0.05%（VIP0）
  
本系统主要用Taker（市价单）：
  双边成本 = 0.10%
```

### 隐性成本

```
滑点（波动时）：0.02-0.05%
价格冲击（自身订单）：0.01-0.02%

真实总成本 ≈ 0.13-0.17%
```

### 盈亏平衡计算

```python
# 假设胜率50%，要达到盈亏平衡：
# 0.5 × avg_win - 0.5 × avg_loss - 0.15% = 0
# avg_win - avg_loss = 0.30%

# 如果止损固定为0.20%：
# avg_win需要 >= 0.50%
# 盈亏比需要 >= 2.5

# 如果胜率能到55%：
# 0.55 × avg_win - 0.45 × avg_loss - 0.15% = 0
# 盈亏比要求降低到约1.8
```

---

## 开发规范

### 目录结构

```
btc-quant-system/
├── CLAUDE.md              # 本文件
├── src/
│   ├── layers/
│   │   ├── environment.py     # 第1层
│   │   ├── classifier.py      # 第2层
│   │   └── executor.py        # 第3层
│   ├── data/
│   │   ├── websocket.py       # WebSocket连接
│   │   ├── rest_api.py        # REST API
│   │   └── storage.py         # 数据存储
│   ├── risk/
│   │   ├── position.py        # 仓位管理
│   │   └── circuit_breaker.py # 熔断规则
│   ├── backtest/
│   │   ├── engine.py          # 回测引擎
│   │   └── metrics.py         # 绩效指标
│   └── utils/
│       ├── config.py
│       └── logger.py
├── tests/
│   ├── test_layer1.py
│   ├── test_layer2.py
│   ├── test_layer3.py
│   └── test_integration.py
├── data/
├── reports/
└── .claude/
    └── commands/
```

### 代码规范

```python
# 1. 所有金额用Decimal，不用float
from decimal import Decimal
price = Decimal("85000.50")

# 2. 时间戳统一用毫秒
timestamp_ms = int(time.time() * 1000)

# 3. 所有API调用要有超时和重试
@retry(max_attempts=3, delay=1)
def api_call():
    response = requests.get(url, timeout=5)
    return response.json()

# 4. 关键决策要有日志
logger.info(f"Layer2 Classification: {result}", extra={
    "impact_direction": direction,
    "liq_count": liq_count,
    "confidence": confidence
})

# 5. 回测和实盘用同一套代码
class Executor:
    def __init__(self, mode="backtest"):  # or "live"
        self.mode = mode
        self.broker = BacktestBroker() if mode == "backtest" else LiveBroker()
```

---

## 验证流程

### 第1层验证（Week 1-2）

```bash
# 运行验证
python -m pytest tests/test_layer1.py -v

# 检查项：
# 1. 休眠有效性：休眠时段强行交易亏损率 > 55%
# 2. 方向偏向：与后续清算方向一致率 > 55%
# 3. 样本数：至少300个时间点
```

### 第2层验证（Week 3-4）

```bash
# 运行验证
python -m pytest tests/test_layer2.py -v

# 检查项：
# 1. 冲击检测召回率 > 80%
# 2. 有清算→趋势延续 > 60%
# 3. 无清算→价格回归 > 55%
# 4. 分类提升 vs 随机 > 10%
```

### 第3层验证（Week 5-6）

```bash
# 运行验证
python -m pytest tests/test_layer3.py -v

# 检查项：
# 1. 均值回归净期望 > 0
# 2. 趋势跟随净期望 > 0
# 3. 综合Sharpe > 0.8
# 4. 最大回撤 < 15%
```

### 集成验证（Week 7-8）

```bash
# Walk-Forward验证
python -m backtest.walk_forward --folds 4

# 检查项：
# 1. 所有fold净期望 > 0
# 2. 参数稳定性：±20%扰动后Sharpe变化 < 0.25
# 3. 极端行情存活：2020.3、2022.5、2024.8
```

---

## 禁止事项

```
❌ 绝对禁止：

1. 不要使用技术指标作为主要信号
   RSI、MACD、均线在这个系统中只能作为辅助
   核心信号是清算数据

2. 不要跳过验证步骤
   每一层必须独立验证通过才能继续
   不通过就停下来修复

3. 不要在回测中使用未来数据
   分类时只能用冲击发生后的数据
   不能用"事后涨跌"作为输入

4. 不要过度优化参数
   参数总数 ≤ 15个
   每个参数必须有逻辑来源

5. 不要忽视手续费和滑点
   所有回测必须扣除真实成本
   成本假设：0.15%双边

6. 不要在趋势中做均值回归
   第1层判断为趋势时，禁用均值回归路径

7. 不要改变已验证模块的接口
   通过验证的模块，接口冻结
   要改就重新验证

8. 不要人工干预自动交易
   系统上线后，人的作用是监控和维护
   不是手动"纠正"系统决策
```

---

## 开发顺序

```
┌──────────────────────────────────────────────────────────────┐
│  Week 1-2: 数据基础设施                                       │
│  ──────────────────────                                      │
│  • WebSocket连接（清算流、aggTrade、depth）                  │
│  • 数据存储和回放                                             │
│  • 基础计算（VWAP、ATR、CVD）                                │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Week 3-4: 第1层环境过滤                                      │
│  ──────────────────────                                      │
│  • 资金费率分析                                               │
│  • OI变化检测                                                │
│  • 市场状态分类                                               │
│  • 验证：休眠有效性                                          │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Week 5-6: 第2层冲击分类                                      │
│  ──────────────────────                                      │
│  • 冲击检测算法                                               │
│  • 清算数据分析                                               │
│  • 分类逻辑                                                   │
│  • 验证：分类准确性                                          │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Week 7-8: 第3层执行风控                                      │
│  ──────────────────────                                      │
│  • 入场逻辑                                                   │
│  • 止损止盈                                                   │
│  • 仓位管理                                                   │
│  • 熔断规则                                                   │
│  • 验证：净期望 > 0                                          │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Week 9-10: 集成 + Walk-Forward                               │
│  ───────────────────────────                                 │
│  • 三层集成                                                   │
│  • 回测引擎                                                   │
│  • Walk-Forward验证                                          │
│  • 参数稳定性测试                                            │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Week 11-12: 模拟盘                                           │
│  ────────────                                                │
│  • 全自动运行30天                                             │
│  • 监控和告警                                                 │
│  • 与回测对比                                                 │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Week 13+: 小资金实盘                                         │
│  ──────────────                                              │
│  • $500-1000起                                               │
│  • 2x杠杆上限                                                │
│  • 100笔后评估                                               │
└──────────────────────────────────────────────────────────────┘
```

---

## 监控和维护

### 每日检查

- [ ] 系统正常运行
- [ ] WebSocket连接稳定
- [ ] 当日交易数量在预期范围
- [ ] 无异常大额亏损

### 每周检查

- [ ] 胜率、盈亏比在预期范围
- [ ] 手续费占比 < 毛利的50%
- [ ] 各层信号分布正常
- [ ] 无策略漂移迹象

### 每月检查

- [ ] 净收益 > 0
- [ ] Sharpe > 0.8
- [ ] 回撤 < 15%
- [ ] 参数稳定性重验证

### 策略失效信号

如果出现以下情况，需要停止并重新评估：

1. 连续2周净亏损
2. 分类准确率下降 > 10%
3. 清算数据与价格行为的关系改变
4. 交易所规则变化

---

## 一句话总结

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                  │
│  当高杠杆散户被清算时，用清算数据判断方向，跟着清算方向交易。   │
│                                                                  │
│  没有清算 → 过度反应 → 均值回归                                 │
│  有清算   → 真突破   → 趋势跟随                                 │
│                                                                  │
│  每一步都可验证，通过再继续。                                   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```
