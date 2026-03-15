# -*- coding: utf-8 -*-
"""Layer 3 测试套件"""

import pytest
from decimal import Decimal
from datetime import datetime
from unittest.mock import MagicMock

from src.layers.environment import EnvironmentResult
from src.layers.classifier import ImpactEvent, ClassificationResult
from src.layers.executor import execute_signal
from src.risk.position import calculate_grade, calculate_position
from src.risk.circuit_breaker import RiskManager


# ── Fixtures / 构造辅助 ────────────────────────────────────────────────────────

def _impact(direction: str = "down") -> ImpactEvent:
    now_ms = int(datetime.now().timestamp() * 1000)
    price_after = Decimal("59900") if direction == "down" else Decimal("60100")
    return ImpactEvent(
        detected_at_ms=now_ms,
        direction=direction,
        price_before=Decimal("60000"),
        price_after=price_after,
        price_change_pct=Decimal("0.00167"),
        volume_30s=Decimal("100"),
        volume_baseline=Decimal("40"),
        volume_surge_ratio=Decimal("2.5"),
    )


def _classification(
    strategy: str,
    direction: str = "down",
    confidence: Decimal = Decimal("0.7"),
) -> ClassificationResult:
    cls_map = {"均值回归": "过度反应", "趋势跟随": "真突破", "放弃": "不确定"}
    classification = cls_map.get(strategy, "不确定")
    liq_count = 1 if strategy == "均值回归" else 10 if strategy == "趋势跟随" else 3
    return ClassificationResult(
        impact=_impact(direction),
        classification=classification,
        strategy=strategy,
        confidence=confidence,
        liq_count=liq_count,
        liq_value=Decimal("10000"),
        liq_ratio=Decimal("0.1"),
        cvd_follows=True,
        wait_seconds=45,
    )


def _env(direction_bias: str = "中性", oi_change_pct: Decimal = Decimal("0")) -> EnvironmentResult:
    tc = MagicMock()  # TimeContext 用 mock，Layer3 不依赖其细节
    return EnvironmentResult(
        status="可交易",
        direction_bias=direction_bias,
        liquidation_side=None,
        suitability=Decimal("0.8"),
        time_context=tc,
        adjustments={"stop_multiplier": 1.0, "activity_level": "正常"},
        reason="test",
        fr_zscore=None,
        oi_change_pct=oi_change_pct,
        liq_volume=Decimal("0"),
        volume_ratio=Decimal("1"),
    )


# ── 均值回归信号测试 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("direction, expected_side, current_price", [
    ("down", "BUY",  Decimal("59920")),  # 下跌过度反应 -> 市价做多
    ("up",   "SELL", Decimal("60080")),  # 上涨过度反应 -> 市价做空
])
def test_mean_reversion_signal(direction, expected_side, current_price):
    """过度反应 → 生成方向正确的MARKET信号"""
    classif = _classification("均值回归", direction)
    signal = execute_signal(classif, _env(), current_price, Decimal("100000"), leverage=1)
    assert signal is not None
    assert signal.side == expected_side
    assert signal.entry_type == "MARKET"


# ── 趋势跟随信号测试 ───────────────────────────────────────────────────────────

def test_trend_follow_limit():
    """普通真突破（liq_value=$10k < $200k）→ LIMIT 12% 回调入场，20分钟时间止损"""
    classif = _classification("趋势跟随", "down", confidence=Decimal("0.8"))
    # _classification 默认 liq_value=10000，属于普通清算档
    signal = execute_signal(classif, _env(), Decimal("59900"), Decimal("100000"), leverage=1)
    assert signal is not None
    assert signal.entry_type == "LIMIT"
    assert signal.time_stop == 1200  # 优化后：600秒→1200秒（10分→20分）
    assert signal.entry_expiry == 180


def test_trend_follow_strong_liq_market():
    """强清算真突破（liq_value=$300k >= $200k）→ MARKET 追入 + 移动止损，15分钟时间止损"""
    classif = _classification("趋势跟随", "down", confidence=Decimal("0.8"))
    classif.liq_value = Decimal("300000")   # 覆盖为强清算金额
    signal = execute_signal(classif, _env(), Decimal("59900"), Decimal("100000"), leverage=1)
    assert signal is not None
    assert signal.entry_type == "MARKET"
    assert signal.trailing_stop is True
    assert signal.entry_expiry is None
    assert signal.time_stop == 900  # 优化后：300秒→900秒（5分→15分）


