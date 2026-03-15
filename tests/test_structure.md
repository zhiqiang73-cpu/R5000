# BTC量化交易系统测试结构

## 目录结构
```
tests/
├── conftest.py              # pytest配置和fixtures
├── __init__.py
├── test_layer1_environment.py
├── test_layer2_classifier.py
├── test_layer3_executor.py
├── test_integration.py
├── test_backtest.py
├── test_risk_manager.py
├── data/
│   ├── test_data/
│   │   ├── liquidations_sample.json
│   │   ├── trades_sample.json
│   │   └── oi_sample.json
│   └── fixtures/
│       ├── environment_fixtures.py
│       └── market_fixtures.py
└── utils/
    ├── data_generator.py
    └── validation_utils.py
```

## test_layer1_environment.py
```python
"""
第1层环境过滤测试
验证标准：
1. 休眠有效性：休眠时段强行交易亏损率 > 55%
2. 方向偏向：与后续清算方向一致率 > 55%
3. 样本数：至少300个时间点
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta

class TestEnvironmentFilter:

    def test_funding_rate_extreme_cases(self):
        """测试资金费率极端情况分类"""
        # 测试fr_zscore > +2.0 → 偏空
        # 测试fr_zscore < -2.0 → 偏多
        # 测试-2.0 < fr_zscore < 2.0 → 中性
        pass

    def test_volume_ratio_low(self):
        """测试成交量过低情况"""
        # volume_ratio < 0.5 时应返回"休眠"
        pass

    def test_market_stress_activity(self):
        """测试市场压力和活跃度组合"""
        # 高压力+高活跃度 → 高波动环境
        # 低压力+低活跃度 → 休眠
        # 其他 → 正常环境
        pass

    @pytest.mark.parametrize("hours_to_settlement", [1, 4, 7])
    def test_funding_settlement_timing(self, hours_to_settlement):
        """测试资金费率结算时间可靠性"""
        # hours_to_settlement < 2: reliability = 0.6
        # 2 <= hours <= 6: reliability = 1.0
        # hours > 6: reliability = 0.4
        pass

    def test_validation_dormancy_effectiveness(self):
        """验证休眠有效性"""
        # 加载历史数据
        # 找出所有休眠时段
        # 模拟在这些时段交易
        # 计算亏损率是否 > 55%
        # 样本数 >= 300
        pass

    def test_validation_direction_bias_accuracy(self):
        """验证方向偏向准确性"""
        # 加载历史数据
        # 记录每次方向偏向
        # 检查后续清算方向是否一致
        # 计算一致率是否 > 55%
        # 样本数 >= 300
        pass
```

## test_layer2_classifier.py
```python
"""
第2层冲击检测+分类测试
验证标准：
1. 冲击检测召回率 > 80%
2. "有清算"的冲击中，趋势延续占比 > 60%
3. "无清算"的冲击中，价格回归占比 > 55%
4. 分类提升 vs 随机 > 10%
"""

import pytest
import numpy as np

class TestImpactDetection:

    def test_dynamic_threshold(self):
        """测试动态冲击阈值"""
        # 基于近期波动率计算阈值
        # threshold = max(0.15%, recent_volatility * 1.5)
        pass

    def test_volume_surge_detection(self):
        """测试成交量激增检测"""
        # volume_30s > 2 * volume_baseline(75th percentile)
        pass

    def test_impact_conditions(self):
        """测试冲击条件"""
        # price_change_30s > impact_threshold AND volume_surge > 2.0
        pass

class TestImpactClassification:

    @pytest.mark.parametrize("liq_count,liq_ratio,expected", [
        (5, 0.20, "真突破"),        # >=5笔，ratio>0.15
        (2, 0.03, "过度反应"),      # <=2笔，ratio<0.05
        (3, 0.10, "不确定"),        # 中间状态
    ])
    def test_classification_logic(self, liq_count, liq_ratio, expected):
        """测试分类逻辑"""
        pass

    def test_wait_window(self):
        """测试观察窗口"""
        # 冲击发生后等待45秒
        # 只能使用冲击后的清算数据
        pass

    def test_supplementary_signals(self):
        """测试补充信号"""
        # CVD后续行为
        # 订单簿恢复
        # 成交速度衰减
        pass

    def test_validation_recall_rate(self):
        """验证召回率 > 80%"""
        # 加载历史数据
        # 识别所有真实冲击
        # 检测系统发现的冲击
        # 计算召回率
        pass

    def test_validation_liquidation_impact(self):
        """验证清算冲击关系"""
        # "有清算"冲击 → 趋势延续 > 60%
        # "无清算"冲击 → 价格回归 > 55%
        pass

    def test_validation_random_beating(self):
        """验证分类提升vs随机 > 10%"""
        # 计算分类准确率
        # 计算随机分类准确率
        # 计算提升幅度
        pass
```

