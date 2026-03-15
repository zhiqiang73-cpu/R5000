# BTCUSDT 永续合约策略挖掘 Skill

> **用途**: 当用户要求从 BTCUSDT 永续合约的微观结构数据中发现、验证交易策略时，使用此 skill。
> **原始项目**: `d:\MyAI\My work team\R6000`（Node.js）
> **当前项目**: `d:\MyAI\My work team\R5000`（Python）
>
> **跨项目说明**:
> - **方法论（第一、三、五、七、八、九章）是语言无关的**，R5000 直接适用
> - **代码模板（第二、四章）是 R6000 的 Node.js 实现**，在 R5000 中需用 Python 等价实现
> - **已发现的策略（第六章）和筛选标准** 是通用的，策略逻辑可直接翻译为 Python
> - R5000 自身的数据采集（WebSocket aggTrade/depth/liquidation）产生的数据可以用同样的方法论分析
> - R5000 的 3 层决策架构（环境过滤→冲击检测→执行风控）和本 skill 的假设框架互补：
>   - 本 skill 负责**发现策略**（假设→检验→验证）
>   - R5000 的架构负责**部署策略**（信号→过滤→执行）

---

## 一、核心方法论：机制优先（Mechanism-First）

### 1.1 基本信念

市场中大部分交易由计算机程序执行。这些程序遵循确定的逻辑，因此**必然在历史数据中留下可检测的模式**。我们的方法不是统计挖掘（容易过拟合），而是：

```
因果假设 → 编码检测条件 → 回测验证 → 稳健性检验 → 多数据集交叉确认
```

### 1.2 核心原则

1. **机制优先**：每个策略必须有明确的因果解释（"为什么这个模式存在？谁的行为导致了它？"）
2. **不可避免性**：优先寻找市场参与者**被迫**产生的行为（如做市商必须管理库存、强平是强制的）
3. **扣费后盈利**：毛利不算数，必须扣除交易成本后仍然盈利（Maker费率约2.5bps来回）
4. **稳健性第一**：一个模式必须在不同时段、不同数据集上都表现一致才算有效
5. **分钟级操作**：秒级利润太小被手续费吃掉，小时级信号太少。分钟级是最佳粒度

---

## 二、数据管道（Data Pipeline）

### 2.1 项目结构

```
R6000/
├── data/
│   ├── binance-archive-6h/segments/    # 6小时历史数据（36 segments）
│   │   ├── 0000_20260314T214420Z/
│   │   │   ├── trades.json             # 成交记录
│   │   │   ├── book.json               # 订单簿快照
│   │   │   └── aux.json                # 辅助数据（OI, 资金费率, 强平）
│   │   └── ...
│   ├── binance-archive-24h/segments/   # 24小时历史数据（5 segments，持续增长）
│   └── btcusdt-capture/segments/       # 实时采集数据（5 segments）
├── src/
│   ├── data/
│   │   ├── align.js                    # 秒级数据对齐
│   │   └── minute-aggregator.js        # 分钟级聚合
│   ├── research/
│   │   ├── factors.js                  # 秒级因子计算
│   │   └── minute-factors.js           # 分钟级因子计算
│   ├── config/
│   │   └── defaults.js                 # 默认配置
│   └── utils/
│       ├── math.js                     # 数学工具（safeDivide, mean, stddev等）
│       └── time.js                     # 时间工具
├── outputs/                            # 输出结果
├── test-hypotheses.js                  # 第一轮假设检验（H1-H5）
├── test-extended-hypotheses.js         # 第二轮扩展假设（H6-H15）
├── validate-all-strategies.js          # 稳健性验证
└── RESEARCH_NOTE.md                    # 研究笔记
```

### 2.2 数据格式

每个 segment 目录包含3个 JSON 文件：

**trades.json** — 逐笔成交：
```json
[
  { "ts": 1710000000000, "price": 87500.5, "qty": 0.12, "side": "buy" }
]
```

**book.json** — 订单簿快照：
```json
[
  { "ts": 1710000000000, "bidPx": 87500.0, "bidSz": 2.5, "askPx": 87501.0, "askSz": 1.8 }
]
```

