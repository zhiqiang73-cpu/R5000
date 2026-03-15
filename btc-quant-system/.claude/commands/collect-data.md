# 数据采集

## 任务
启动或管理数据采集服务。

## 数据源

### 实时WebSocket流

| 数据 | 地址 | 存储位置 |
|------|------|----------|
| 清算流 | `wss://fstream.binance.com/ws/!forceOrder@arr` | `data/raw/liquidations/` |
| 逐笔成交 | `wss://fstream.binance.com/ws/btcusdt@aggTrade` | `data/raw/trades/` |
| 深度数据 | `wss://fstream.binance.com/ws/btcusdt@depth@100ms` | `data/raw/depth/` |
| K线 | `wss://fstream.binance.com/ws/btcusdt@kline_1m` | `data/raw/klines/` |

### REST API（定时）

| 数据 | 接口 | 频率 |
|------|------|------|
| 资金费率 | `/fapi/v1/fundingRate` | 每15分钟 |
| 持仓量 | `/fapi/v1/openInterest` | 每5分钟 |
| 24h统计 | `/fapi/v1/ticker/24hr` | 每分钟 |

## 启动采集

### 全部数据
```bash
python -m data.collector start --all
```

### 指定数据
```bash
python -m data.collector start --streams liquidations,trades
```

### 后台运行
```bash
nohup python -m data.collector start --all > logs/collector.log 2>&1 &
```

## 数据格式

### 清算数据 (liquidations)
```json
{
    "timestamp": 1234567890123,
    "symbol": "BTCUSDT",
    "side": "SELL",
    "order_type": "LIMIT",
    "time_in_force": "IOC",
    "quantity": 0.05,
    "price": 85000.0,
    "avg_price": 84950.0,
    "status": "FILLED",
    "trade_time": 1234567890120
}
```

### 逐笔成交 (trades)
```json
{
    "timestamp": 1234567890123,
    "price": 85000.5,
    "quantity": 0.1,
    "is_buyer_maker": false,
    "trade_id": 123456789
}
```

### 深度快照 (depth)
```json
{
    "timestamp": 1234567890123,
    "bids": [[85000.0, 1.5], [84999.5, 2.0], ...],
    "asks": [[85000.5, 1.2], [85001.0, 0.8], ...]
}
```

## 数据存储

### 文件命名
```
data/raw/liquidations/2024-01-15.jsonl
data/raw/trades/2024-01-15/00.jsonl  # 按小时分文件
data/raw/depth/2024-01-15/00.jsonl
```

### 存储优化
- 使用JSONL格式（每行一条记录）
- 交易数据按小时分文件（量大）
- 深度数据只保留每秒一次快照（采样）
- 定期压缩历史数据

## 数据验证

### 检查数据完整性
```bash
python -m data.verify --date 2024-01-15
```

输出：
```
Liquidations: 1,234 records, 0 gaps
Trades: 2,345,678 records, 2 gaps (00:15-00:17, 03:22-03:25)
Depth: 86,400 snapshots, 100% coverage
```

### 修复数据缺口
```bash
# 从Binance历史API补充
python -m data.repair --date 2024-01-15 --stream trades
```

## 监控

### 检查采集状态
```bash
python -m data.collector status
```

输出：
```
Collector Status:
  Liquidations: ✅ Running (last: 2s ago)
  Trades: ✅ Running (last: 0.1s ago)
  Depth: ✅ Running (last: 0.1s ago)
  
  Today's records:
    Liquidations: 523
    Trades: 1,234,567
    Depth snapshots: 43,200
```

### 重连逻辑
```python
# 内置自动重连
# 断开后：1s, 2s, 4s, 8s, 16s, 30s (max) 间隔重试
# 重连后自动验证数据连续性
```

## 注意事项

1. **网络稳定性**
   - 建议使用VPS靠近交易所
   - 配置supervisor或systemd保持运行

2. **磁盘空间**
   - 交易数据约 500MB/天
   - 深度数据约 200MB/天（采样后）
   - 定期归档到云存储

3. **时钟同步**
   - 服务器时间必须与UTC同步
   - `ntpdate pool.ntp.org`
