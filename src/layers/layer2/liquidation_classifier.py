"""
Layer2 清算分类器

根据清算数据判断冲击是"过度反应"还是"真突破"

核心逻辑：
- 冲击发生后等待45秒，观察清算数据
- 下跌冲击 + 大量SELL清算 = 真突破（趋势延续）
- 上涨冲击 + 大量BUY清算 = 真突破
- 清算很少 = 过度反应（均值回归）
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import List

from src.data.data_reader import get_liquidations_since, get_volume_since

from .types import ClassificationResult
from .supplementary_signals import get_supplementary_bonus

logger = logging.getLogger(__name__)

# 配置常量（来自CLAUDE.md设计）
# 清算数量阈值
LIQ_COUNT_THRESHOLD = 5  # 至少5笔清算
LIQ_COUNT_LOW = 2  # 少量清算
# 清算量占比阈值
LIQ_RATIO_THRESHOLD = Decimal("0.15")  # 15%以上
LIQ_RATIO_LOW = Decimal("0.05")  # 5%以下
# 观察等待时间（秒）
OBSERVE_WAIT_SECONDS = 45


@dataclass
class LiquidationData:
    """清算数据汇总"""
    count: int
    volume: Decimal
    value: Decimal
    ratio: Decimal


def analyze_liquidations(
    direction: str,
    impact_time: int,
    symbol: str = "BTCUSDT"
) -> LiquidationData:
    """
    分析冲击后的清算数据

    Args:
        direction: 冲击方向 "up" 或 "down"
        impact_time: 冲击发生时间（毫秒）
        symbol: 交易对

    Returns:
        LiquidationData
    """
    # 获取冲击后的清算数据
    liquidations = get_liquidations_since(impact_time, symbol)

    # 过滤相关方向的清算
    if direction == "down":
        # 下跌冲击：关注多头被清算（SELL）
        relevant_liqs = [l for l in liquidations if l.side == "SELL"]
    else:
        # 上涨冲击：关注空头被清算（BUY）
        relevant_liqs = [l for l in liquidations if l.side == "BUY"]

    # 计算总量
    total_volume = sum(l.quantity for l in relevant_liqs)
    total_value = sum(l.price * l.quantity for l in relevant_liqs)

    # 获取同期总成交量
    market_volume = get_volume_since(impact_time, symbol)

    # 计算清算占比
    if market_volume > 0:
        ratio = total_volume / market_volume
    else:
        ratio = Decimal("0")

    return LiquidationData(
        count=len(relevant_liqs),
        volume=total_volume,
        value=total_value,
        ratio=ratio
    )


async def classify_impact(
    direction: str,
    impact_time: int,
    symbol: str = "BTCUSDT",
    wait_seconds: int = OBSERVE_WAIT_SECONDS,
    use_supplementary: bool = True
) -> ClassificationResult:
    """
    冲击分类（异步）

    冲击发生后，等待一段时间，收集清算数据，判断是过度反应还是真突破

    Args:
        direction: 冲击方向 "up" 或 "down"
        impact_time: 冲击发生时间（毫秒）
        symbol: 交易对
        wait_seconds: 等待观察的秒数，默认45秒
        use_supplementary: 是否使用补充信号

    Returns:
        ClassificationResult
    """
    logger.info(f"开始分类: direction={direction}, impact_time={impact_time}")

    # 等待观察窗口
    if wait_seconds > 0:
        logger.info(f"等待 {wait_seconds} 秒观察清算数据...")
        await asyncio.sleep(wait_seconds)

    # 收集清算数据
    liq_data = analyze_liquidations(direction, impact_time, symbol)

    logger.info(
        f"清算数据: count={liq_data.count}, volume={liq_data.volume}, "
        f"ratio={liq_data.ratio:.4f}"
    )

    # 分类逻辑
    if liq_data.count >= LIQ_COUNT_THRESHOLD and liq_data.ratio > LIQ_RATIO_THRESHOLD:
        # 大量清算 → 真突破
        category = "真突破"
        strategy = "趋势跟随"
        trade_direction = "BUY" if direction == "up" else "SELL"

        # 信心度：清算越多，信心越高，最高1.0
        confidence = min(liq_data.ratio * 3, Decimal("1"))

        # 补充信号加成
        if use_supplementary:
            bonus = get_supplementary_bonus(
                cvd_follows=True,  # 简化：假设跟随
                depth_recovered=liq_data.ratio > Decimal("0.2"),
                speed_decayed=True  # 简化：假设衰减
            )
            confidence = min(confidence + bonus, Decimal("1"))

        reason = (
            f"清算数量={liq_data.count} >= {LIQ_COUNT_THRESHOLD}, "
            f"清算占比={liq_data.ratio:.2%} > {LIQ_RATIO_THRESHOLD:.2%}"
        )

        logger.info(
            f"分类结果: {category}, strategy={strategy}, "
            f"direction={trade_direction}, confidence={confidence:.2f}"
        )

        return ClassificationResult(
            category=category,
            strategy=strategy,
            direction=trade_direction,
            confidence=confidence,
            liq_count=liq_data.count,
            liq_ratio=liq_data.ratio,
            liq_value=liq_data.value,
            reason=reason
        )

    elif liq_data.count <= LIQ_COUNT_LOW and liq_data.ratio < LIQ_RATIO_LOW:
        # 几乎没有清算 → 过度反应
        category = "过度反应"
        strategy = "均值回归"
        trade_direction = "SELL" if direction == "up" else "BUY"

        # 信心度：较低
        confidence = Decimal("0.6")

        reason = (
            f"清算数量={liq_data.count} <= {LIQ_COUNT_LOW}, "
            f"清算占比={liq_data.ratio:.2%} < {LIQ_RATIO_LOW:.2%}"
        )

        logger.info(
            f"分类结果: {category}, strategy={strategy}, "
            f"direction={trade_direction}, confidence={confidence:.2f}"
        )

        return ClassificationResult(
            category=category,
            strategy=strategy,
            direction=trade_direction,
            confidence=confidence,
            liq_count=liq_data.count,
            liq_ratio=liq_data.ratio,
            liq_value=liq_data.value,
            reason=reason
        )

    else:
        # 中间状态 → 不确定
        category = "不确定"
        strategy = "放弃"
        trade_direction = "SELL" if direction == "up" else "BUY"

        confidence = Decimal("0.3")

        reason = (
            f"清算信号不明确: count={liq_data.count}, "
            f"ratio={liq_data.ratio:.2%}"
        )

        logger.info(f"分类结果: {category}, reason={reason}")

        return ClassificationResult(
            category=category,
            strategy=strategy,
            direction=trade_direction,
            confidence=confidence,
            liq_count=liq_data.count,
            liq_ratio=liq_data.ratio,
            liq_value=liq_data.value,
            reason=reason
        )


def classify_impact_sync(
    direction: str,
    impact_time: int,
    symbol: str = "BTCUSDT"
) -> ClassificationResult:
    """
    冲击分类（同步版本）

    不等待，直接用现有数据分类
    """
    logger.info(f"同步分类: direction={direction}, impact_time={impact_time}")

    liq_data = analyze_liquidations(direction, impact_time, symbol)

    logger.info(
        f"清算数据: count={liq_data.count}, volume={liq_data.volume}, "
        f"ratio={liq_data.ratio:.4f}"
    )

    # 分类逻辑（与async版本相同）
    if liq_data.count >= LIQ_COUNT_THRESHOLD and liq_data.ratio > LIQ_RATIO_THRESHOLD:
        category = "真突破"
        strategy = "趋势跟随"
        trade_direction = "BUY" if direction == "up" else "SELL"
        confidence = min(liq_data.ratio * 3, Decimal("1"))
        reason = f"清算数量={liq_data.count} >= {LIQ_COUNT_THRESHOLD}, 占比={liq_data.ratio:.2%}"

        return ClassificationResult(
            category=category,
            strategy=strategy,
            direction=trade_direction,
            confidence=confidence,
            liq_count=liq_data.count,
            liq_ratio=liq_data.ratio,
            liq_value=liq_data.value,
            reason=reason
        )

    elif liq_data.count <= LIQ_COUNT_LOW and liq_data.ratio < LIQ_RATIO_LOW:
        category = "过度反应"
        strategy = "均值回归"
        trade_direction = "SELL" if direction == "up" else "BUY"
        confidence = Decimal("0.6")
        reason = f"清算数量={liq_data.count} <= {LIQ_COUNT_LOW}, 占比={liq_data.ratio:.2%}"

        return ClassificationResult(
            category=category,
            strategy=strategy,
            direction=trade_direction,
            confidence=confidence,
            liq_count=liq_data.count,
            liq_ratio=liq_data.ratio,
            liq_value=liq_data.value,
            reason=reason
        )

    else:
        category = "不确定"
        strategy = "放弃"
        trade_direction = "SELL" if direction == "up" else "BUY"
        confidence = Decimal("0.3")
        reason = f"清算信号不明确: count={liq_data.count}, ratio={liq_data.ratio:.2%}"

        return ClassificationResult(
            category=category,
            strategy=strategy,
            direction=trade_direction,
            confidence=confidence,
            liq_count=liq_data.count,
            liq_ratio=liq_data.ratio,
            liq_value=liq_data.value,
            reason=reason
        )