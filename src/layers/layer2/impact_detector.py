"""
Layer2 冲击检测器

检测是否发生价格冲击（价格快速变动 + 成交量激增）

核心逻辑：
- 动态阈值：基于近期波动率，阈值 = max(0.15%, volatility * 1.5)
- 30秒窗口：检测30秒内的价格变动
- 成交量对比：30秒成交量 / 近期基准成交量 > 2.0
"""

import logging
import time
from decimal import Decimal
from typing import Optional, Tuple

from src.data.data_reader import (
    get_price_at_time,
    get_recent_trades,
    get_volatility,
)

logger = logging.getLogger(__name__)

# 配置常量（可调参数）
WINDOW_SECONDS = 30  # 检测窗口秒数
MIN_TRADE_COUNT = 5  # 最小成交笔数（数据不足不检测）

# 最小阈值：0.15% 价格变动
MIN_PRICE_CHANGE = Decimal("0.0015")
# 动态阈值乘数：波动率 * 1.5
VOLATILITY_MULTIPLIER = Decimal("1.5")
# 成交量激增倍数阈值
VOLUME_SURGE_THRESHOLD = Decimal("2.0")


def calculate_volume_baseline(minutes: int = 60, symbol: str = "BTCUSDT") -> Decimal:
    """
    计算成交量基准（第75百分位数）

    Args:
        minutes: 回看分钟数
        symbol: 交易对

    Returns:
        Decimal - 成交量基准（30秒窗口的平均成交量）
    """
    trades = get_recent_trades(minutes=minutes, symbol=symbol)

    if len(trades) < 10:
        return Decimal("0")

    # 按30秒窗口聚合
    volumes_30s = []
    current_window: list = []
    window_start = trades[0]["timestamp"]

    for trade in trades:
        if trade["timestamp"] - window_start >= 30000:  # 30秒
            if current_window:
                window_vol = sum(t["quantity"] for t in current_window)
                volumes_30s.append(window_vol)
            current_window = [trade]
            window_start = trade["timestamp"]
        else:
            current_window.append(trade)

    # 最后一个窗口
    if current_window:
        window_vol = sum(t["quantity"] for t in current_window)
        volumes_30s.append(window_vol)

    if not volumes_30s:
        return Decimal("0")

    # 计算第75百分位数
    volumes_30s.sort()
    idx = int(len(volumes_30s) * 0.75)
    idx = min(idx, len(volumes_30s) - 1)
    return volumes_30s[idx]


def detect_impact(symbol: str = "BTCUSDT") -> Tuple[bool, Optional[str], Optional[Decimal]]:
    """
    检测是否发生价格冲击

    Args:
        symbol: 交易对，默认BTCUSDT

    Returns:
        (是否检测到冲击, 方向, 幅度)
        - is_impact: True表示检测到冲击
        - direction: "up" 或 "down"，None表示未检测到
        - magnitude: 价格变动幅度（比例），None表示未检测到
    """
    now_ms = int(time.time() * 1000)
    window_ms = WINDOW_SECONDS * 1000

    # 获取窗口内的成交数据
    trades = get_recent_trades(minutes=5, symbol=symbol)

    # 过滤出窗口内的成交
    window_trades = [t for t in trades if t["timestamp"] >= now_ms - window_ms]

    if len(window_trades) < MIN_TRADE_COUNT:
        logger.debug(f"成交数据不足: {len(window_trades)} 笔")
        return False, None, None

    # 获取窗口起始价格（30秒前）
    start_time = now_ms - window_ms
    start_price = get_price_at_time(start_time, symbol)

    if start_price is None:
        # 尝试用窗口内最早的成交
        if window_trades:
            start_price = window_trades[0]["price"]
            start_time = window_trades[0]["timestamp"]
        else:
            return False, None, None

    # 获取当前价格
    current_price = window_trades[-1]["price"]

    # 计算价格变动
    price_change = abs(current_price - start_price) / start_price

    # 计算动态阈值
    volatility = get_volatility(minutes=60, symbol=symbol)
    dynamic_threshold = max(MIN_PRICE_CHANGE, volatility * VOLATILITY_MULTIPLIER)

    # 计算成交量
    window_volume = sum(t["quantity"] for t in window_trades)
    volume_baseline = calculate_volume_baseline(minutes=60, symbol=symbol)

    if volume_baseline > 0:
        volume_surge = window_volume / volume_baseline
    else:
        volume_surge = Decimal("1")

    # 判断条件
    price_triggered = price_change > dynamic_threshold
    volume_triggered = volume_surge > VOLUME_SURGE_THRESHOLD

    logger.info(
        f"冲击检测: 价格变动={price_change:.4f}, 阈值={dynamic_threshold:.4f}, "
        f"成交量激增={volume_surge:.2f}x, 窗口={len(window_trades)}笔"
    )

    if price_triggered and volume_triggered:
        direction = "up" if current_price > start_price else "down"
        logger.info(
            f"检测到冲击: direction={direction}, magnitude={price_change:.4f}, "
            f"volume_surge={volume_surge:.2f}"
        )
        return True, direction, price_change

    return False, None, None


def get_impact_details(symbol: str = "BTCUSDT") -> Optional[dict]:
    """
    获取冲击事件的详细信息（用于调试和分析）

    Args:
        symbol: 交易对

    Returns:
        dict 或 None
    """
    now_ms = int(time.time() * 1000)
    window_ms = WINDOW_SECONDS * 1000

    trades = get_recent_trades(minutes=5, symbol=symbol)
    window_trades = [t for t in trades if t["timestamp"] >= now_ms - window_ms]

    if len(window_trades) < MIN_TRADE_COUNT:
        return None

    start_time = now_ms - window_ms
    start_price = get_price_at_time(start_time, symbol)

    if start_price is None:
        if window_trades:
            start_price = window_trades[0]["price"]
            start_time = window_trades[0]["timestamp"]
        else:
            return None

    current_price = window_trades[-1]["price"]
    window_volume = sum(t["quantity"] for t in window_trades)
    volume_baseline = calculate_volume_baseline(minutes=60, symbol=symbol)

    volatility = get_volatility(minutes=60, symbol=symbol)
    dynamic_threshold = max(MIN_PRICE_CHANGE, volatility * VOLATILITY_MULTIPLIER)

    return {
        "start_time": start_time,
        "end_time": window_trades[-1]["timestamp"],
        "start_price": start_price,
        "end_price": current_price,
        "price_change": abs(current_price - start_price) / start_price,
        "dynamic_threshold": dynamic_threshold,
        "volatility": volatility,
        "volume_30s": window_volume,
        "volume_baseline": volume_baseline,
        "volume_surge": window_volume / volume_baseline if volume_baseline > 0 else Decimal("1"),
        "trade_count": len(window_trades)
    }