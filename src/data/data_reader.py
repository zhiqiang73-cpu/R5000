"""
数据读取工具 - 从JSONL文件读取清算数据和成交数据
提供第2层所需的数据接口
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# 数据根目录
DATA_ROOT = Path("data/raw")


@dataclass
class Liquidation:
    """清算事件"""
    timestamp: int  # 毫秒时间戳
    symbol: str
    side: str  # "BUY" or "SELL" (空头被强平还是多头被强平)
    price: Decimal
    quantity: Decimal


def get_liquidations_since(time_ms: int, symbol: str = "BTCUSDT") -> List[Liquidation]:
    """
    获取指定时间之后的清算数据

    Args:
        time_ms: 毫秒时间戳（不含该时间点）
        symbol: 交易对，默认BTCUSDT

    Returns:
        List[Liquidation] - 清算事件列表，按时间升序
    """
    results: List[Liquidation] = []
    now = datetime.now(timezone.utc)

    # 读取最近7天的数据（清算事件不会太旧）
    for days_ago in range(7):
        date = now - timezone.timedelta(days=days_ago)
        date_str = date.strftime("%Y-%m-%d")
        file_path = DATA_ROOT / "liquidations" / f"{date_str}.jsonl"

        if not file_path.exists():
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        server_ts = record.get("server_ts", 0)

                        # 只取time_ms之后的
                        if server_ts <= time_ms:
                            continue

                        data = record.get("data", {})
                        if not data:
                            continue

                        order = data.get("o", {})
                        # 过滤交易对
                        s = data.get("s", "")
                        if s and s != symbol:
                            continue

                        liq = Liquidation(
                            timestamp=server_ts,
                            symbol=s or symbol,
                            side=order.get("S", ""),  # "SELL"=多头被强平, "BUY"=空头被强平
                            price=Decimal(str(order.get("p", "0"))),
                            quantity=Decimal(str(order.get("q", "0")))
                        )
                        results.append(liq)

                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        logger.debug(f"解析清算行失败: {e}")
                        continue

        except IOError as e:
            logger.warning(f"读取清算文件失败 {file_path}: {e}")

    # 按时间排序
    results.sort(key=lambda x: x.timestamp)
    return results


def get_volume_since(time_ms: int, symbol: str = "BTCUSDT") -> Decimal:
    """
    获取指定时间之后的总成交量

    Args:
        time_ms: 毫秒时间戳（不含该时间点）
        symbol: 交易对

    Returns:
        Decimal - 成交量总和
    """
    total_volume = Decimal("0")
    now = datetime.now(timezone.utc)

    # 读取最近24小时的成交数据（按小时分目录）
    for hours_ago in range(24):
        dt = now - timezone.timedelta(hours=hours_ago)
        date_str = dt.strftime("%Y-%m-%d")
        hour_str = dt.strftime("%H")
        file_path = DATA_ROOT / "trades" / date_str / f"{hour_str}.jsonl"

        if not file_path.exists():
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        server_ts = record.get("server_ts", 0)

                        if server_ts <= time_ms:
                            continue

                        data = record.get("data", {})
                        s = data.get("s", "")
                        if s and s != symbol:
                            continue

                        q = data.get("q", "0")
                        total_volume += Decimal(str(q))

                    except (json.JSONDecodeError, KeyError, ValueError):
                        continue

        except IOError:
            continue

    return total_volume


def get_volatility(minutes: int = 60, symbol: str = "BTCUSDT") -> Decimal:
    """
    计算近期波动率（基于成交数据的价格范围）

    Args:
        minutes: 回看分钟数，默认60分钟
        symbol: 交易对

    Returns:
        Decimal - 波动率（价格变化百分比，0.01 = 1%）
    """
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - minutes * 60 * 1000

    prices: List[Decimal] = []

    # 收集价格数据
    now = datetime.now(timezone.utc)
    for hours_ago in range(2):  # 最多看2小时
        dt = now - timezone.timedelta(hours=hours_ago)
        date_str = dt.strftime("%Y-%m-%d")
        hour_str = dt.strftime("%H")
        file_path = DATA_ROOT / "trades" / date_str / f"{hour_str}.jsonl"

        if not file_path.exists():
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        server_ts = record.get("server_ts", 0)

                        if server_ts < start_ms:
                            continue

                        data = record.get("data", {})
                        s = data.get("s", "")
                        if s and s != symbol:
                            continue

                        p = data.get("p", "0")
                        if p and p != "0":
                            prices.append(Decimal(str(p)))

                    except (json.JSONDecodeError, KeyError, ValueError):
                        continue

        except IOError:
            continue

    if len(prices) < 10:
        # 数据不足，返回默认波动率
        return Decimal("0.005")  # 0.5%

    # 计算波动率：价格范围的百分比
    price_min = min(prices)
    price_max = max(prices)
    price_mean = sum(prices) / len(prices)

    if price_mean == 0:
        return Decimal("0.005")

    volatility = (price_max - price_min) / price_mean
    return volatility


def get_price_at_time(time_ms: int, symbol: str = "BTCUSDT") -> Optional[Decimal]:
    """
    获取指定时间点的价格（最近的一笔成交）

    Args:
        time_ms: 毫秒时间戳
        symbol: 交易对

    Returns:
        Decimal - 价格，或None
    """
    now = datetime.now(timezone.utc)

    # 往前找最近的成交记录
    for hours_ago in range(2):
        dt = now - timezone.timedelta(hours=hours_ago)
        date_str = dt.strftime("%Y-%m-%d")
        hour_str = dt.strftime("%H")
        file_path = DATA_ROOT / "trades" / date_str / f"{hour_str}.jsonl"

        if not file_path.exists():
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                # 倒序读取找最近的
                lines = f.readlines()
                for line in reversed(lines):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        server_ts = record.get("server_ts", 0)

                        if server_ts <= time_ms:
                            data = record.get("data", {})
                            s = data.get("s", "")
                            if s and s != symbol:
                                continue

                            p = data.get("p", "0")
                            if p and p != "0":
                                return Decimal(str(p))

                    except (json.JSONDecodeError, KeyError, ValueError):
                        continue

        except IOError:
            continue

    return None


def get_recent_trades(minutes: int = 5, symbol: str = "BTCUSDT") -> List[dict]:
    """
    获取最近几分钟的成交数据（用于实时计算）

    Args:
        minutes: 回看分钟数
        symbol: 交易对

    Returns:
        List[dict] - 成交记录列表，包含price, quantity, timestamp
    """
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - minutes * 60 * 1000
    results: List[dict] = []
    now = datetime.now(timezone.utc)

    for hours_ago in range(1):
        dt = now - timezone.timedelta(hours=hours_ago)
        date_str = dt.strftime("%Y-%m-%d")
        hour_str = dt.strftime("%H")
        file_path = DATA_ROOT / "trades" / date_str / f"{hour_str}.jsonl"

        if not file_path.exists():
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        server_ts = record.get("server_ts", 0)

                        if server_ts < start_ms:
                            continue

                        data = record.get("data", {})
                        s = data.get("s", "")
                        if s and s != symbol:
                            continue

                        results.append({
                            "timestamp": server_ts,
                            "price": Decimal(str(data.get("p", "0"))),
                            "quantity": Decimal(str(data.get("q", "0"))),
                            "buyer_maker": data.get("m", False)  # aggTrade: m=True 表示卖方主动
                        })

                    except (json.JSONDecodeError, KeyError, ValueError):
                        continue

        except IOError:
            continue

    return results