**aux.json** — 辅助市场数据：
```json
[
  {
    "ts": 1710000000000,
    "markPrice": 87500.3,
    "indexPrice": 87498.1,
    "openInterest": 15000,
    "fundingRate": 0.0001,
    "liquidationBuy": 0,
    "liquidationSell": 0.5
  }
]
```

### 2.3 数据加载流程

```
原始 trades/book/aux JSON
  → alignTables()          // 对齐到1秒桶，前填充缺失值
  → computeFactors()       // 计算秒级因子
  → aggregateToMinuteBars() // 聚合为1分钟K线
  → computeMinuteFactors()  // 计算分钟级因子
  → [准备好的 bars 数组]    // 可以用于假设检验
```

**完整加载代码模板**（直接复制使用）：

```javascript
import { readFileSync, readdirSync } from 'fs';
import { computeFactors } from './src/research/factors.js';
import { alignTables } from './src/data/align.js';
import { defaultConfig } from './src/config/defaults.js';
import { aggregateToMinuteBars } from './src/data/minute-aggregator.js';
import { computeMinuteFactors } from './src/research/minute-factors.js';

function loadDataset(segmentsDir) {
  let allSnapshots = [];
  try {
    const dirs = readdirSync(segmentsDir).filter(d => d.match(/^\d{4}_/)).sort();
    for (const d of dirs) {
      try {
        const p = `${segmentsDir}/${d}`;
        const aligned = alignTables({
          trades: JSON.parse(readFileSync(`${p}/trades.json`, 'utf-8')),
          book:   JSON.parse(readFileSync(`${p}/book.json`, 'utf-8')),
          aux:    JSON.parse(readFileSync(`${p}/aux.json`, 'utf-8'))
        }, defaultConfig);
        allSnapshots.push(...computeFactors(aligned, defaultConfig));
      } catch {}
    }
  } catch {}
  return allSnapshots;
}

// 自动扫描所有含有 segments 子目录的数据集
function discoverDatasets(baseDir = 'data') {
  const datasets = [];
  try {
    for (const name of readdirSync(baseDir)) {
      const segPath = `${baseDir}/${name}/segments`;
      try {
        const dirs = readdirSync(segPath).filter(d => d.match(/^\d{4}_/));
        if (dirs.length > 0) datasets.push({ name, path: segPath, segCount: dirs.length });
      } catch {}
    }
  } catch {}
  return datasets;
}

function loadAllAndPrepare() {
  const datasets = discoverDatasets();
  console.log(`发现 ${datasets.length} 个数据集:`);
  datasets.forEach(ds => console.log(`  ${ds.name}: ${ds.segCount} segments (${ds.path})`));
  
  const byDataset = {};
  let allSnapshots = [];
  for (const ds of datasets) {
    const snaps = loadDataset(ds.path);
    byDataset[ds.name] = snaps;
    allSnapshots.push(...snaps);
    console.log(`  → ${ds.name}: ${snaps.length} 秒级记录`);
  }
  
  allSnapshots.sort((a, b) => a.ts - b.ts);
  const dedup = [];
  let lastTs = 0;
  for (const s of allSnapshots) {
    if (s.ts !== lastTs) { dedup.push(s); lastTs = s.ts; }
  }
  const bars = computeMinuteFactors(aggregateToMinuteBars(dedup, 1));
  return { byDataset, allSnapshots: dedup, bars };
}
```

### 2.4 分钟级 Bar 可用字段

