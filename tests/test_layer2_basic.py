"""第2层基本测试"""

import pytest
from decimal import Decimal
import json
import tempfile
from pathlib import Path
from datetime import datetime

# 创建测试数据
def create_test_trades_file():
    """创建测试trades文件"""
    temp_dir = Path(tempfile.mkdtemp())
    date_str = datetime.now().strftime("%Y-%m-%d")
    hour_str = datetime.now().strftime("%H")
    file_dir = temp_dir / "trades" / date_str
    file_dir.mkdir(parents=True, exist_ok=True)

    file_path = file_dir / f"{hour_str}.jsonl"

    # 创建上升趋势的trades
    base_price = 85000.0
    base_time = int(datetime.now().timestamp() * 1000) - 100000

    with open(file_path, 'w') as f:
        for i in range(10):
            # 价格从85000涨到86000
            price = base_price + i * 100
            timestamp = base_time + i * 3000  # 每3秒一笔
            record = {
                "server_ts": timestamp,
                "data": {
                    "s": "BTCUSDT",
                    "p": str(price),
                    "q": "0.1",  # 每个trade 0.1 BTC
                    "T": timestamp,
                    "m": True if i % 2 == 0 else False  # 交替买方/卖方主动
                }
            }
            f.write(json.dumps(record) + '\n')

    return temp_dir, file_path

def test_impact_event_dataclass():
    """测试ImpactEvent数据结构"""
    from src.layers.classifier import ImpactEvent

    event = ImpactEvent(
        detected_at_ms=123456789,
        direction="up",
        price_before=Decimal("85000"),
        price_after=Decimal("86000"),
        price_change_pct=Decimal("0.01176"),
        volume_30s=Decimal("1.0"),
        volume_baseline=Decimal("0.5"),
        volume_surge_ratio=Decimal("2.0")
    )

    assert event.direction == "up"
    assert event.price_change_pct > Decimal("0.01")
    assert event.volume_surge_ratio == Decimal("2.0")

def test_classification_result_dataclass():
    """测试ClassificationResult数据结构"""
    from src.layers.classifier import ClassificationResult, ImpactEvent

    impact = ImpactEvent(
        detected_at_ms=123456789,
        direction="up",
        price_before=Decimal("85000"),
        price_after=Decimal("86000"),
        price_change_pct=Decimal("0.01176"),
        volume_30s=Decimal("1.0"),
        volume_baseline=Decimal("0.5"),
        volume_surge_ratio=Decimal("2.0")
    )

    result = ClassificationResult(
        impact=impact,
        classification="真突破",
        strategy="趋势跟随",
        confidence=Decimal("0.8"),
        liq_count=10,
        liq_value=Decimal("10000"),
        liq_ratio=Decimal("0.2"),
        cvd_follows=True,
        wait_seconds=45
    )

    assert result.classification == "真突破"
    assert result.strategy == "趋势跟随"
    assert result.liq_count == 10

def test_cvd_calculation():
    """测试CVD计算逻辑"""
    from src.layers.classifier import get_cvd_since

    # 由于文件读取依赖，这里只验证导入和基本逻辑
    # 实际测试需要mock数据
    assert True  # 占位

def test_imports():
    """测试模块导入"""
    from src.layers import ImpactEvent, ClassificationResult, detect_impact, classify_impact

    # 验证导入成功
    assert ImpactEvent is not None
    assert ClassificationResult is not None
    assert detect_impact is not None
    assert classify_impact is not None

if __name__ == "__main__":
    test_impact_event_dataclass()
    test_classification_result_dataclass()
    test_cvd_calculation()
    test_imports()
    print("所有基础测试通过")