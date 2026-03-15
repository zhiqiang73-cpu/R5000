# BTC-USDT 短线量化交易系统

## 核心理念

> **你赚的每一分钱，都是别人亏的。本系统的对手是被清算的高杠杆散户。**

## 系统架构

```
第1层：环境过滤 → 判断是否值得交易
第2层：冲击分类 → 用清算数据判断真突破/过度反应  
第3层：执行风控 → 精确入场，严格止损
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动数据采集
python -m data.collector start --all

# 3. 运行回测
python -m backtest.run --start 2024-01-01 --end 2024-06-30
```

## 目录结构

```
btc-quant-system/
├── CLAUDE.md              # Claude Code大脑文档
├── README.md              # 本文件
├── src/
│   ├── layers/            # 三层决策逻辑
│   ├── data/              # 数据采集和存储
│   ├── risk/              # 风控模块
│   ├── backtest/          # 回测引擎
│   └── utils/             # 工具函数
├── tests/                 # 测试代码
├── data/                  # 数据存储
├── reports/               # 验证报告
└── .claude/commands/      # Claude Code自定义命令
```

## Claude Code命令

| 命令 | 说明 |
|------|------|
| `/validate-layer1` | 验证第1层环境过滤 |
| `/validate-layer2` | 验证第2层冲击分类 |
| `/validate-layer3` | 验证第3层执行风控 |
| `/backtest` | 运行回测 |
| `/walk-forward` | Walk-Forward验证 |
| `/collect-data` | 管理数据采集 |
| `/check-params` | 检查参数数量 |
| `/analyze-liquidations` | 分析清算数据 |

## 开发流程

1. **Week 1-2**: 数据基础设施
2. **Week 3-4**: 第1层环境过滤 + 验证
3. **Week 5-6**: 第2层冲击分类 + 验证
4. **Week 7-8**: 第3层执行风控 + 验证
5. **Week 9-10**: 集成 + Walk-Forward
6. **Week 11-12**: 模拟盘
7. **Week 13+**: 小资金实盘

## 验证标准

- 第1层：休眠有效性 > 55%，方向一致率 > 55%
- 第2层：趋势延续率 > 60%，回归率 > 55%
- 第3层：净期望 > 0，Sharpe > 0.8，回撤 < 15%

## 风险提示

⚠️ 加密货币交易风险极高，可能导致全部本金损失。本系统仅供学习研究，不构成投资建议。