聚合 + 因子计算后，每个 bar 对象包含以下字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `ts` | number | 该分钟的起始时间戳(ms) |
| `open`, `high`, `low`, `close` | number | OHLC 价格 |
| `volume` | number | 该分钟总成交量(BTC) |
| `tradeCount` | number | 成交笔数 |
| `aggressiveBuyQty` | number | 主动买入量 |
| `aggressiveSellQty` | number | 主动卖出量 |
| `liquidationBuy` | number | 空头强平量（被迫买入） |
| `liquidationSell` | number | 多头强平量（被迫卖出） |
| `bidPx`, `bidSz` | number | 最优买价/量 |
| `askPx`, `askSz` | number | 最优卖价/量 |
| `spreadBps` | number | 买卖点差(bps) |
| `openInterest` | number | 持仓量 |
| `fundingRate` | number | 资金费率 |
| `markPrice`, `indexPrice` | number | 标记价/指数价 |
| `flowImbalance` | number | 订单流不平衡 [-1,1]，>0偏买 |
| `priceChangeBps` | number | 该分钟涨跌幅(bps) |
| `rangeBps` | number | 该分钟振幅(bps) |
| `volumeSpike` | number | 成交量相对过去60分钟均值的倍数 |
| `cumulativeFlowImbalance` | number | 过去5分钟累积流不平衡 |
| `flowConsistency` | number | 流方向一致性 [0,1] |
| `liquidationIntensity` | number | 过去10分钟累积强平量 |
| `oiChangeBps` | number | 过去15分钟OI变化(bps) |
| `priceMove5MinBps` | number | 过去5分钟价格变化(bps) |
| `priceMove10MinBps` | number | 过去10分钟价格变化(bps) |
| `avgRangeBps` | number | 过去5分钟平均振幅(bps) |
| `premiumBps` | number | 永续-指数溢价(bps) |

---

## 三、假设设计方法

### 3.1 如何发现新假设

**思路**：从"市场中谁被迫做什么"出发，而不是从"统计相关性"出发。

#### 问自己这些问题：

1. **谁在交易？** → 做市商、套利机器人、机构算法、散户、强平引擎
2. **他们被迫做什么？** → 做市商必须管理库存、强平是强制的、套利必须修复价差
3. **这种行为在数据中留下什么痕迹？** → 价格回归、量能模式、流方向变化
4. **这种痕迹可以用什么指标检测？** → 使用上面的 bar 字段组合
5. **检测到后应该做什么方向？** → 基于因果推理确定做多还是做空

#### 市场机制清单（用于灵感）

| 机制 | 参与者 | 被迫行为 | 数据痕迹 |
|------|--------|---------|---------|
| 库存修复 | 做市商 | 吸收冲击后必须恢复中性 | 急涨急跌后价格回归 |
| 分批执行 | 机构算法 | 大单拆成小单持续执行 | 流方向持续一致 |
| 吸筹/派发 | 大户 | 大量买入但不推动价格 | 放量+价格不动 |
| 动量耗竭 | 追涨者 | 后来者推力递减 | 价格涨+量递减 |
| 强制平仓 | 交易所引擎 | 保证金不足自动平仓 | 强平数据突增+价格冲击 |
| 套利修复 | 套利机器人 | 永续-现货价差必须收敛 | 溢价极端后回归 |
| 资金费率 | 全体持仓者 | 每8小时支付/收取资金费 | 费率极端前后流变化 |
| 连续超买 | 多方 | 短期极端后缺乏新买力 | 连续同向K线后反转 |
| 点差变化 | 做市商 | 不确定时扩大点差 | 点差突扩后收窄=信心恢复 |
| 大单吸收 | 有韧性的挂单 | 冲击被吸收=底部/顶部坚固 | 大单后价格快速恢复 |

### 3.2 假设的标准格式

每个假设必须包含4要素：

```javascript
{
  name: '描述性名称_参数_持有时间',
  mechanism: '因果机制的一句话解释',
  condition: (bars, i) => {
    // 返回 true/false：当前 bar 是否满足触发条件
    // 可以使用 bars[i] 的所有字段，也可以回看 bars[i-N]
  },
  direction: (bars, i) => {
    // 返回 1(做多), -1(做空), 0(不做)
    // 基于因果逻辑决定方向
  },
  holdBars: N  // 持有N根1分钟bar后平仓
}
```

### 3.3 参数搜索策略

对每个假设，系统性测试多组参数：

- **阈值参数**：通常测试3-4个档位（如 5, 8, 10, 15 bps）
- **持有时间**：通常测试 3, 5, 10 分钟
- **这意味着每个假设有 ~9-12 个变体**

**重要**：参数不是优化出来的，而是从因果逻辑推导出来的。比如"做市商修复库存需要几分钟"决定了持有时间范围。

---

## 四、检验执行

### 4.1 通用检验框架