## test_layer3_executor.py
```python
"""
第3层执行+风控测试
验证标准：
1. 均值回归策略：净期望值 > 0
2. 趋势跟随策略：净期望值 > 0
3. 综合胜率 > 42%，平均盈亏比 > 1.5
4. 熔断机制正确触发
5. 综合Sharpe > 0.8
6. 最大回撤 < 15%
"""

import pytest
from decimal import Decimal

class TestMeanReversionExecution:

    def test_mean_reversion_direction(self):
        """测试均值回归方向"""
        # up冲击 → SELL
        # down冲击 → BUY
        pass

    def test_rr_ratio_check(self):
        """测试盈亏比检查"""
        # rr_ratio < 1.3 → 放弃
        pass

    def test_deviation_check(self):
        """测试偏离度检查"""
        # deviation < 0.04% → 放弃
        pass

class TestTrendFollowExecution:

    def test_pullback_entry(self):
        """测试回调入场"""
        # 等25%回调
        # 使用限价单
        pass

    def test_rr_ratio_check(self):
        """测试趋势跟随盈亏比检查"""
        # rr_ratio < 1.8 → 放弃
        pass

    def test_liquidation_bonus(self):
        """测试清算强度加成"""
        # liq_bonus = min(liq_count/20, 0.3)
        pass

class TestSignalGrading:

    @pytest.mark.parametrize("confidence,rr_ratio,env_activity,expected", [
        (0.85, 2.6, "高", "A"),    # 3+3+1 = 7
        (0.70, 2.1, "正常", "B"),  # 2+2+0 = 4
        (0.50, 1.6, "低", "C"),    # 1+1+0 = 2
        (0.30, 1.2, "高", "SKIP"), # 0+0+1 = 1
    ])
    def test_calculate_grade(self, confidence, rr_ratio, env_activity, expected):
        """测试信号分级"""
        pass

    def test_position_calculation(self):
        """测试仓位计算"""
        # A级：风险1.5%
        # B级：风险1.0%
        # C级：风险0.5%
        # 杠杆上限：3x
        pass

class TestRiskManager:

    def test_circuit_breakers(self):
        """测试熔断规则"""
        # 当日亏损 > 3% → 停止当日
        # 最近10笔胜率 < 30% → 暂停1小时
        # 连续3笔止损 → 暂停30分钟
        # 单笔亏损 > 2% → 暂停30分钟+人工确认
        pass

    def test_validation_mean_reversion(self):
        """验证均值回归净期望 > 0"""
        # 扣手续费后净期望 > 0
        pass

    def test_validation_trend_follow(self):
        """验证趋势跟随净期望 > 0"""
        # 扣手续费后净期望 > 0
        pass

    def test_validation_performance_metrics(self):
        """验证绩效指标"""
        # 胜率 > 42%
        # 平均盈亏比 > 1.5
        # Sharpe > 0.8
        # 最大回撤 < 15%
        pass
```

## test_integration.py
```python
"""
集成测试
验证标准：
1. 所有fold净期望 > 0
2. 参数稳定性：±20%扰动后Sharpe变化 < 0.25
3. 极端行情存活：2020.3、2022.5、2024.8
"""

import pytest

class TestIntegration:

    def test_three_layer_integration(self):
        """测试三层集成"""
        # 第1层 → 第2层 → 第3层
        # 验证数据流正确
        pass

    def test_walk_forward_validation(self):
        """Walk-Forward验证"""
        # 4-fold交叉验证
        # 所有fold净期望 > 0
        pass

    def test_parameter_stability(self):
        """参数稳定性测试"""
        # ±20%参数扰动
        # Sharpe变化 < 0.25
        pass

    def test_extreme_market_survival(self):
        """极端行情存活测试"""
        # 2020.3（新冠）
        # 2022.5（LUNA崩盘）
        # 2024.8（极端测试）
        # 检查最大回撤和存活
        pass
```

## conftest.py
```python
"""
pytest配置和fixtures
"""

import pytest
import pandas as pd
from datetime import datetime, timedelta

@pytest.fixture
def sample_market_data():
    """提供样本市场数据"""
    return {
        'price': 85000.0,
        'funding_rate': 0.0002,
        'oi_change_pct': 0.02,
        'volume_ratio': 1.2,
        'liquidations_30min': 150000,
    }

@pytest.fixture
def sample_impact_data():
    """提供样本冲击数据"""
    return {
        'impact_time': int(datetime.now().timestamp() * 1000),
        'impact_direction': 'up',
        'price_change_30s': 0.002,  # 0.2%
        'volume_surge': 2.5,
    }

@pytest.fixture
def sample_liquidation_data():
    """提供样本清算数据"""
    return [
        {'side': 'BUY', 'quantity': 10.5, 'price': 85000.0},
        {'side': 'BUY', 'quantity': 8.2, 'price': 84950.0},
        {'side': 'SELL', 'quantity': 15.0, 'price': 85100.0},
    ]

@pytest.fixture
def sample_account_data():
    """提供样本账户数据"""
    return {
        'balance': 10000.0,
        'positions': [],
        'recent_trades': []
    }

@pytest.fixture
def config_params():
    """系统配置参数"""
    return {
        'LIQ_COUNT_THRESHOLD': 5,
        'LIQ_RATIO_THRESHOLD': 0.15,
        'IMPACT_WAIT_SECONDS': 45,
        'RR_RATIO_MEAN_REVERSION': 1.3,
        'RR_RATIO_TREND_FOLLOW': 1.8,
        'DEVIATION_THRESHOLD': 0.0004,  # 0.04%
        'TRADING_COST': 0.0015,  # 0.15%
    }
```

