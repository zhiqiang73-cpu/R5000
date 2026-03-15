# -*- coding: utf-8 -*-
"""第2层测试套件"""

import pytest
from unittest.mock import patch
from decimal import Decimal

from src.layers.classifier import (
    ImpactEvent, ClassificationResult,
    detect_impact, detect_liq_impact, classify_impact,
    _read_trades_since, _read_liqs_since, get_cvd_since,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_trades_up():
    """6笔上涨成交 (≥5 条, 价格变化 0.35% > 0.15% 阈值)"""
    return [
        {"T": 1000, "p": "67000", "q": "0.5", "m": True,  "s": "BTCUSDT"},
        {"T": 1020, "p": "67050", "q": "0.8", "m": False, "s": "BTCUSDT"},
        {"T": 1040, "p": "67100", "q": "1.0", "m": False, "s": "BTCUSDT"},
        {"T": 1060, "p": "67180", "q": "0.6", "m": True,  "s": "BTCUSDT"},
        {"T": 1080, "p": "67200", "q": "0.9", "m": False, "s": "BTCUSDT"},
        {"T": 1100, "p": "67234", "q": "0.8", "m": True,  "s": "BTCUSDT"},
    ]


@pytest.fixture
def mock_trades_low_vol():
    """成交量不足（< 5条）"""
    return [
        {"T": 1000, "p": "67000", "q": "0.1", "m": True,  "s": "BTCUSDT"},
        {"T": 1100, "p": "67010", "q": "0.1", "m": False, "s": "BTCUSDT"},
    ]


@pytest.fixture
def mock_liqs_true_breakout():
    """6笔 SELL 清算 → 真突破"""
    return [
        {"S": "SELL", "q": "0.5", "p": "67200"},
        {"S": "SELL", "q": "1.0", "p": "67210"},
        {"S": "SELL", "q": "0.8", "p": "67220"},
        {"S": "SELL", "q": "0.3", "p": "67225"},
        {"S": "SELL", "q": "0.4", "p": "67230"},
        {"S": "SELL", "q": "0.6", "p": "67235"},
    ]


@pytest.fixture
def mock_liqs_mean_reversion():
    """0笔清算 → 过度反应"""
    return []


@pytest.fixture
def mock_liqs_uncertain():
    """3笔清算（介于5和2之间，金额仅 ~$23k）→ 不确定"""
    return [
        {"S": "SELL", "q": "0.2", "p": "67200"},
        {"S": "SELL", "q": "0.1", "p": "67210"},
        {"S": "SELL", "q": "0.05", "p": "67220"},
    ]


@pytest.fixture
def mock_liqs_big_value_few_count():
    """3笔大单清算，总金额 ~$201k（count < 5 但金额 >= $200k）→ 金额优先真突破"""
    return [
        {"S": "SELL", "q": "1.0", "p": "67000"},   # $67,000
        {"S": "SELL", "q": "1.0", "p": "67100"},   # $67,100
        {"S": "SELL", "q": "1.0", "p": "67200"},   # $67,200
    ]  # 合计 ≈ $201,300


# ── detect_impact 测试 ────────────────────────────────────────────────────────

def test_detect_impact_price_change(mock_trades_up):
    """价格变动超阈值 + 成交量激增 → 触发冲击"""
    # 同一个 mock 返回给所有 _read_trades_since 调用（基线也是同数据，确保surge极大）
    with patch("src.layers.classifier._read_trades_since", return_value=mock_trades_up):
        impact = detect_impact()
        assert impact is not None
        assert impact.direction == "up"
        assert impact.price_change_pct > Decimal("0.0015")


def test_detect_liq_impact_triggers():
    """分钟级清算累积触发：单方向 $1.42M, dominance=100% → 触发 ImpactEvent"""
    # 模拟 15:39 UTC 场景：16笔全部空头爆仓（BUY 单），总额 $1.42M
    big_liqs = [
        {"S": "BUY", "q": "4.236", "ap": "70260.8"},   # ~$297,625
        {"S": "BUY", "q": "3.289", "ap": "70244.1"},   # ~$231,033
        {"S": "BUY", "q": "2.725", "ap": "70677.0"},   # ~$192,595
        {"S": "BUY", "q": "1.921", "ap": "69973.6"},   # ~$134,419
        {"S": "BUY", "q": "1.569", "ap": "70007.5"},   # ~$109,842
        {"S": "BUY", "q": "1.440", "ap": "70732.5"},   # ~$101,855
        {"S": "BUY", "q": "1.236", "ap": "70562.0"},   # ~$87,215
        {"S": "BUY", "q": "0.938", "ap": "69956.6"},   # ~$65,619
        {"S": "BUY", "q": "0.850", "ap": "70555.0"},   # ~$59,972
        {"S": "BUY", "q": "0.800", "ap": "69770.5"},   # ~$55,816
    ]  # 合计 ≈ $1,335,991 > $200k

    mock_trades = [
        {"T": 1000, "p": "70200", "q": "0.5", "m": True,  "s": "BTCUSDT"},
        {"T": 1020, "p": "70300", "q": "0.8", "m": False, "s": "BTCUSDT"},
        {"T": 1040, "p": "70400", "q": "1.0", "m": False, "s": "BTCUSDT"},
        {"T": 1060, "p": "70500", "q": "0.6", "m": True,  "s": "BTCUSDT"},
        {"T": 1080, "p": "70600", "q": "0.9", "m": False, "s": "BTCUSDT"},
    ]

    import src.layers.classifier as clf_mod
    clf_mod._last_liq_impact_ts = 0  # 重置冷却状态

    with patch("src.layers.classifier._read_liqs_since", return_value=big_liqs):
        with patch("src.layers.classifier._read_trades_since", return_value=mock_trades):
            impact = detect_liq_impact()

    assert impact is not None, "高清算金额应触发 ImpactEvent"
    assert impact.direction == "up"           # BUY 主导 → 空头被清 → 价格涨
    assert impact.price_before == Decimal("70200")
    assert impact.price_after  == Decimal("70600")


def test_detect_liq_impact_no_trigger_low_value():
    """清算金额不足 $200k → 不触发"""
    small_liqs = [
        {"S": "BUY", "q": "0.1", "ap": "70000"},  # $7,000
        {"S": "BUY", "q": "0.2", "ap": "70000"},  # $14,000
    ]
    mock_trades = [
        {"T": 1000, "p": "70000", "q": "0.5", "m": True,  "s": "BTCUSDT"},
        {"T": 1020, "p": "70100", "q": "0.5", "m": False, "s": "BTCUSDT"},
    ]
    import src.layers.classifier as clf_mod
    clf_mod._last_liq_impact_ts = 0

    with patch("src.layers.classifier._read_liqs_since", return_value=small_liqs):
        with patch("src.layers.classifier._read_trades_since", return_value=mock_trades):
            impact = detect_liq_impact()

    assert impact is None, "金额不足 $200k 不应触发"


def test_detect_liq_impact_no_trigger_mixed_direction():
    """多空混战（dominance < 70%）→ 不触发"""
    mixed_liqs = [
        {"S": "BUY",  "q": "3.0", "ap": "70000"},  # 空头爆 $210k
        {"S": "SELL", "q": "2.5", "ap": "70000"},  # 多头爆 $175k
    ]  # buy_val / total = 210/385 = 54.5% < 70%
    mock_trades = [
        {"T": 1000, "p": "70000", "q": "0.5", "m": True,  "s": "BTCUSDT"},
        {"T": 1020, "p": "70100", "q": "0.5", "m": False, "s": "BTCUSDT"},
    ]
    import src.layers.classifier as clf_mod
    clf_mod._last_liq_impact_ts = 0

    with patch("src.layers.classifier._read_liqs_since", return_value=mixed_liqs):
        with patch("src.layers.classifier._read_trades_since", return_value=mock_trades):
            impact = detect_liq_impact()

    assert impact is None, "方向不明不应触发"


def test_detect_impact_low_volume(mock_trades_low_vol):
    """成交量不足（< 5条）→ 不触发"""
    with patch("src.layers.classifier._read_trades_since", return_value=mock_trades_low_vol):
        impact = detect_impact()
        assert impact is None


# ── get_cvd_since 测试 ────────────────────────────────────────────────────────

def test_cvd_calculation(mock_trades_up):
    """CVD 方向验证: m=True→卖主动(-q), m=False→买主动(+q)"""
    # mock_trades_up: m=[T,F,F,T,F,T], q=[0.5, 0.8, 1.0, 0.6, 0.9, 0.8]
    # CVD = -0.5 + 0.8 + 1.0 - 0.6 + 0.9 - 0.8 = 0.8
    with patch("src.layers.classifier._read_trades_since", return_value=mock_trades_up):
        cvd = get_cvd_since(900)
        assert cvd == Decimal("0.8"), f"期望 CVD=0.8, 实际={cvd}"


def test_cvd_all_buyer_maker():
    """全部 m=True（卖主动）→ CVD 为负"""
    trades = [
        {"T": 1000, "p": "67000", "q": "1.0", "m": True, "s": "BTCUSDT"},
        {"T": 1010, "p": "67010", "q": "2.0", "m": True, "s": "BTCUSDT"},
    ]
    with patch("src.layers.classifier._read_trades_since", return_value=trades):
        cvd = get_cvd_since(900)
        assert cvd == Decimal("-3.0")


# ── classify_impact 测试 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_classify_true_breakout(mock_liqs_true_breakout, mock_trades_up):
    """6笔方向一致清算 → 真突破"""
    impact = ImpactEvent(
        1000, "down",
        Decimal("67000"), Decimal("67200"), Decimal("0.003"),
        Decimal("2.3"), Decimal("1.0"), Decimal("2.3"),
    )
    with patch("src.layers.classifier._read_liqs_since", return_value=mock_liqs_true_breakout):
        with patch("src.layers.classifier._read_trades_since", return_value=mock_trades_up):
            result = await classify_impact(impact, wait_seconds=0)

    assert result.classification == "真突破"
    assert result.strategy == "趋势跟随"
    assert result.liq_count == 6
    assert result.confidence > Decimal("0.4")


@pytest.mark.asyncio
async def test_classify_mean_reversion(mock_liqs_mean_reversion, mock_trades_up):
    """0笔清算 → 过度反应"""
    impact = ImpactEvent(
        1000, "down",
        Decimal("67000"), Decimal("67200"), Decimal("0.003"),
        Decimal("2.3"), Decimal("1.0"), Decimal("2.3"),
    )
    with patch("src.layers.classifier._read_liqs_since", return_value=mock_liqs_mean_reversion):
        with patch("src.layers.classifier._read_trades_since", return_value=mock_trades_up):
            result = await classify_impact(impact, wait_seconds=0)

    assert result.classification == "过度反应"
    assert result.strategy == "均值回归"
    assert result.liq_count == 0
    assert result.confidence == Decimal("0.6")


@pytest.mark.asyncio
async def test_classify_value_priority_breakout(mock_liqs_big_value_few_count, mock_trades_up):
    """liq_count=3 < 5，但 liq_value ~$201k >= $200k → 金额优先，判为真突破"""
    impact = ImpactEvent(
        1000, "down",
        Decimal("67000"), Decimal("67200"), Decimal("0.003"),
        Decimal("2.3"), Decimal("1.0"), Decimal("2.3"),
    )
    with patch("src.layers.classifier._read_liqs_since", return_value=mock_liqs_big_value_few_count):
        with patch("src.layers.classifier._read_trades_since", return_value=mock_trades_up):
            result = await classify_impact(impact, wait_seconds=0)

    assert result.classification == "真突破"
    assert result.strategy == "趋势跟随"
    assert result.liq_count == 3           # 笔数不足 5 但金额通过
    assert result.confidence >= Decimal("0.6")


@pytest.mark.asyncio
async def test_classify_uncertain(mock_liqs_uncertain, mock_trades_up):
    """3笔清算（> low_liq_count=2, < threshold=5）→ 不确定"""
    impact = ImpactEvent(
        1000, "down",
        Decimal("67000"), Decimal("67200"), Decimal("0.003"),
        Decimal("2.3"), Decimal("1.0"), Decimal("2.3"),
    )
    with patch("src.layers.classifier._read_liqs_since", return_value=mock_liqs_uncertain):
        with patch("src.layers.classifier._read_trades_since", return_value=mock_trades_up):
            result = await classify_impact(impact, wait_seconds=0)

    assert result.classification == "不确定"
    assert result.strategy == "放弃"
    assert result.liq_count == 3


# ── 读取函数辅助测试 ───────────────────────────────────────────────────────────

def test_read_fallback_returns_empty():
    """文件不存在时 _read_trades_since 返回空列表"""
    with patch("pathlib.Path.exists", return_value=False):
        trades = _read_trades_since(1000)
        assert trades == []


def test_decimal_precision():
    """Decimal 精度: CVD 结果类型正确"""
    trades = [
        {"q": "0.123456789", "p": "67000.123456789", "m": False, "T": 1000, "s": "BTCUSDT"},
    ]
    with patch("src.layers.classifier._read_trades_since", return_value=trades):
        cvd = get_cvd_since(1000)
        assert isinstance(cvd, Decimal)
        assert cvd == Decimal("0.123456789")


def test_cross_hour_read_no_crash():
    """跨小时读取不崩溃（文件不存在时正常返回空列表）"""
    with patch("pathlib.Path.exists", return_value=False):
        trades = _read_trades_since(1000)
        assert isinstance(trades, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