```javascript
function testHypothesis(bars, name, mechanism, conditionFn, directionFn, holdBars) {
  const trades = [];
  
  // 从第60根bar开始（确保因子计算的lookback窗口足够）
  for (let i = 60; i < bars.length - holdBars; i++) {
    if (!conditionFn(bars, i)) continue;
    
    const direction = directionFn(bars, i);
    if (direction === 0) continue;
    
    const entryPrice = bars[i].close;
    if (!entryPrice) continue;
    
    const exitPrice = bars[i + holdBars]?.close;
    if (!exitPrice) continue;
    
    const pnlBps = ((exitPrice - entryPrice) / entryPrice) * 10000 * direction;
    
    // 追踪持有期内的最大有利/不利波动
    let maxFavorable = 0, maxAdverse = 0;
    for (let j = 1; j <= holdBars; j++) {
      const c = bars[i + j]?.close;
      if (!c) continue;
      const moveBps = ((c - entryPrice) / entryPrice) * 10000 * direction;
      maxFavorable = Math.max(maxFavorable, moveBps);
      maxAdverse = Math.min(maxAdverse, moveBps);
    }
    
    trades.push({
      ts: bars[i].ts,
      direction,
      pnlBps,
      maxFavorable,
      maxAdverse
    });
  }
  
  return { name, mechanism, holdBars, trades };
}
```

### 4.2 评估指标

```javascript
function evaluate(trades) {
  if (trades.length < 5) return null; // 样本太少，无意义
  
  const wins = trades.filter(t => t.pnlBps > 0);
  const losses = trades.filter(t => t.pnlBps <= 0);
  
  const winRate = wins.length / trades.length;
  const avgGrossPnl = trades.reduce((s, t) => s + t.pnlBps, 0) / trades.length;
  const avgWin = wins.length ? wins.reduce((s, t) => s + t.pnlBps, 0) / wins.length : 0;
  const avgLoss = losses.length ? Math.abs(losses.reduce((s, t) => s + t.pnlBps, 0) / losses.length) : 0;
  const profitFactor = avgLoss > 0 ? (avgWin * wins.length) / (avgLoss * losses.length) : 99;
  
  const MAKER_COST_BPS = 2.5; // 来回 Maker 手续费
  const avgNetPnl = avgGrossPnl - MAKER_COST_BPS;
  const totalNetPnl = trades.reduce((s, t) => s + t.pnlBps, 0) - trades.length * MAKER_COST_BPS;
  
  return {
    n: trades.length,
    winRate,
    avgGrossPnl,
    avgNetPnl,
    avgWin,
    avgLoss,
    profitFactor,
    totalNetPnl
  };
}
```

### 4.3 筛选标准

一个假设变体**初步有价值**的条件：

| 指标 | 最低要求 | 理想值 |
|------|---------|--------|
| 样本数 | >= 10 | >= 30 |
| 胜率 | > 55% | > 65% |
| 扣费后均净利 | > 0 bps | > 1.5 bps |
| 盈亏比(PF) | > 1.2 | > 2.0 |

---

## 五、稳健性验证（关键步骤！）

### 5.1 为什么必须做

回测中表现好**不代表**真的有效。常见陷阱：
- **过拟合**：参数刚好匹配了这段数据
- **幸存者偏差**：只看到了表现好的变体
- **市场状态偏差**：这段时间刚好是单边涨/跌

### 5.2 双重验证方法

每个通过初筛的模式必须通过**2层验证**：

#### 第1层：时间分割（前半 vs 后半）

```javascript
const mid = Math.floor(barsAll.length / 2);
const firstHalf = barsAll.slice(0, mid);
const secondHalf = barsAll.slice(mid);

// 分别测试，胜率都必须 > 50%
const resultH1 = evaluate(runOnBars(firstHalf, pattern));
const resultH2 = evaluate(runOnBars(secondHalf, pattern));
```

#### 第2层：数据集分割（跨数据集交叉验证）

```javascript
// 自动对所有发现的数据集进行交叉验证
const { byDataset } = loadAllAndPrepare();
const datasetNames = Object.keys(byDataset);

// 对每个数据集单独准备bars并测试
for (const dsName of datasetNames) {
  const dsBars = computeMinuteFactors(aggregateToMinuteBars(byDataset[dsName], 1));
  const result = evaluate(runOnBars(dsBars, pattern));
  console.log(`${dsName}: ${result.n}笔, 胜率${(result.winRate*100).toFixed(1)}%`);
}
// 要求：至少2个数据集各有>=3笔且胜率>50%
```

