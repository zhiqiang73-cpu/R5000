# -*- coding: utf-8 -*-
"""
BTCUSDT 量化交易系统 - 第2层: 冲击检测 + 清算分类
核心: 用清算数据判断过度反应(均值回归) vs 真突破(趋势跟随)
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, List, Dict, Any
import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

from .time_context import get_time_context  # Layer1依赖

logger = logging.getLogger(__name__)
DATA_ROOT = Path("data/raw")


@dataclass
class ImpactEvent:
    detected_at_ms: int
    direction: str  # "up" | "down"
    price_before: Decimal
    price_after: Decimal
    price_change_pct: Decimal
    volume_30s: Decimal
    volume_baseline: Decimal
    volume_surge_ratio: Decimal


@dataclass
class ClassificationResult:
    impact: ImpactEvent
    classification: str  # "过度反应" | "真突破" | "不确定"
    strategy: str        # "均值回归" | "趋势跟随" | "放弃"
    confidence: Decimal  # 0-1
    liq_count: int
    liq_value: Decimal
    liq_ratio: Decimal
    cvd_follows: bool
    wait_seconds: int


def _read_trades_since(since_ms: int) -> List[dict]:
    """从 trades JSONL 读取 since_ms 后的 BTCUSDT aggTrade (跨小时)"""
    trades = []
    now_utc = datetime.now(timezone.utc)
    date_str = now_utc.strftime("%Y-%m-%d")
    current_hour = now_utc.hour

    for h_offset in [0, -1]:  # 当前小时 + 前一小时（防止跨整点丢数据）
        h = current_hour + h_offset
        if h < 0:
            # 跨天：取前一天的第23小时
            prev_date = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
            file_path = DATA_ROOT / "trades" / prev_date / "23.jsonl"
        else:
            file_path = DATA_ROOT / "trades" / date_str / f"{h:02d}.jsonl"

        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    record = json.loads(line)
                    if record["server_ts"] >= since_ms and record["data"]["s"] == "BTCUSDT":
                        trades.append(record["data"])

    logger.debug(f"读取 trades since {since_ms}: {len(trades)} 条")
    return trades


def _read_liqs_since(since_ms: int) -> List[dict]:
    """从 liquidations JSONL 读取 since_ms 后的清算（跨天）"""
    liqs = []
    now_utc = datetime.now(timezone.utc)

    # 防止跨天时漏掉前一天数据（与 time_context.py 逻辑一致）
    dates_to_check = [
        now_utc.strftime("%Y-%m-%d"),
        (now_utc - timedelta(days=1)).strftime("%Y-%m-%d"),
    ]

    for date_str in dates_to_check:
        file_path = DATA_ROOT / "liquidations" / f"{date_str}.jsonl"
        if not file_path.exists():
            continue
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                record = json.loads(line)
                if record["server_ts"] >= since_ms and record["data"]["o"]["s"] == "BTCUSDT":
                    liqs.append(record["data"]["o"])

    logger.debug(f"读取 liqs since {since_ms}: {len(liqs)} 条")
    return liqs


def get_cvd_since(since_ms: int) -> Decimal:
    """计算 CVD: 买方主动 +q, 卖方主动 -q (BTC qty)

    Binance aggTrade: m=True 表示 buyer 是 maker（被动方），
    即该笔交易由 seller 主动发起 → 计为 -q（卖方主动）
    """
    trades = _read_trades_since(since_ms)
    cvd = Decimal("0")
    for trade in trades:
        qty = Decimal(trade["q"])
        # m=True: buyer maker → seller aggressor → -q（卖方主动，看空）
        cvd += -qty if trade["m"] else qty
    logger.debug(f"CVD since {since_ms}: {cvd}")
    return cvd


# ── 分钟级清算累积触发常量 ──────────────────────────────────────────────────
LIQ_ACCUM_WINDOW_MS   = 60_000        # 60秒滑动窗口
LIQ_ACCUM_THRESHOLD   = Decimal("200000")  # 主导方向清算金额阈值 $200k
LIQ_DOMINANCE_MIN     = Decimal("0.70")    # 主导方向占比下限（70%）
LIQ_ACCUM_COOLDOWN_MS = 120_000       # 两次触发之间最短间隔 120s

_last_liq_impact_ts: int = 0  # 模块级：上次触发时间戳（ms）


def detect_liq_impact(
    threshold: Decimal = LIQ_ACCUM_THRESHOLD,
    window_seconds: int = 60,
    cooldown_seconds: int = 120,
) -> Optional["ImpactEvent"]:
    """
    分钟级清算累积触发器（与 detect_impact 并列，OR 关系）

    当 60s 内单方向清算金额 >= $200k 且方向占比 >= 70% 时触发，
    捕捉"大资金分批入场"导致的清算级联（即使 30s 内价格变动 < 0.15%）。

    Returns:
        ImpactEvent 或 None
    """
    global _last_liq_impact_ts

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    since_ms = now_ms - window_seconds * 1000

    # 冷却期：避免同一波清算反复触发
    if now_ms - _last_liq_impact_ts < cooldown_seconds * 1000:
        return None

    liqs = _read_liqs_since(since_ms)
    if not liqs:
        return None

    # 按方向统计清算金额
    buy_val  = Decimal("0")   # 空头被清（BUY单）→ 价格涨
    sell_val = Decimal("0")   # 多头被清（SELL单）→ 价格跌
    for liq in liqs:
        val = Decimal(liq["q"]) * Decimal(liq["ap"])
        if liq["S"] == "BUY":
            buy_val  += val
        else:
            sell_val += val

    total_val   = buy_val + sell_val
    dominant    = max(buy_val, sell_val)
    if total_val <= 0 or dominant < threshold:
        return None
    if dominant / total_val < LIQ_DOMINANCE_MIN:
        return None  # 方向不明（多空混战），不触发

    direction = "up" if buy_val > sell_val else "down"

    # 取价格：用 60s 内的 aggTrade 首末价格
    trades = _read_trades_since(since_ms)
    if len(trades) < 2:
        return None
    sorted_t = sorted(trades, key=lambda t: t["T"])
    price_before      = Decimal(sorted_t[0]["p"])
    price_after       = Decimal(sorted_t[-1]["p"])
    price_change_pct  = abs(price_after - price_before) / price_before if price_before > 0 else Decimal("0")

    # 成交量（60s 窗口）
    volume_60s = sum(Decimal(t["q"]) for t in sorted_t)
    vol_1h_since = now_ms - 3_600_000
    vol_1h = sum(Decimal(t["q"]) for t in _read_trades_since(vol_1h_since))
    volume_baseline    = vol_1h / Decimal("60") if vol_1h > 0 else volume_60s * Decimal("0.5")
    volume_surge_ratio = volume_60s / volume_baseline if volume_baseline > 0 else Decimal("1")

    _last_liq_impact_ts = now_ms
    logger.info(
        f"[清算累积触发] dir={direction}, dominant={dominant:,.0f}USD, "
        f"dominance={dominant/total_val:.0%}, price_chg={price_change_pct:.3%}"
    )
    return ImpactEvent(
        detected_at_ms=now_ms,
        direction=direction,
        price_before=price_before,
        price_after=price_after,
        price_change_pct=price_change_pct,
        volume_30s=volume_60s,       # 字段名沿用 30s，实际为 60s 窗口
        volume_baseline=volume_baseline,
        volume_surge_ratio=volume_surge_ratio,
    )


def detect_impact(
    lookback_seconds: int = 30,
    impact_threshold_pct: Decimal = Decimal("0.0015"),  # 0.15% 最小阈值
    volatility_multiplier: Decimal = Decimal("1.5"),    # 动态阈值倍数
    volume_surge_threshold: Decimal = Decimal("2.0"),   # 成交量激增倍数
) -> Optional[ImpactEvent]:
    """检测价格冲击"""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    since_ms = now_ms - lookback_seconds * 1000

    trades = _read_trades_since(since_ms)
    if len(trades) < 5:
        logger.debug("Trades不足, 无冲击")
        return None

    # 价格变动: 首末价格（按时间戳排序）
    sorted_trades = sorted(trades, key=lambda t: t["T"])
    price_before = Decimal(sorted_trades[0]["p"])
    price_after = Decimal(sorted_trades[-1]["p"])
    price_change_pct = abs(price_after - price_before) / price_before

    # 30秒内成交量
    volume_30s = sum(Decimal(t["q"]) for t in trades)

    # 基线：近1h成交量 ÷ 120（转换为 30s 基线）
    vol_60min_since = now_ms - 3600 * 1000
    vol_60min = sum(Decimal(t["q"]) for t in _read_trades_since(vol_60min_since))
    volume_baseline = vol_60min / 120 if vol_60min > 0 else volume_30s * Decimal("0.5")
    volume_surge_ratio = volume_30s / volume_baseline

    # 波动率 (近60min price std / mean，标准化)
    prices_60min = [Decimal(t["p"]) for t in _read_trades_since(vol_60min_since)]
    if len(prices_60min) > 1:
        mean_p = sum(prices_60min) / len(prices_60min)
        var = sum((p - mean_p) ** 2 for p in prices_60min) / len(prices_60min)
        volatility = Decimal(str(float(var) ** 0.5)) / mean_p
    else:
        volatility = Decimal("0.002")  # fallback 0.2%

    dynamic_threshold = max(impact_threshold_pct, volatility * volatility_multiplier)

    logger.info(
        f"冲击检测: change={price_change_pct:.4f}, surge={volume_surge_ratio:.2f}, "
        f"dyn_th={dynamic_threshold:.4f}, vol={volatility:.4f}"
    )

    if price_change_pct > dynamic_threshold and volume_surge_ratio > volume_surge_threshold:
        direction = "up" if price_after > price_before else "down"
        return ImpactEvent(
            detected_at_ms=now_ms,
            direction=direction,
            price_before=price_before,
            price_after=price_after,
            price_change_pct=price_change_pct,
            volume_30s=volume_30s,
            volume_baseline=volume_baseline,
            volume_surge_ratio=volume_surge_ratio,
        )

    return None


async def classify_impact(
    impact: ImpactEvent,
    wait_seconds: int = 45,             # 观察窗口
    liq_count_threshold: int = 5,       # 高清算量阈值（笔数）
    liq_ratio_threshold: Decimal = Decimal("0.15"),  # 清算量占成交量15%
    low_liq_count: int = 2,             # 低清算量阈值（笔数）
    low_liq_ratio: Decimal = Decimal("0.05"),        # 清算量占比5%
    liq_value_breakout_min: Decimal = Decimal("200000"),  # 金额优先阈值（USD）
) -> ClassificationResult:
    """清算分类：等待观察窗口后，用清算数据判断冲击类型"""
    logger.info(f"分类冲击 {impact.direction}, 等待 {wait_seconds}s...")
    await asyncio.sleep(wait_seconds)

    since_ms = impact.detected_at_ms
    liqs = _read_liqs_since(since_ms)
    trades_since = _read_trades_since(since_ms)

    # 方向一致的清算（下跌冲击看多头SELL清算，上涨冲击看空头BUY清算）
    relevant_liqs = []
    for liq in liqs:
        side = liq["S"]
        if (impact.direction == "down" and side == "SELL") or \
           (impact.direction == "up" and side == "BUY"):
            qty = Decimal(liq["q"])
            price = Decimal(liq["p"])
            relevant_liqs.append({"qty": qty, "value": qty * price})

    liq_count = len(relevant_liqs)
    liq_value = sum(l["value"] for l in relevant_liqs)

    # 清算量占比（USDT / USDT）
    total_vol_usdt = sum(Decimal(t["q"]) for t in trades_since) * Decimal(impact.price_after)
    liq_ratio = liq_value / total_vol_usdt if total_vol_usdt > 0 else Decimal("0")

    # CVD 辅助判断
    cvd = get_cvd_since(since_ms)
    cvd_follows = (cvd > 0 and impact.direction == "up") or \
                  (cvd < 0 and impact.direction == "down")

    # 分类（优先级：笔数+比例 > 金额优先 > 低清算 > 不确定）
    if liq_count >= liq_count_threshold and liq_ratio > liq_ratio_threshold:
        # ① 高笔数 + 高比例 → 真突破（原逻辑，confidence 由 liq_ratio 决定）
        classification = "真突破"
        strategy = "趋势跟随"
        confidence = min(liq_ratio * 3, Decimal("1"))
        reason = f"高清算: {liq_count}笔, {liq_ratio:.2%}"
    elif liq_count <= low_liq_count and liq_ratio < low_liq_ratio:
        # ② 低清算 → 过度反应（原逻辑）
        classification = "过度反应"
        strategy = "均值回归"
        confidence = Decimal("0.6")
        reason = f"低清算: {liq_count}笔, {liq_ratio:.2%}"
    elif liq_value >= liq_value_breakout_min:
        # ③ 金额优先：笔数不足但金额大（e.g. 几笔大单清算）→ 真突破
        # confidence 随金额线性增长：$200k→0.60, $1M→0.78, $2M→1.0
        val_conf = min(
            Decimal("0.6") + (liq_value - liq_value_breakout_min)
            / Decimal("1800000") * Decimal("0.4"),
            Decimal("1"),
        )
        classification = "真突破"
        strategy = "趋势跟随"
        confidence = val_conf
        reason = f"强清算金额: USD {liq_value:,.0f} ({liq_count}笔)"
    else:
        # ④ 清算信号模糊 → 放弃
        classification = "不确定"
        strategy = "放弃"
        confidence = Decimal("0")
        reason = f"清算模糊: {liq_count}笔, {liq_ratio:.2%}"

    logger.info(
        f"分类结果: {classification} ({strategy}), conf={confidence}, "
        f"{reason}, CVD_follows={cvd_follows}"
    )

    return ClassificationResult(
        impact=impact,
        classification=classification,
        strategy=strategy,
        confidence=confidence,
        liq_count=liq_count,
        liq_value=liq_value,
        liq_ratio=liq_ratio,
        cvd_follows=cvd_follows,
        wait_seconds=wait_seconds,
    )


__all__ = ["ImpactEvent", "ClassificationResult", "detect_impact", "classify_impact"]