## backtest/engine.py提案
```python
"""
回测引擎设计
"""

import pandas as pd
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

class EventDrivenBacktestEngine:
    """
    事件驱动回测引擎

    特点：
    1. 模拟真实交易延迟（等待观察窗口）
    2. 精确手续费和滑点模型
    3. 事件时间戳精确到毫秒
    4. 无未来数据泄露
    """

    def __init__(self, data_path: str, config: Dict):
        self.data_path = data_path
        self.config = config
        self.current_time = None
        self.positions = []
        self.trades = []
        self.capital = Decimal("10000.0")

    def load_historical_data(self):
        """加载历史数据"""
        # 加载raw/liquidations/, raw/trades/, raw/depth/
        # 时间戳对齐
        # 按时间排序
        pass

    def simulate_layer1(self, timestamp: int):
        """模拟第1层环境过滤"""
        # 基于历史数据计算环境状态
        # 返回 status, direction_bias, liquidation_side
        pass

    def simulate_layer2(self, timestamp: int):
        """模拟第2层冲击检测"""
        # 检测冲击事件
        # 等待观察窗口（不能使用未来数据）
        # 分类结果
        pass

    def simulate_layer3(self, classification_result, env_data):
        """模拟第3层执行"""
        # 根据分类执行策略
        # 计算仓位和订单
        # 模拟成交（含滑点）
        # 计算实际盈亏
        pass

    def apply_trading_costs(self, trade):
        """应用交易成本"""
        # 手续费：0.10%（双边0.05%）
        # 滑点：0.02-0.05%
        # 价格冲击：0.01-0.02%
        # 总成本：0.13-0.17%
        pass

    def run_backtest(self, start_date: str, end_date: str):
        """运行回测"""
        # 按时间顺序处理事件
        # 严格执行等待窗口
        # 记录所有决策和交易
        # 生成绩效报告
        pass

    def calculate_metrics(self):
        """计算绩效指标"""
        metrics = {
            'total_trades': len(self.trades),
            'win_rate': self.calculate_win_rate(),
            'avg_profit_loss': self.calculate_avg_pnl(),
            'sharpe_ratio': self.calculate_sharpe(),
            'max_drawdown': self.calculate_max_drawdown(),
            'expectancy': self.calculate_expectancy(),
            'profit_factor': self.calculate_profit_factor(),
        }
        return metrics

class WalkForwardValidator:
    """
    Walk-Forward验证器
    """

    def __init__(self, folds: int = 4):
        self.folds = folds

    def split_data(self, data):
        """分割数据为训练集和测试集"""
        # 时间序列分割
        # 避免未来数据泄露
        pass

    def validate(self, engine_class, data):
        """运行Walk-Forward验证"""
        results = []
        for fold in range(self.folds):
            train_data, test_data = self.get_fold_data(data, fold)
            engine = engine_class(train_data)
            metrics = engine.run_backtest()
            results.append(metrics)
        return results

    def check_stability(self, results):
        """检查参数稳定性"""
        # ±20%参数扰动
        # Sharpe变化 < 0.25
        pass
```

## 风险点识别

### 禁止事项中的风险点

1. **第3条禁止事项** - 不要在回测中使用未来数据
   - 风险：分类时使用冲击发生后的数据容易造成未来数据泄露
   - 建议：回测引擎必须严格执行等待观察窗口，冲击后的45秒内不能使用清算数据

2. **第4条禁止事项** - 不要过度优化参数（参数总数 ≤ 15个）
   - 风险：实际系统中参数可能超过15个
   - 建议：参数文档化，建立参数变更记录

3. **第6条禁止事项** - 不要在趋势中做均值回归
   - 风险：第1层判断可能误判趋势状态
   - 建议：加强趋势检测算法的验证

4. **第8条禁止事项** - 不要人工干预自动交易
   - 风险：系统bug可能导致重大损失
   - 建议：增加熔断机制和安全检查

### 验证标准中的潜在问题

1. **第1层验证标准** - 休眠时段强行交易亏损率 > 55%
   - 问题：55%阈值是否足够？需要统计显著性检验
   - 建议：增加p-value检验（p < 0.05）

2. **第2层验证标准** - 分类提升 vs 随机 > 10%
   - 问题：随机基准需要明确定义
   - 建议：定义随机分类算法（50:50随机）

3. **第3层验证标准** - 综合Sharpe > 0.8
   - 问题：回测Sharpe可能高估实盘性能
   - 建议：计算保守Sharpe（考虑最大回撤）

### 测试数据挑战

1. **清算数据获取**：真实清算数据有限
   - 建议：使用合成数据+真实数据混合验证

2. **极端行情测试**：2020.3、2022.5、2024.8数据
   - 建议：收集这些时段的高频数据

3. **样本量要求**：每层测试需要≥300个样本
   - 建议：建立数据收集管道