#### 通过标准

```
★ 稳健盈利 = 以下全部满足：
  1. 全量胜率 > 55%
  2. 全量扣费后净利 > 0
  3. 前半胜率 > 50% 且 后半胜率 > 50%（时间一致性）
  4. DS1胜率 > 50% 且 DS2胜率 > 50%（跨数据集一致性）
  5. 两个子集样本数都 >= 3

⚠ 有信号 = 时间一致但扣费后亏钱（可能参数需要调整）
✗ 不行 = 不一致，放弃
```

### 5.3 验证代码模板

```javascript
function robustnessCheck(pattern, barsAll, barsDS1, barsDS2) {
  const mid = Math.floor(barsAll.length / 2);
  
  const all   = evaluate(run(barsAll, pattern));
  const half1 = evaluate(run(barsAll.slice(0, mid), pattern));
  const half2 = evaluate(run(barsAll.slice(mid), pattern));
  const ds1   = evaluate(run(barsDS1, pattern));
  const ds2   = evaluate(run(barsDS2, pattern));
  
  const timeConsistent = half1?.n >= 3 && half2?.n >= 3 
                      && half1.winRate > 0.5 && half2.winRate > 0.5;
  const crossConsistent = ds1?.n >= 3 && ds2?.n >= 3 
                       && ds1.winRate > 0.5 && ds2.winRate > 0.5;
  
  let verdict = 'FAIL';
  if (timeConsistent && crossConsistent && all?.avgNetPnl > 0) verdict = 'ROBUST';
  else if (timeConsistent && all?.winRate > 0.55) verdict = 'SIGNAL';
  
  return { pattern: pattern.name, verdict, all, half1, half2, ds1, ds2 };
}
```

---

## 六、已发现的策略（截至 2026-03-15）

### 6.1 通过完整验证的 4 个策略

#### H1: 均值回归（最稳健）

```
触发: 1分钟K线涨跌幅 > 8bps
方向: 做反向（急涨做空、急跌做多）
持有: 10分钟
因果: 做市商吸收冲击后必须修复库存 → 价格回归
```

| 指标 | 值 |
|------|-----|
| 样本 | 38笔 |
| 胜率 | 68.4% |
| PF | 2.55 |
| 均净利 | +1.3 bps |
| 总净利 | +51 bps |
| 前半→后半 | 70.0% → 76.9% ✓ |
| DS1→DS2 | 71.4% → 70.0% ✓ |
| 多头侧 | **82.4%胜率** (急跌后做多) |
| 空头侧 | 57.1%胜率 (急涨后做空) |

**代码**:
```javascript
{
  name: 'H1_均值回归_8bps_10m',
  hold: 10,
  cond: (b, i) => Math.abs(b[i].priceChangeBps) > 8,
  dir:  (b, i) => b[i].priceChangeBps > 8 ? -1 : (b[i].priceChangeBps < -8 ? 1 : 0)
}
```

#### H10: 量能衰竭（最赚钱）

```
触发: 5分钟内价格移动>5bps 但成交量逐bar递减
方向: 做反向（涨且量衰做空、跌且量衰做多）
持有: 10分钟
因果: 推动力在递减=燃料耗尽 → 趋势反转
```

| 指标 | 值 |
|------|-----|
| 样本 | 38笔 |
| 胜率 | 65.8% |
| **PF** | **3.65** (所有策略最高) |
| **均净利** | **+2.6 bps** (所有策略最高) |
| **总净利** | **+99 bps** (所有策略最高) |
| 前半→后半 | 76.5% → 60.0% ✓ |
| DS1→DS2 | 73.7% → 61.5% ✓ |

