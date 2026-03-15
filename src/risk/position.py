# -*- coding: utf-8 -*-
"""仓位计算 + 信号分级"""

from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


def downgrade_grade(grade: str) -> str:
    """信号等级降一级"""
    mapping = {"A": "B", "B": "C", "C": "SKIP", "SKIP": "SKIP"}
    return mapping.get(grade, "SKIP")


def calculate_grade(
    confidence: Decimal,
    rr_ratio: Decimal,
    activity_level: str,
) -> str:
    """
    计算信号等级 A/B/C/SKIP

    评分规则：
    - confidence: >=0.8=3, >=0.6=2, >0.4=1
    - rr_ratio:   >2.5=3, >2.0=2, >1.5=2, >1.0=1
    - activity_level=='高': +1
    - score≥6→A, ≥4→B, ≥2→C, else SKIP

    注：confidence >= 0.6 给+2（原 >0.6 排除了恰好0.6的均值回归信号）
        rr > 1.0 给+1（与 conf>=0.6 的+2 合计达到 grade C 门槛）
    """
    score = 0
    if confidence >= Decimal("0.8"):
        score += 3
    elif confidence >= Decimal("0.6"):
        score += 2
    elif confidence > Decimal("0.4"):
        score += 1

    if rr_ratio > Decimal("2.5"):
        score += 3
    elif rr_ratio > Decimal("2.0"):
        score += 2
    elif rr_ratio > Decimal("1.5"):
        score += 1
    elif rr_ratio > Decimal("1.0"):
        score += 1

    if activity_level == "高":
        score += 1

    if score >= 6:
        return "A"
    elif score >= 4:
        return "B"
    elif score >= 2:
        return "C"
    return "SKIP"


def calculate_position(
    grade: str,
    stop_distance: Decimal,
    account_balance: Decimal,
    current_price: Decimal,
    leverage: int = 1,
) -> Decimal:
    """
    凯利简化版仓位计算。

    风险比例：A=1.5%, B=1.0%, C=0.5%
    杠杆上限：max_qty = balance×leverage×0.8 / current_price
    最小仓位：0.001 BTC

    Args:
        grade: 信号等级 (A/B/C)
        stop_distance: 止损距离（USDT）
        account_balance: 账户总余额（USDT）
        current_price: 当前价格（USDT）
        leverage: 杠杆倍数（默认1x）
    """
    risk_pct = {"A": Decimal("0.015"), "B": Decimal("0.010"), "C": Decimal("0.005")}
    if grade not in risk_pct:
        raise ValueError(f"无效等级: {grade}")
    if stop_distance <= 0:
        raise ValueError("止损距离必须大于0")

    risk_usd = account_balance * risk_pct[grade]
    qty = risk_usd / stop_distance

    # 给保证金预留20%安全垫，避免贴边触发 -2019
    margin_buffer = Decimal("0.8")
    max_qty = (account_balance * Decimal(str(leverage)) * margin_buffer) / current_price
    min_qty = Decimal("0.001")

    # 先限制杠杆上限，再保证最小值
    return max(min_qty, min(qty, max_qty))


def calculate_position_dynamic_leverage(
    grade: str,
    stop_distance: Decimal,
    account_balance: Decimal,
    current_price: Decimal,
    confidence: Decimal,
) -> Decimal:
    """
    动态杠杆仓位计算（根据信心度调整杠杆和风险）
    
    Args:
        grade: 信号等级 (A/B/C)
        stop_distance: 止损距离（USDT）
        account_balance: 账户总余额（USDT）
        current_price: 当前价格（USDT）
        confidence: 信心度 (0.0-1.0)
    
    Returns:
        仓位数量（BTC）
    """
    # 根据信心度动态调整杠杆和风险
    if confidence >= Decimal("0.8"):
        # 高信心：3x杠杆，2%风险
        leverage = 3
        risk_pct = Decimal("0.02")
    elif confidence >= Decimal("0.6"):
        # 中等信心：2x杠杆，1.5%风险
        leverage = 2
        risk_pct = Decimal("0.015")
    else:
        # 低信心：1x杠杆，1%风险
        leverage = 1
        risk_pct = Decimal("0.01")
    
    # 根据grade微调（高等级可以稍微激进）
    grade_bonus = {"A": Decimal("1.2"), "B": Decimal("1.0"), "C": Decimal("0.8")}
    if grade in grade_bonus:
        risk_pct = risk_pct * grade_bonus[grade]
    
    if stop_distance <= 0:
        raise ValueError("止损距离必须大于0")

    risk_usd = account_balance * risk_pct
    qty = risk_usd / stop_distance

    # 给保证金预留20%安全垫
    margin_buffer = Decimal("0.8")
    max_qty = (account_balance * Decimal(str(leverage)) * margin_buffer) / current_price
    min_qty = Decimal("0.001")

    logger.info(
        f"动态杠杆计算: confidence={float(confidence):.2f} leverage={leverage}x "
        f"risk_pct={float(risk_pct):.4f} grade={grade}"
    )

    return max(min_qty, min(qty, max_qty))

