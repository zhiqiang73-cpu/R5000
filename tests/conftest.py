"""
pytest配置和fixtures
"""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from decimal import Decimal

@pytest.fixture
def sample_config():
    """系统配置参数"""
    return {
        'LIQ_COUNT_THRESHOLD': 5,
        'LIQ_RATIO_THRESHOLD': 0.15,
        'IMPACT_WAIT_SECONDS': 45,
        'RR_RATIO_MEAN_REVERSION': Decimal('1.3'),
        'RR_RATIO_TREND_FOLLOW': Decimal('1.8'),
        'DEVIATION_THRESHOLD': Decimal('0.0004'),  # 0.04%
        'TRADING_COST': Decimal('0.0015'),  # 0.15% 总成本
        'MAKER_FEE': Decimal('0.0002'),  # 0.02%
        'TAKER_FEE': Decimal('0.0005'),  # 0.05%
        'SLIPPAGE': Decimal('0.0003'),  # 0.03%
        'MAX_LEVERAGE': 3,
        'RISK_PER_TRADE': {
            'A': Decimal('0.015'),
            'B': Decimal('0.010'),
            'C': Decimal('0.005')
        }
    }

@pytest.fixture
def sample_market_data():
    """样本市场数据"""
    now = datetime.now()
    timestamps = [int((now - timedelta(minutes=i)).timestamp() * 1000)
                  for i in range(60)]

    return {
        'timestamps': timestamps,
        'prices': [Decimal(str(85000 + np.random.randn() * 100)) for _ in range(60)],
        'funding_rates': [Decimal(str(0.0001 + np.random.randn() * 0.00005)) for _ in range(60)],
        'oi_changes': [Decimal(str(np.random.randn() * 0.01)) for _ in range(60)],
        'volumes': [Decimal(str(100 + np.random.rand() * 50)) for _ in range(60)]
    }

@pytest.fixture
def sample_impact_event():
    """样本冲击事件"""
    return {
        'impact_time': int(datetime.now().timestamp() * 1000),
        'impact_direction': 'up',
        'price_change_30s': Decimal('0.002'),  # 0.2%
        'volume_surge': Decimal('2.5'),
        'start_price': Decimal('85000.0'),
        'end_price': Decimal('85170.0'),
        'impact_high': Decimal('85200.0'),
        'impact_low': Decimal('84980.0')
    }

@pytest.fixture
def sample_liquidation_data():
    """样本清算数据"""
    return [
        {
            'timestamp': int(datetime.now().timestamp() * 1000),
            'side': 'BUY',
            'quantity': Decimal('10.5'),
            'price': Decimal('85100.0')
        },
        {
            'timestamp': int((datetime.now() + timedelta(seconds=10)).timestamp() * 1000),
            'side': 'BUY',
            'quantity': Decimal('8.2'),
            'price': Decimal('85050.0')
        },
        {
            'timestamp': int((datetime.now() + timedelta(seconds=20)).timestamp() * 1000),
            'side': 'SELL',
            'quantity': Decimal('15.0'),
            'price': Decimal('85150.0')
        }
    ]

@pytest.fixture
def sample_trade_data():
    """样本成交数据"""
    now = datetime.now()
    return [
        {
            'timestamp': int((now + timedelta(seconds=i)).timestamp() * 1000),
            'price': Decimal(str(85000 + np.random.randn() * 50)),
            'quantity': Decimal(str(np.random.rand() * 5)),
            'is_buyer_maker': bool(np.random.rand() > 0.5)
        }
        for i in range(100)
    ]

@pytest.fixture
def sample_env_data():
    """样本环境数据"""
    return {
        'status': 'tradable',
        'direction_bias': 'short',
        'liquidation_side': 'long',
        'stop_multiplier': Decimal('1.0'),
        'activity_level': 'high',
        'market_stress': 'high'
    }

@pytest.fixture
def sample_account_data():
    """样本账户数据"""
    return {
        'balance': Decimal('10000.0'),
        'positions': [],
        'recent_trades': [
            {'pnl': Decimal('50.0'), 'side': 'BUY'},
            {'pnl': Decimal('-30.0'), 'side': 'SELL'},
            {'pnl': Decimal('80.0'), 'side': 'BUY'}
        ],
        'daily_loss': Decimal('20.0')
    }

@pytest.fixture
def synthetic_price_series():
    """合成价格序列用于回测"""
    # 生成30天的价格数据，1分钟频率
    n_points = 30 * 24 * 60  # 30天 * 24小时 * 60分钟
    timestamps = []
    now = datetime.now()

    for i in range(n_points):
        timestamps.append(int((now - timedelta(minutes=n_points-i)).timestamp() * 1000))

    # 生成有趋势和波动的价格序列
    prices = []
    price = Decimal('85000.0')

    for i in range(n_points):
        # 添加趋势和随机波动
        trend = Decimal('0.0001') if i < n_points//2 else Decimal('-0.0001')
        noise = Decimal(str(np.random.randn() * 0.0005))
        price_change = trend + noise
        price = price * (Decimal('1') + price_change)
        prices.append(price)

    return {
        'timestamps': timestamps,
        'prices': prices,
        'volume': [Decimal(str(np.random.rand() * 100 + 50)) for _ in range(n_points)]
    }

@pytest.fixture
def extreme_market_periods():
    """极端行情时间段"""
    return {
        '2020-03': {
            'start': '2020-03-01',
            'end': '2020-03-31',
            'description': '新冠恐慌抛售'
        },
        '2022-05': {
            'start': '2022-05-01',
            'end': '2022-05-31',
            'description': 'LUNA崩盘'
        },
        '2024-08': {
            'start': '2024-08-01',
            'end': '2024-08-31',
            'description': '极端行情测试'
        }
    }

@pytest.fixture
def walk_forward_folds():
    """Walk-Forward数据分割"""
    data_length = 365 * 24 * 60  # 1年的分钟数据
    fold_length = data_length // 4

    return [
        {
            'train_start': 0,
            'train_end': fold_length * 3,
            'test_start': fold_length * 3,
            'test_end': data_length
        },
        {
            'train_start': fold_length,
            'train_end': data_length,
            'test_start': fold_length * 2,
            'test_end': fold_length * 3
        },
        {
            'train_start': fold_length * 2,
            'train_end': data_length,
            'test_start': fold_length,
            'test_end': fold_length * 2
        },
        {
            'train_start': fold_length * 3,
            'train_end': data_length,
            'test_start': 0,
            'test_end': fold_length
        }
    ]

def assert_decimal_equal(actual, expected, tolerance=Decimal('0.0001')):
    """断言Decimal值相等"""
    assert abs(actual - expected) <= tolerance, f"{actual} != {expected}"

def assert_percentage_greater(actual, threshold):
    """断言百分比大于阈值"""
    assert actual > threshold, f"{actual*100:.2f}% <= {threshold*100:.2f}%"

def assert_percentage_less(actual, threshold):
    """断言百分比小于阈值"""
    assert actual < threshold, f"{actual*100:.2f}% >= {threshold*100:.2f}%"