# ── 盈亏比不足 → SKIP ─────────────────────────────────────────────────────────

def test_skip_low_rr():
    """均值回归价格已大幅回归，剩余空间 < ATR，盈亏比 < 1.0 → None

    参数计算（下跌冲击，做多均值回归）：
    price_before=60000, price_after=59900, price_change_pct=0.00167
    atr_1min = 0.00167 * 59900 * 0.3 ≈ 30

    current_price=59975（已回归75%，剩余25点）：
    stop = 59975 - 30*1.5 = 59930，risk = 45
    tp = 60000 + 30*0.5 = 60015，reward = 40
    RR = 40/45 ≈ 0.89 < 1.0 → None
    """
    classif = _classification("均值回归", "down")
    signal = execute_signal(classif, _env(), Decimal("59975"), Decimal("100000"), leverage=1)
    assert signal is None


# ── 不确定分类 → None ─────────────────────────────────────────────────────────

def test_skip_uncertain():
    """不确定分类 → execute_signal 返回 None"""
    classif = _classification("放弃")
    signal = execute_signal(classif, _env(), Decimal("60000"), Decimal("100000"), leverage=1)
    assert signal is None


# ── 信号分级测试 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("confidence, rr_ratio, activity, expected_grade", [
    (Decimal("0.9"), Decimal("2.6"), "高", "A"),   # 3+3+1=7 → A
    (Decimal("0.5"), Decimal("1.6"), "低", "C"),   # 1+1+0=2 → C
])
def test_calculate_grade(confidence, rr_ratio, activity, expected_grade):
    assert calculate_grade(confidence, rr_ratio, activity) == expected_grade


# ── 仓位计算测试 ───────────────────────────────────────────────────────────────

def test_position_sizing():
    """A 级，止损200，余额100k，价格30k → 计算量=7.5 BTC（未触发杠杆上限）"""
    # max_qty = 100000*1*0.8/30000 = 2.66 BTC；qty = 1500/200 = 7.5 BTC > 2.66 → 触发上限，返回2.66
    qty = calculate_position(
        grade="A",
        stop_distance=Decimal("200"),
        account_balance=Decimal("100000"),
        current_price=Decimal("30000"),
        leverage=1,  # 测试默认1x杠杆
    )
    max_qty = Decimal("100000") * Decimal("0.8") / Decimal("30000")  # 2.66...
    # 实际：qty = max(0.001, min(7.5, 2.66)) = 2.66
    assert qty == max_qty


def test_position_leverage_cap():
    """止损极小时，仓位被杠杆上限截断"""
    # qty = 1500/20 = 75 BTC；max_qty = 100000*3*0.8/60000 = 4 BTC → 返回4
    qty = calculate_position(
        grade="A",
        stop_distance=Decimal("20"),
        account_balance=Decimal("100000"),
        current_price=Decimal("60000"),
        leverage=3,  # 测试3x杠杆上限
    )
    max_qty = Decimal("100000") * Decimal("3") * Decimal("0.8") / Decimal("60000")
    assert qty == max_qty  # 应该正好等于上限


# ── 熔断测试 ──────────────────────────────────────────────────────────────────

class MockTrade:
    def __init__(self, pnl: Decimal):
        self.pnl = pnl


def test_circuit_breaker_daily_loss():
    """当日亏损 4000/100000 = 4% > 3% → 停止当日交易"""
    rm = RiskManager(Decimal("100000"))
    rm.daily_loss = Decimal("4000")
    action, reason = rm.check_circuit_breakers()
    assert action == "停止当日交易"
    assert reason == "daily_loss_limit"


def test_circuit_breaker_consecutive_loss():
    """连续3笔亏损 → 暂停至次日0点（当日不再交易，避免在不利行情中持续下单）"""
    rm = RiskManager(Decimal("100000"))
    rm.recent_trades = [MockTrade(Decimal("-100")) for _ in range(3)]
    action, _ = rm.check_circuit_breakers()
    assert action == "暂停至次日0点"


def test_env_direction_downgrade():
    """偏空环境做多信号 → grade 从 A 降到 B"""
    rm = RiskManager(Decimal("100000"))
    env = _env(direction_bias="偏空")
    signal = {"side": "BUY", "grade": "A"}
    ok, _ = rm.check_before_entry(signal, env)
    assert ok is True
    assert signal["grade"] == "B"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
