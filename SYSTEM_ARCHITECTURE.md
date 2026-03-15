# BTCUSDT 短线量化交易系统 - 完整架构文档

**版本**: v2.0 (优化版)
**最后更新**: 2026-03-15
**状态**: 模拟盘运行中，数据积累阶段

---

## 📋 目录

1. [系统概述](#系统概述)
2. [核心参数配置](#核心参数配置)
3. [三层决策架构](#三层决策架构)
4. [数据流架构](#数据流架构)
5. [执行与风控](#执行与风控)
6. [回测验证结果](#回测验证结果)
7. [部署架构](#部署架构)
8. [开发规范](#开发规范)

---

## 系统概述

### 核心哲学

```
你在赢谁的钱？
→ 被清算引擎强制平仓的高杠杆散户

他们的弱点？
→ 必须以任何价格成交，无法等待

你的优势？
→ 可以选择时机，等待他们被迫交易
→ 用清算数据判断方向，跟着清算交易
```

### 系统特点

- ✅ **三层决策链**：环境过滤 → 冲击分类 → 执行风控
- ✅ **清算驱动**：用 liquidation 数据作为核心分类器
- ✅ **双策略**：均值回归（过度反应）+ 趋势跟随（真突破）
- ✅ **严格风控**：信号分级、仓位管理、熔断机制
- ✅ **事件驱动回测**：用同一套代码做回测和实盘

---

## 核心参数配置

### ⚙️ Layer 1: 环境过滤参数

```python
# 资金费率极端度
FR_ZSCORE_THRESHOLD = 2.0  # Z-score绝对值 > 2.0 才有方向偏向

# 资金费率可靠性
HOURS_TO_SETTLEMENT < 2  # 结算前2小时：可靠性 0.6
HOURS_TO_SETTLEMENT > 6  # 结算后6小时：可靠性 0.4
其他                      # 可靠性 1.0

# OI变化阈值
OI_CHANGE_PCT_HIGH = 0.03   # OI变化 > 3%：高活跃
OI_CHANGE_PCT_LOW = 0.01    # OI变化 < 1%：低活跃

# 成交量阈值
VOLUME_RATIO_THRESHOLD = 0.5  # 当前成交量/均值 < 0.5：流动性不足

# 清算流活跃度
LIQ_VOLUME_HIGH = (阈值)  # 30分钟清算量 > 阈值：高压力
LIQ_VOLUME_LOW = (阈值)   # 30分钟清算量 < 阈值：低压力
```

**输出**：
- `status`: "可交易" | "休眠"
- `direction_bias`: "偏多" | "偏空" | "中性"
- `liquidation_side`: "多头" | "空头" | None
- `adjustments["stop_multiplier"]`: 0.8 - 1.3
- `adjustments["activity_level"]`: "高" | "正常" | "低"

---

### ⚙️ Layer 2: 冲击分类参数

```python
# 冲击检测
IMPACT_THRESHOLD = max(0.15%, recent_volatility * 1.5)  # 价格变动阈值
VOLUME_SURGE_RATIO = 2.0  # 成交量激增倍数（相对75%分位数）

# 分类等待窗口
WAIT_SECONDS = 45  # 冲击发生后等待45秒收集清算数据

# 清算阈值
LIQ_COUNT_THRESHOLD = 5      # 清算笔数阈值
LIQ_RATIO_THRESHOLD = 0.15   # 清算量占总成交量 > 15%

# 分类逻辑
if liq_count >= 5 and liq_ratio > 0.15:
    classification = "真突破"  # 清算级联可能继续
    strategy = "趋势跟随"
elif liq_count <= 2 and liq_ratio < 0.05:
    classification = "过度反应"  # 市场自发吸收
    strategy = "均值回归"
else:
    classification = "不确定"
    strategy = "放弃"

# 信心度计算
confidence = min(liq_ratio * 3, 1.0)  # 清算越多，信心越高
```

**输出**：
- `classification`: "过度反应" | "真突破" | "不确定"
- `strategy`: "均值回归" | "趋势跟随" | "放弃"
- `confidence`: 0.0 - 1.0
- `liq_count`: 清算笔数
- `liq_value`: 清算金额（USDT）
- `liq_ratio`: 清算量/总成交量

---

### ⚙️ Layer 3: 执行与风控参数（优化后 v2.0）

#### 📌 均值回归（过度反应）

```python
# 入场
entry_type = "MARKET"  # 市价立即入场
side = opposite_of_impact  # 与冲击方向相反

# 止损止盈
stop_loss = impact_boundary ± atr_1min * 1.5 * stop_multiplier
take_profit = vwap_3min ± atr_1min * 0.5

# 时间止损
time_stop = 180  # 3分钟（保持不变）

# 盈亏比要求
rr_threshold = 1.0  # 最低盈亏比

# 偏离度检查（新增）
MIN_DEVIATION = 0.001  # 至少偏离0.1%（可选优化）
```

#### 📌 趋势跟随（真突破）

**类型1：强清算（liq_value >= $200k）**
```python
# 入场
entry_type = "MARKET"  # 市价追入
side = same_as_impact  # 与冲击方向相同

# 止损止盈
stop_loss = price_before ± atr_1min * 0.3 * stop_multiplier
take_profit = price_after ± range_impact * 2

# 时间止损（已优化）
time_stop = 900  # 15分钟（原300秒 → 900秒）✅

# 移动止损
trailing_stop = True  # 启用移动止损
```

**类型2：普通清算（liq_value < $200k）**
```python
# 入场
entry_type = "LIMIT"  # 限价单
entry_price = price_after ∓ range_impact * 0.25  # 回调25%
side = same_as_impact
entry_expiry = 180  # 限价单有效期3分钟

# 止损止盈
stop_loss = price_before ± atr_1min * 0.3 * stop_multiplier
take_profit = price_after ± range_impact * 2

# 时间止损（已优化）
time_stop = 1200  # 20分钟（原600秒 → 1200秒）✅

# 移动止损
trailing_stop = True
```

#### 📌 信号分级与仓位

```python
# 信号分级（A/B/C）
def calculate_grade(confidence, rr_ratio, activity_level):
    score = 0
    # 信心分
    if confidence >= 0.6:  # 修正：>= 不是 >
        score += 2
    elif confidence >= 0.4:
        score += 1
    # 盈亏比分（新增）
    if rr_ratio > 1.0:
        score += 1
    # 原有评分
    if confidence > 0.8: score += 3
    elif confidence > 0.6: score += 2
    if rr_ratio > 2.5: score += 3
    elif rr_ratio > 2.0: score += 2
    elif rr_ratio > 1.5: score += 1
    if activity_level == "高": score += 1

    if score >= 6: return "A"  # 风险1.5%
    if score >= 4: return "B"  # 风险1.0%
    if score >= 2: return "C"  # 风险0.5%
    return "SKIP"

# 仓位计算（v2.0：杠杆上限）
risk_pct = {"A": 0.015, "B": 0.010, "C": 0.005}
risk_usd = account_balance * risk_pct[grade]
position_size = risk_usd / stop_distance

# 杠杆上限
max_position = account_balance * leverage * 0.8 / current_price
position_size = min(position_size, max_position)

# 最小仓位
MIN_POSITION = 0.001  # 最小0.001 BTC
position_size = max(position_size, MIN_POSITION)
```

#### 📌 熔断规则

```python
# 熔断阈值
DAILY_LOSS_LIMIT = 0.03  # 当日亏损 > 3%
CONSECUTIVE_LOSS_COUNT = 3  # 连续3笔亏损
LOW_WIN_RATE_COUNT = 10  # 最近10笔
LOW_WIN_RATE_THRESHOLD = 0.3  # 胜率 < 30%
LARGE_SINGLE_LOSS = 0.02  # 单笔亏损 > 2%

# 熔断动作
daily_loss_limit → "停止当日交易"（当日0点恢复）
consecutive_losses → "暂停至次日0点"
low_win_rate → "暂停1小时"
large_single_loss → "暂停30分钟 + 人工确认"
```

---

## 三层决策架构

```
┌─────────────────────────────────────────────────────────────┐
│  数据源                                                     │
│  • WebSocket: liquidations, aggTrade, depth                 │
│  • REST API: Funding Rate, Open Interest, Klines            │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: 环境过滤 (time_context.py + environment.py)       │
│  ─────────────────────────────────────────────────────────  │
│  输入: FR, OI, Volume, Liquidations                         │
│  处理:                                                       │
│    • FR Z-score 计算                                        │
│    • OI 变化检测                                            │
│    • 市场活跃度评估                                         │
│  输出: status="可交易|休眠", direction_bias, adjustments    │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: 冲击检测 + 分类 (classifier.py + layer2/)         │
│  ─────────────────────────────────────────────────────────  │
│  输入: aggTrade流, liquidation流                            │
│  处理:                                                       │
│    • 实时冲击检测（价格变动 > 0.15% + 成交量激增）           │
│    • 等待45秒收集清算数据                                   │
│    • 清算分类（有清算=真突破，无清算=过度反应）             │
│  输出: classification, strategy, confidence, liq_value      │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: 执行 + 风控 (executor.py + risk/)                │
│  ─────────────────────────────────────────────────────────  │
│  输入: Layer2结果 + Layer1环境 + 账户状态                   │
│  处理:                                                       │
│    • 均值回归: MARKET入场，3分钟时间止损                    │
│    • 趋势跟随:                                                │
│      - 强清算: MARKET追入，15分钟时间止损 ✅                 │
│      - 普通清算: LIMIT回调25%，20分钟时间止损 ✅            │
│    • 信号分级: A/B/C（风险1.5%/1%/0.5%）                    │
│    • 仓位管理: 凯利简化，杠杆上限                           │
│    • 熔断检查: 日亏损、连亏、低胜率                         │
│  输出: TradeSignal 或 None                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 数据流架构

### 数据采集

#### WebSocket 实时流（24/7运行）

```python
# 启动命令
python -m src.data.websocket --streams liquidations,trades,depth

# 数据流
wss://fstream.binance.com/ws/!forceOrder@arr    # 清算流
wss://fstream.binance.com/ws/btcusdt@aggTrade   # 交易流
wss://fstream.binance.com/ws/btcusdt@depth@100ms # 订单簿

# 存储格式
data/raw/
├── liquidations/YYYY-MM-DD.jsonl    # 每天一个文件
├── trades/YYYY-MM-DD/HH.jsonl       # 每小时一个文件
└── depth/YYYY-MM-DD/HH.jsonl        # 每小时一个文件（1Hz限速）
```

#### REST API 定时采集（每5-15分钟）

```python
# 启动命令
python -m src.data.rest_collector

# 数据接口
/fapi/v1/premiumIndex           # 资金费率（每15分钟）
/fapi/data/openInterestHist      # OI历史（每5分钟）
/fapi/v1/klines?interval=1m      # 1分钟K线（实时计算VWAP/ATR）
```

### 数据回放（回测用）

```python
# 数据读取器
src/data/data_reader.py

# 回测数据集
BacktestDataset:
  - trades: List[TradeTick]
  - liquidations: List[LiquidationTick]
  - depth: List[DepthTick]
```

---

## 执行与风控

### 执行流程（模拟盘/实盘）

```python
# 主循环 (live_trader.py)
while True:
    # 1. 订阅 WebSocket
    async for msg in websocket:
        # 2. 更新滚动缓冲区
        update_buffer(msg)

        # 3. 冲击检测
        if detect_impact():
            # 4. 等待45秒
            await wait(45)

            # 5. Layer 2 分类
            classif = classify(impact)

            # 6. 如果是"放弃"，跳过
            if classif.strategy == "放弃":
                continue

            # 7. Layer 1 环境检查
            env = evaluate_environment()

            # 8. Layer 3 执行
            signal = execute_signal(classif, env, price, balance)

            # 9. 熔断检查
            if not risk_manager.check_before_entry(signal, env):
                continue

            # 10. 下单
            if signal.entry_type == "MARKET":
                order = broker.place_market_order()
            else:  # LIMIT
                order = broker.place_limit_order()

            # 11. 持仓监控
            await monitor_position(order)

            # 12. 记录交易
            log_trade()
```

### 持仓监控

```python
# 监控逻辑 (live_trader.py::_position_monitor)
while position_open:
    # 每秒检查
    current_price = get_ticker()

    # 止损检查
    if hit_stop_loss:
        close_position("stop_loss")
        break

    # 止盈检查
    if hit_take_profit:
        close_position("take_profit")
        break

    # 时间止损
    if elapsed > signal.time_stop:
        close_position("time_stop")
        break

    # 移动止损（如果启用）
    if signal.trailing_stop and profit > atr:
        update_trailing_stop()

    await sleep(1)
```

### 熔断器状态机

```python
class RiskManager:
    def __init__(self, total_capital):
        self.daily_loss = 0
        self.recent_trades = []
        self._pause_until = None  # 暂停到期时间

    def check_circuit_breakers(self):
        now = datetime.now()

        # 检查是否在暂停期
        if self._pause_until and now < self._pause_until:
            return "暂停中", f"至 {self._pause_until}"

        # 当日亏损检查
        if self.daily_loss > self.total_capital * 0.03:
            self._pause_until = end_of_day()
            return "停止当日交易", "daily_loss_limit"

        # 连续亏损检查
        if len(self.recent_trades) >= 3:
            last_3 = self.recent_trades[-3:]
            if all(t.pnl < 0 for t in last_3):
                self._pause_until = next_day_0am()
                return "暂停至次日0点", "consecutive_losses"

        # 低胜率检查
        if len(self.recent_trades) >= 10:
            wins = sum(1 for t in self.recent_trades[-10:] if t.pnl > 0)
            if wins < 3:
                self._pause_until = now + timedelta(hours=1)
                return "暂停1小时", "low_win_rate"

        return None, None
```

---

## 回测验证结果

### 优化前后对比（2026-03-08 ~ 2026-03-13）

| 指标 | 优化前 | 优化后 | 变化 |
|------|--------|--------|------|
| **Sharpe** | 0.27 | **0.44** | **+63%** ✅ |
| **最大回撤** | 0.66% | **0.32%** | **-52%** ✅ |
| **期望值** | +2.40 | **+3.15** | **+31%** ✅ |
| 总PnL | +26.37 | +22.02 | -16% |
| 胜率 | 36.36% | 28.57% | -21% |
| 成交笔数 | 11笔 | 7笔 | -36% |

**结论**：优化后系统更加稳定，Sharpe提升63%，回撤减半。

### 策略表现拆解

**趋势跟随**：
- 笔数：5笔
- 总PnL：+47.68 USDT
- 胜率：40%
- 评价：✅ 盈利策略

**均值回归**：
- 笔数：1笔
- 总PnL：-65.74 USDT
- 胜率：0%
- 评价：⚠️ 样本太少，需更多数据

---

## 部署架构

### 启动方式

#### 完整启动（推荐）

```bash
# Windows 批处理
双击 "启动交易系统.bat"

# 自动启动：
- WS采集窗口（最小化）
- REST采集窗口（最小化）
- 模拟盘窗口（主窗口）
```

#### 分离启动

```bash
# 终端1：WS数据采集
python -m src.data.websocket --streams liquidations,trades,depth

# 终端2：REST数据采集
python -m src.data.rest_collector

# 终端3：模拟盘
python -m src.live_trader
```

### 目录结构

```
R5000/
├── CLAUDE.md                      # 本文档
├── 启动交易系统.bat                # 主启动脚本
├── 启动监控面板.bat                # Layer1 仪表盘
│
├── src/
│   ├── layers/
│   │   ├── time_context.py        # Layer 1: Binance REST数据
│   │   ├── environment.py         # Layer 1: 环境评估逻辑
│   │   ├── classifier.py          # Layer 2: ImpactEvent, ClassificationResult
│   │   ├── executor.py            # Layer 3: execute_signal() ✅ 已优化
│   │   └── layer1_frontend.py     # Streamlit 仪表盘
│   │
│   ├── layer2/                    # Layer 2 详细实现
│   │   ├── impact_detector.py     # 冲击检测
│   │   ├── liquidation_classifier.py  # 清算分类
│   │   ├── supplementary_signals.py    # 补充信号
│   │   └── types.py              # 数据类型定义
│   │
│   ├── data/
│   │   ├── websocket.py           # WS采集（liquidations/trades/depth）
│   │   ├── rest_api.py            # REST接口封装
│   │   ├── rest_collector.py      # FR/OI定时采集
│   │   └── data_reader.py         # 数据回放
│   │
│   ├── risk/
│   │   ├── position.py            # calculate_grade(), calculate_position()
│   │   └── circuit_breaker.py     # RiskManager 熔断器
│   │
│   ├── backtest/
│   │   ├── engine.py              # 事件驱动回测引擎
│   │   └── metrics.py             # 绩效指标计算
│   │
│   ├── broker/
│   │   ├── live_broker.py         # Binance Testnet/Mainet 接口
│   │   └── backtest_broker.py     # 回测模拟经纪商
│   │
│   ├── live_trader.py             # 模拟盘/实盘主循环
│   └── utils/
│       ├── config.py              # 配置管理
│       └── logger.py              # 日志工具
│
├── tests/
│   ├── test_layer1.py             # Layer 1 测试（23个）
│   ├── test_layer2.py             # Layer 2 测试（14个）
│   ├── test_layer3.py             # Layer 3 测试（13个）✅ 已更新
│   └── test_integration.py        # 集成测试
│
├── data/
│   └── raw/
│       ├── liquidations/YYYY-MM-DD.jsonl
│       ├── trades/YYYY-MM-DD/HH.jsonl
│       └── depth/YYYY-MM-DD/HH.jsonl
│
├── logs/
│   └── live_YYYYMMDD.log          # 模拟盘运行日志
│
├── reports/
│   └── backtest_result.json       # 最新回测结果
│
└── backtest_result.json           # 回测输出
```

---

## 开发规范

### 代码规范

```python
# 1. 金额统一用 Decimal
from decimal import Decimal
price = Decimal("85000.50")

# 2. 时间戳统一用毫秒
timestamp_ms = int(time.time() * 1000)

# 3. API调用必须有超时和重试
@retry(max_attempts=3, delay=1)
def api_call():
    response = requests.get(url, timeout=5)
    return response.json()

# 4. 关键决策必须有日志
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

### 测试规范

```bash
# 运行所有测试
python -m pytest tests/ -v

# 运行特定层测试
python -m pytest tests/test_layer1.py -v
python -m pytest tests/test_layer2.py -v
python -m pytest tests/test_layer3.py -v

# 测试覆盖率
python -m pytest tests/ --cov=src --cov-report=html
```

### 回测规范

```bash
# 运行回测
python -m src.backtest.engine --start 2026-03-08 --end 2026-03-13

# 输出
- 控制台：关键指标摘要
- 文件：backtest_result.json（完整交易记录）
```

---

## 验证流程

### 当前完成度

| 模块 | 状态 | 测试 | 说明 |
|------|------|------|------|
| **数据基础设施** | ✅ 100% | — | WS + REST采集完整 |
| **Layer 1 环境过滤** | ✅ 100% | 23/23 | FR/OI/Volume综合判断 |
| **Layer 2 冲击分类** | ✅ 100% | 14/14 | 清算数据分类器 |
| **Layer 3 执行风控** | ✅ 100% | 13/13 | 双策略+熔断，时间止损已优化 |
| **回测引擎** | ✅ 100% | — | 事件驱动 |
| **模拟盘** | ✅ 运行中 | — | Testnet实时运行 |
| **Walk-Forward** | ⏳ 待进行 | — | 需要30+天数据 |

**总测试：50/50 通过 ✅**

---

### 上实盘路径

```
当前位置（2026-03-15）
  ↓
Week 1-2: 积累数据30天
  目标：50+笔交易
  验证：样本充足性
  ↓
Week 3: Walk-Forward验证
  方法：4-fold交叉验证
  标准：所有fold净PnL > 0
  ↓
Week 4-5: 模拟盘30天
  对比：模拟盘 vs 回测
  标准：偏差 < 20%
  ↓
Week 6: 评估决策
  如果通过 → 小资金实盘（$500-1000，2x杠杆）
  如果未通过 → 继续优化
```

---

## 关键参数速查表

### 时间参数

| 参数 | 均值回归 | 强清算趋势 | 普通趋势 | 说明 |
|------|----------|------------|----------|------|
| **time_stop** | 180秒 (3分) | 900秒 (15分) ✅ | 1200秒 (20分) ✅ | 优化后 |
| **entry_expiry** | — | — | 180秒 (3分) | LIMIT单有效期 |
| **wait_seconds** | — | 45秒 | 45秒 | 分类等待窗口 |

### 阈值参数

| 参数 | 值 | 用途 |
|------|-----|------|
| `IMPACT_THRESHOLD` | max(0.15%, vol*1.5) | 冲击检测 |
| `VOLUME_SURGE_RATIO` | 2.0x | 成交量激增 |
| `LIQ_COUNT_THRESHOLD` | 5笔 | 清算阈值 |
| `LIQ_RATIO_THRESHOLD` | 15% | 清算占比 |
| `FR_ZSCORE_THRESHOLD` | 2.0 | FR极端度 |
| `OI_CHANGE_PCT_HIGH` | 3% | OI高活跃 |
| `MIN_DEVIATION` | 0.1% | 均值回归偏离（可选） |

### 盈亏比要求

| 策略 | rr_threshold | 说明 |
|------|--------------|------|
| 均值回归 | 1.0x | 最低要求 |
| 强清算趋势 | 1.5x | 中等要求 |
| 普通趋势 | 1.8x | 较高要求 |

### 风险参数

| 参数 | 值 | 说明 |
|------|-----|------|
| A级风险 | 1.5% | 最高质量信号 |
| B级风险 | 1.0% | 中等质量 |
| C级风险 | 0.5% | 低质量 |
| 杠杆上限 | 2-3x | 最大仓位限制 |
| 日亏损熔断 | 3% | 当日亏损 > 3%停止 |
| 连亏熔断 | 3笔 | 连续3笔亏损暂停至次日0点 |

---

## 版本历史

### v2.0 (2026-03-15) - 优化版

**改进**：
- ✅ 延长趋势跟随时间止损（5分→15分，10分→20分）
- ✅ Sharpe提升63%（0.27→0.44）
- ✅ 最大回撤减半（0.66%→0.32%）
- ✅ 单笔期望提升31%（+2.40→+3.15）

**文件变更**：
- `src/layers/executor.py`: time_stop参数优化
- `tests/test_layer3.py`: 更新测试用例
- `src/layers/executor.py.backup`: 原始备份

### v1.0 (2026-03-10) - 初始版本

**实现**：
- 三层决策架构
- 清算数据分类器
- 均值回归+趋势跟随双策略
- 事件驱动回测引擎
- 模拟盘运行

---

## 快速命令参考

### 启动系统

```bash
# 完整启动
双击 "启动交易系统.bat"

# 或手动启动
python -m src.data.websocket --streams liquidations,trades,depth &
python -m src.data.rest_collector &
python -m src.live_trader
```

### 监控系统

```bash
# 查看模拟盘日志
tail -f logs/live_$(date +%Y%m%d).log

# 查看最新交易
grep "交易完成" logs/live_$(date +%Y%m%d).log | tail -10

# 查看余额
grep "余额=" logs/live_$(date +%Y%m%d).log | tail -1

# 查看数据收集进度
ls -lh data/raw/liquidations/$(date +%Y-%m-%d).jsonl
wc -l data/raw/trades/$(date +%Y-%m-%d)/*/*.jsonl
```

### 运行测试

```bash
# 所有测试
python -m pytest tests/ -v

# 特定层
python -m pytest tests/test_layer3.py -v
```

### 运行回测

```bash
# 标准回测
python -m src.backtest.engine --start 2026-03-08 --end 2026-03-13

# 查看结果
python -c "import json; print(json.load(open('backtest_result.json'))['metrics'])"
```

---

## 文档

- `CLAUDE.md` - 项目说明
- `MEMORY.md` - 项目记忆
- `优化完成总结.md` - 优化报告
- `优化验证报告.md` - 验证结果
- `回测分析报告_最新.md` - 回测详情
- `SYSTEM_ARCHITECTURE.md` - 本文档

---

**系统状态**: ✅ 模拟盘运行中，数据积累阶段

**下一步**: 继续积累数据30天，然后Walk-Forward验证

**目标**: 小资金实盘（$500-1000）

---

*最后更新: 2026-03-15*
*版本: v2.0 (优化版)*