**代码**:
```javascript
function getWindow(arr, end, len) {
  return arr.slice(Math.max(0, end - len + 1), end + 1);
}

{
  name: 'H10_量能衰竭_10m',
  hold: 10,
  cond: (b, i) => {
    if (i < 5) return false;
    const w = getWindow(b, i, 5);
    if (w.length < 5) return false;
    const volTrend = w[4].volume < w[2].volume && w[2].volume < w[0].volume;
    const priceMoved = Math.abs((b[i].close - w[0].close) / w[0].close * 10000) > 5;
    return volTrend && priceMoved;
  },
  dir: (b, i) => {
    const w = getWindow(b, i, 5);
    return b[i].close > w[0].close ? -1 : 1;
  }
}
```

#### H3: 量价背离（胜率最高，样本少）

```
触发: 成交量 > 2x均值 且 价格不动(< 3bps)
方向: 朝流方向做（流偏买做多、流偏卖做空）
持有: 10分钟
因果: 放量不动 = 有人在吸收对手盘 → 吸收完释放
```

| 指标 | 值 |
|------|-----|
| 样本 | 11笔 (偏少) |
| 胜率 | **72.7%** (最高) |
| PF | 2.95 |
| 均净利 | +2.0 bps |

**代码**:
```javascript
{
  name: 'H3_量价背离_vol2x_10m',
  hold: 10,
  cond: (b, i) => b[i].volumeSpike > 2 && Math.abs(b[i].priceChangeBps) < 3,
  dir:  (b, i) => b[i].flowImbalance > 0.2 ? 1 : (b[i].flowImbalance < -0.2 ? -1 : 0)
}
```

#### H2: 流持续（顺势策略）

```
触发: 5分钟累积流不平衡 > 0.5 且方向一致性 > 60%
方向: 顺流方向
持有: 10分钟
因果: 机构分批执行大单 → 订单流持续 → 动量延续
```

| 指标 | 值 |
|------|-----|
| 样本 | 18笔 |
| 胜率 | 72.2% |
| PF | 1.82 |
| 均净利 | +1.5 bps |
| 注意 | 后半胜率降至55.6%，稳定性弱于H1 |

**代码**:
```javascript
{
  name: 'H2_流持续_flow0.5_10m',
  hold: 10,
  cond: (b, i) => Math.abs(b[i].cumulativeFlowImbalance) > 0.5 && b[i].flowConsistency > 0.6,
  dir:  (b, i) => b[i].cumulativeFlowImbalance > 0 ? 1 : -1
}
```

### 6.2 第二梯队（有信号但未通过完整验证）

| 编号 | 假设 | 机制 | 胜率 | 问题 |
|------|------|------|------|------|
| H8 | 连续5bar反转 | 短期动量耗竭+挂单积累 | 68.8% | 扣费后不赚钱(-0.1bps) |
| H15 | 大单吸收 | 大单冲击后价格快速恢复 | 61.5% | 样本13笔，扣费后亏 |
| H14 | 强平反转 | 强制平仓=暂时冲击→修复 | 55.6% | 样本9笔，太少 |
| H12 | 基差回归 | 套利机器人修复永续-指数价差 | 51.7% | 胜率不够高(需>55%) |
| H5 | 极端流耗竭 | 一侧流动性被扫光→反弹 | ~50% | 无优势 |

### 6.3 已排除（机制真实但信号不够强）

| 编号 | 假设 | 为什么不行 |
|------|------|-----------|
| H6 | 盘口不平衡 | 1分钟内盘口变化太快，信号被噪声淹没 |
| H11 | 流翻转 | 翻转时机难精确捕捉 |
| H4 | OI确认 | OI更新频率太低(5秒一次) |
| H9 | 波动率压缩 | 11小时内压缩-爆发周期太少 |
| H7 | 点差收窄 | 数据中点差变化不够频繁 |
| H13 | 多框架共振 | 样本太少，与H1高度重叠 |

---

## 七、挖掘新策略的操作流程

当你（AI助手）需要挖掘新策略时，按以下步骤执行：

### Step 1: 加载数据

扫描 `data/` 下所有可用的 segments 目录，使用第二节的加载代码模板加载并准备 bars 数组。报告数据量（秒级条数、分钟bar数、时间跨度）。

### Step 2: 设计新假设

从第三节的"市场机制清单"中选择一个尚未测试的机制（或组合已有机制），设计新假设。每个假设必须写明：
1. **名称**: 简明描述
2. **因果机制**: 一句话解释为什么这个模式存在
3. **触发条件**: 用 bar 字段组合定义
4. **方向逻辑**: 基于因果推理决定做多/做空
5. **参数范围**: 阈值和持有时间的搜索范围

