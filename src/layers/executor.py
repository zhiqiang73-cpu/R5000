# -*- coding: utf-8 -*-
"""
BTCUSDT 量化交易系统 - 第3层: 执行 + 信号生成
输入：ClassificationResult (Layer2) + EnvironmentResult (Layer1) + 当前价格 + 余额
输出：TradeSignal 或 None
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from src.layers.environment import EnvironmentResult
from src.layers.classifier import ClassificationResult
from src.risk.position import calculate_grade, calculate_position, calculate_position_dynamic_leverage, downgrade_grade


@dataclass
class TradeSignal:
    side: str              # "BUY" | "SELL"
    entry_type: str        # "MARKET" | "LIMIT"
    entry_price: Decimal   # 入场价（LIMIT单有效）
    quantity: Decimal      # 仓位（BTC）
    stop_loss: Decimal     # 止损价
    take_profit: Decimal   # 止盈价
    grade: str             # "A" | "B" | "C"
    time_stop: int         # 时间止损（秒）
    trailing_stop: Optional[bool] = None  # 是否追踪止损
    entry_expiry: Optional[int] = None    # 限价单有效期（秒）


def _get_opposite(direction: str) -> str:
    return "down" if direction == "up" else "up"


def _get_activity_level(env: EnvironmentResult) -> str:
    oi_pct = abs(env.oi_change_pct)
    if oi_pct > Decimal("0.03"):
        return "高"
    elif oi_pct < Decimal("0.01"):
        return "低"
    return "正常"


def execute_signal(
    classification: ClassificationResult,
    environment: EnvironmentResult,
    current_price: Decimal,
    balance: Decimal,
    available_balance: Optional[Decimal] = None,
    leverage: int = 1,
) -> Optional[TradeSignal]:
    """
    根据前两层输出生成交易信号。

    过度反应 → 均值回归（市价反向）
    真突破   → 趋势跟随（回调限价）
    不确定   → None

    Args:
        classification: Layer2 分类结果
        environment: Layer1 环境评估
        current_price: 当前价格
        balance: 账户总余额
        available_balance: 账户可用余额（优先用于仓位计算）
        leverage: 杠杆倍数（默认1x）
    """
    # ── 前置检查 ─────────────────────────────────────────────────────
    if environment.status != "可交易":
        return None
    if classification.classification == "不确定":
        return None
    if classification.strategy == "放弃":
        return None

    # ── 从 Layer2 数据中取冲击信息 ───────────────────────────────────
    impact = classification.impact
    price_before = impact.price_before
    price_after = impact.price_after
    price_change_pct = impact.price_change_pct
    impact_dir = impact.direction
    confidence = classification.confidence

    # 强清算阈值：>= $200k 时市价追入，否则等回调
    _LIQ_VALUE_STRONG = Decimal("200000")

    stop_mult = Decimal(str(environment.adjustments.get("stop_multiplier", 1.0)))
    activity_level = _get_activity_level(environment)

    # ATR 1min 估算（TODO: 替换为真实ATR）
    atr_1min = price_change_pct * price_after * Decimal("0.3")

    # ── 交易方向 ─────────────────────────────────────────────────────
    is_mean_reversion = classification.classification == "过度反应"
    trade_dir = _get_opposite(impact_dir) if is_mean_reversion else impact_dir
    side = "BUY" if trade_dir == "up" else "SELL"

    # 方向与环境偏向冲突时，后续降级
    expected_bias = "偏多" if side == "BUY" else "偏空"
    has_conflict = (environment.direction_bias != "中性" and
                    environment.direction_bias != expected_bias)

    range_impact = abs(price_after - price_before)

    # ── 入场参数计算 ─────────────────────────────────────────────────
    if is_mean_reversion:
        entry_price = current_price
        entry_type = "MARKET"

        # 止损：基于 ATR（紧凑止损）
        # 不用冲击极值，而用 ATR*1.5 作为止损距离：
        # - 若反弹继续扩大（超过1.5×ATR），说明非过度反应而是真突破，及时止损
        # - 比极值止损更紧，使 RR 比可以达到合格水平
        if side == "SELL":
            stop_loss = current_price + atr_1min * Decimal("1.5") * stop_mult
        else:
            stop_loss = current_price - atr_1min * Decimal("1.5") * stop_mult

        # 止盈：略超越 price_before（ATR 延伸），因为均值回归常常会超调
        if side == "SELL":
            take_profit = price_before - atr_1min * Decimal("0.5")
        else:
            take_profit = price_before + atr_1min * Decimal("0.5")

        # 均值回归：高胜率策略，RR 要求低于趋势跟随
        # 若分类正确（无清算 = 市场自发吸收 → 价格必回归），胜率 > 55%，1.5x RR 以覆盖手续费
        rr_threshold = Decimal("1.5")
        time_stop = 180
        trailing_stop = False
        entry_expiry = None

        # 偏离度检查：价格偏离不够大则手续费会吃掉利润
        deviation = abs(current_price - price_before) / price_before
        if deviation < Decimal("0.0008"):
            return None

    elif classification.liq_value >= _LIQ_VALUE_STRONG:
        # ── 强清算（liq_value >= $200k）：市价追入 + 移动止损 ──────────────
        entry_type = "MARKET"
        entry_price = current_price

        if trade_dir == "up":
            stop_loss = price_before - atr_1min * Decimal("0.3") * stop_mult
            take_profit = price_after + range_impact * Decimal("2")
        else:
            stop_loss = price_before + atr_1min * Decimal("0.3") * stop_mult
            take_profit = price_after - range_impact * Decimal("2")

        rr_threshold = Decimal("1.5")
        time_stop = 900  # 强清算趋势：5分→15分
        trailing_stop = True
        entry_expiry = None

    else:
        # ── 普通清算（liq_value < $200k）：等回调 25% 限价入场 ────────────
        entry_type = "LIMIT"

        if trade_dir == "up":
            entry_price = price_after - range_impact * Decimal("0.25")
            stop_loss = price_before - atr_1min * Decimal("0.3") * stop_mult
            take_profit = price_after + range_impact * Decimal("2")
        else:
            entry_price = price_after + range_impact * Decimal("0.25")
            stop_loss = price_before + atr_1min * Decimal("0.3") * stop_mult
            take_profit = price_after - range_impact * Decimal("2")

        rr_threshold = Decimal("1.8")
        time_stop = 1200  # 普通趋势：10分→20分
        trailing_stop = True
        entry_expiry = 180

    # ── 止损方向合法性检查（防止因等待期价格漂移导致 SL 穿越 entry） ──
    if side == "BUY" and stop_loss >= entry_price:
        return None
    if side == "SELL" and stop_loss <= entry_price:
        return None

    # ── 盈亏比检查 ───────────────────────────────────────────────────
    risk_dist = abs(entry_price - stop_loss)
    if risk_dist <= 0:
        return None
    reward_dist = abs(take_profit - entry_price)
    rr_ratio = reward_dist / risk_dist
    if rr_ratio < rr_threshold:
        return None

    # ── 信号分级 + 方向冲突 → 直接放弃 ─────────────────────────────
    grade = calculate_grade(confidence, rr_ratio, activity_level)
    if has_conflict:
        return None
    if grade == "SKIP":
        return None

    # ── 仓位计算（动态杠杆）──────────────────────────────────────────────────
    capital_base = available_balance if available_balance is not None else balance
    if capital_base <= 0:
        return None
    
    # 根据环境决定是否使用动态杠杆
    use_dynamic_leverage = (
        classification.confidence >= Decimal("0.6")  # 信心度>=0.6才启用
        and environment.status == "可交易"  # 环境可交易
    )
    
    if use_dynamic_leverage:
        quantity = calculate_position_dynamic_leverage(
            grade, risk_dist, capital_base, entry_price, classification.confidence
        )
    else:
        quantity = calculate_position(grade, risk_dist, capital_base, entry_price, leverage)

    return TradeSignal(
        side=side,
        entry_type=entry_type,
        entry_price=entry_price,
        quantity=quantity,
        stop_loss=stop_loss,
        take_profit=take_profit,
        grade=grade,
        time_stop=time_stop,
        trailing_stop=trailing_stop,
        entry_expiry=entry_expiry,
    )
