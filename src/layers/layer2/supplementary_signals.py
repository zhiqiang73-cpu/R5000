"""
Layer2 补充信号

用于增强分类信心度（辅助判断，不是核心）

信号包括：
- CVD ( Cumulated Volume Delta ) 是否跟随冲击方向
- 订单簿是否恢复
- 成交速度是否衰减
"""

from decimal import Decimal

# 信心度加成上限
MAX_BONUS = Decimal("0.2")

# 各信号权重
WEIGHT_CVD = Decimal("0.05")
WEIGHT_DEPTH = Decimal("0.08")
WEIGHT_SPEED = Decimal("0.07")


def get_supplementary_bonus(
    cvd_follows: bool = None,
    depth_recovered: bool = None,
    speed_decayed: bool = None
) -> Decimal:
    """
    计算补充信号带来的信心度加成

    Args:
        cvd_follows: CVD是否跟随冲击方向（True增加信心）
        depth_recovered: 订单簿是否恢复（>70%恢复增加信心）
        speed_decayed: 成交速度是否衰减（衰减增加信心）

    Returns:
        Decimal - 信心度加成，范围 0 - 0.2
    """
    bonus = Decimal("0")

    if cvd_follows is True:
        bonus += WEIGHT_CVD
    elif cvd_follows is False:
        bonus -= WEIGHT_CVD * Decimal("0.5")

    if depth_recovered is True:
        bonus += WEIGHT_DEPTH
    elif depth_recovered is False:
        bonus -= WEIGHT_DEPTH * Decimal("0.5")

    if speed_decayed is True:
        bonus += WEIGHT_SPEED
    elif speed_decayed is False:
        bonus -= WEIGHT_SPEED * Decimal("0.5")

    # 限制范围
    bonus = max(Decimal("0"), min(bonus, MAX_BONUS))

    return bonus


def get_supplementary_signals(
    impact_direction: str,
    impact_time: int,
    symbol: str = "BTCUSDT"
) -> dict:
    """
    获取补充信号（简化实现）

    由于需要实时数据流，这里返回模拟值
    实际使用时应该连接实时数据源

    Args:
        impact_direction: 冲击方向 "up" 或 "down"
        impact_time: 冲击发生时间（毫秒）
        symbol: 交易对

    Returns:
        dict: 包含各信号状态的字典
    """
    # TODO: 实现真实的信号计算
    # 需要连接实时数据流或读取历史数据

    return {
        "cvd_follows": None,  # 无法确定
        "depth_recovered": None,
        "speed_decayed": None,
        "bonus": Decimal("0")
    }


def evaluate_supplementary_signals(
    cvd_delta: Decimal,
    depth_before: Decimal,
    depth_after: Decimal,
    speed_during: Decimal,
    speed_after: Decimal,
    impact_direction: str
) -> dict:
    """
    评估补充信号（基于具体数值）

    Args:
        cvd_delta: CVD变化量（正=买方主导）
        depth_before: 冲击前订单簿深度
        depth_after: 冲击后订单簿深度
        speed_during: 冲击期间成交速度
        speed_after: 冲击后成交速度
        impact_direction: 冲击方向 "up" 或 "down"

    Returns:
        dict: 信号状态和加成
    """
    # CVD是否跟随
    cvd_follows = None
    if impact_direction == "up" and cvd_delta > 0:
        cvd_follows = True
    elif impact_direction == "down" and cvd_delta < 0:
        cvd_follows = True
    elif cvd_delta != 0:
        cvd_follows = False

    # 订单簿恢复（冲击后/冲击前 > 70%）
    depth_recovered = None
    if depth_before > 0:
        recovery_ratio = depth_after / depth_before
        depth_recovered = recovery_ratio > Decimal("0.7")

    # 成交速度衰减（冲击后/冲击期间 < 50%）
    speed_decayed = None
    if speed_during > 0:
        speed_ratio = speed_after / speed_during
        speed_decayed = speed_ratio < Decimal("0.5")

    bonus = get_supplementary_bonus(
        cvd_follows=cvd_follows,
        depth_recovered=depth_recovered,
        speed_decayed=speed_decayed
    )

    return {
        "cvd_follows": cvd_follows,
        "depth_recovered": depth_recovered,
        "speed_decayed": speed_decayed,
        "bonus": bonus
    }