### Step 3: 批量检验

对每个假设的所有参数变体运行 `testHypothesis()`，收集结果。使用第四节的评估指标筛选。

### Step 4: 稳健性验证

对通过初筛（胜率>55% 且 净利>0）的变体，运行第五节的双重验证。只有标记为 `ROBUST` 的才算真正发现。

### Step 5: 记录结果

将新发现追加到 `RESEARCH_NOTE.md`，包含：
- 策略名称、因果机制
- 完整的验证数据表格
- 代码实现
- 与已有策略的对比

### Step 6: 更新策略库

将新的 `ROBUST` 策略代码添加到 `validate-all-strategies.js` 的 patterns 数组中，确保后续可以一键重新验证所有策略。

---

## 八、进一步探索方向

以下方向尚未完整探索，适合在更多数据积累后尝试：

### 8.1 组合策略
- H1 + H10 同时触发 → 更强信号？
- H1 多头 + 市场趋势过滤（只在上涨市做多均值回归）

### 8.2 时间模式
- 亚洲/欧洲/美国交易时段开盘效应
- 资金费率结算前后（每8小时）的行为变化
- 整点效应（每小时第0分钟）

### 8.3 波动率体制
- 高波动 vs 低波动环境下策略表现差异
- 波动率突变的预测

### 8.4 多品种
- 将同样的假设框架应用于 ETHUSDT
- 跨品种相关性信号

### 8.5 深层机制
- 做市商在订单簿上的"假单"检测（频繁报撤）
- 冰山订单检测（成交量远大于挂单可见量）
- 不同时间尺度的 OI 行为（5分钟 vs 1小时 OI 变化的含义不同）

### 8.6 止损优化
- 当前所有策略使用固定持有时间退出，没有止损
- 加入动态止损（如 -10bps 止损）可能改善风险调整后收益
- 加入获利了结（如 +15bps 止盈）可能锁定更多利润

---

## 九、关键注意事项

### 9.1 避免的陷阱

1. **不要做纯统计挖掘**：不要"扫描所有指标组合找相关性"。每个假设必须先有因果解释
2. **不要信未验证的结果**：初筛表现好 ≠ 真的有效。必须过稳健性验证
3. **不要在少样本上做结论**：<10笔交易的结果只作为"值得关注"，不作为"已验证"
4. **不要忘记扣费**：毛利 - 2.5bps(Maker来回) = 净利。很多看起来赚钱的策略扣费后亏钱
5. **不要过度优化参数**：如果一个假设只在极窄的参数下有效，说明它不稳健

### 9.2 数据注意

- 当前数据量约11小时，**非常有限**。所有结论都需要更多数据确认
- 数据可能偏向某种市场状态（如全部是上涨行情），需要等到覆盖不同行情后再做最终判断
- 新数据到来后应该**首先重新验证已有策略**，然后再挖掘新的

### 9.3 杠杆指南

对于通过验证的策略，使用 Kelly 公式计算最优杠杆：

```
Kelly = (胜率 × 盈亏比 - 败率) / 盈亏比
其中: 盈亏比 = 平均赢 / 平均亏

实际杠杆 = Kelly / 2  （半Kelly，降低波动）
```

| 策略 | Kelly | 推荐杠杆(半Kelly) |
|------|-------|------------------|
| H1 均值回归 | 41.6% | ~5x |
| H10 量能衰竭 | 47.7% | ~5x |
| H3 量价背离 | 48.1% | ~5x |

---

## 十、完整执行脚本参考

### 运行已有假设检验
```bash
node test-hypotheses.js           # H1-H5 第一轮
node test-extended-hypotheses.js  # H6-H15 第二轮
```

### 运行稳健性验证
```bash
node validate-all-strategies.js   # 全部策略对照验证
```

### 输出文件
```
outputs/hypothesis-test-results.json      # 第一轮详细结果
outputs/extended-hypothesis-results.json  # 第二轮详细结果
```

---

*最后更新: 2026-03-15 | 已验证策略: 4个 | 总测试假设: 15个(86个参数变体)*
