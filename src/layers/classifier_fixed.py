"""
BTCUSDT Quant System - Layer 2: Impact Detection and Liquidation Classification
Core: Use liquidation data to judge overreaction (mean reversion) vs true breakout (trend following)
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, List, Dict, Any
import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime

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
    classification: str  # "overreaction" | "true_breakout" | "uncertain"
    strategy: str  # "mean_reversion" | "trend_following" | "abandon"
    confidence: Decimal  # 0-1
    liq_count: int
    liq_value: Decimal
    liq_ratio: Decimal
    cvd_follows: bool
    wait_seconds: int

def _read_trades_since(since_ms: int) -> List[dict]:
    """Read BTCUSDT aggTrade from trades JSONL since timestamp"""
    trades = []
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    hour_str = now.strftime("%H")

    file_path = DATA_ROOT / "trades" / date_str / f"{hour_str}.jsonl"
    if not file_path.exists():
        logger.warning(f"Trades file not exist: {file_path}")
        return trades

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            if record["server_ts"] >= since_ms and record["data"]["s"] == "BTCUSDT":
                trades.append(record["data"])

    logger.debug(f"Read trades since {since_ms}: {len(trades)} records")
    return trades

def _read_liqs_since(since_ms: int) -> List[dict]:
    """Read liquidations from JSONL since timestamp"""
    liqs = []
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")

    file_path = DATA_ROOT / "liquidations" / f"{date_str}.jsonl"
    if not file_path.exists():
        logger.warning(f"Liqs file not exist: {file_path}")
        return liqs

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            if record["server_ts"] >= since_ms and record["data"]["o"]["s"] == "BTCUSDT":
                liqs.append(record["data"]["o"])

    logger.debug(f"Read liqs since {since_ms}: {len(liqs)} records")
    return liqs

def get_cvd_since(since_ms: int) -> Decimal:
    """Calculate CVD: buyer maker +q, seller maker -q (BTC qty)"""
    trades = _read_trades_since(since_ms)
    cvd = Decimal("0")
    for trade in trades:
        qty = Decimal(trade["q"])
        is_buyer_maker = trade["m"]  # m=true: buyer is maker (buy order)
        cvd += qty if is_buyer_maker else -qty
    logger.debug(f"CVD since {since_ms}: {cvd}")
    return cvd

def detect_impact(
    lookback_seconds: int = 30,
    impact_threshold_pct: Decimal = Decimal("0.0015"),
    volatility_multiplier: Decimal = Decimal("1.5"),
    volume_surge_threshold: Decimal = Decimal("2.0"),
) -> Optional[ImpactEvent]:
    """Detect price impact"""
    now_ms = int(datetime.now().timestamp() * 1000)
    since_ms = now_ms - lookback_seconds * 1000

    trades = _read_trades_since(since_ms)
    if len(trades) < 5:
        logger.debug("Not enough trades, no impact")
        return None

    # Price change: first and last price
    sorted_trades = sorted(trades, key=lambda t: t["T"])
    price_before = Decimal(sorted_trades[0]["p"])
    price_after = Decimal(sorted_trades[-1]["p"])
    price_change_pct = abs(price_after - price_before) / price_before

    # Volume
    volume_30s = sum(Decimal(t["q"]) for t in trades)

    # Baseline: 75th percentile of recent 60min (simplified)
    vol_60min_since = now_ms - 3600 * 1000
    vol_60min = sum(Decimal(t["q"]) for t in _read_trades_since(vol_60min_since))
    volume_baseline = vol_60min / 60 if vol_60min > 0 else volume_30s * Decimal("0.5")
    volume_surge_ratio = volume_30s / volume_baseline

    # Volatility (recent 60min price std / mean)
    prices_60min = [Decimal(t["p"]) for t in _read_trades_since(vol_60min_since)]
    if len(prices_60min) > 1:
        mean_p = sum(prices_60min) / len(prices_60min)
        var = sum((p - mean_p)**2 for p in prices_60min) / len(prices_60min)
        volatility = (var ** Decimal("0.5")) / mean_p
    else:
        volatility = Decimal("0.002")

    dynamic_threshold = max(impact_threshold_pct, volatility * volatility_multiplier)

    logger.info(f"Impact detection: change={price_change_pct:.4f}, surge={volume_surge_ratio:.2f}, "
                f"dyn_th={dynamic_threshold:.4f}, vol={volatility:.4f}")

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
            volume_surge_ratio=volume_surge_ratio
        )

    return None

async def classify_impact(
    impact: ImpactEvent,
    wait_seconds: int = 45,
    liq_count_threshold: int = 5,
    liq_ratio_threshold: Decimal = Decimal("0.15"),
    low_liq_count: int = 2,
    low_liq_ratio: Decimal = Decimal("0.05"),
) -> ClassificationResult:
    """Classification using liquidation data"""
    logger.info(f"Classifying impact {impact.direction}, waiting {wait_seconds}s...")
    await asyncio.sleep(wait_seconds)

    since_ms = impact.detected_at_ms
    liqs = _read_liqs_since(since_ms)
    trades_since = _read_trades_since(since_ms)

    # Direction-consistent liquidations
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

    total_volume = sum(Decimal(t["q"]) for t in trades_since)
    liq_ratio = liq_value / (total_volume * Decimal(impact.price_after)) if total_volume * Decimal(impact.price_after) > 0 else Decimal("0")

    # CVD
    cvd = get_cvd_since(since_ms)
    cvd_follows = (cvd > 0 and impact.direction == "up") or (cvd < 0 and impact.direction == "down")

    # Classification
    if liq_count >= liq_count_threshold and liq_ratio > liq_ratio_threshold:
        classification = "true_breakout"
        strategy = "trend_following"
        confidence = min(liq_ratio * 3, Decimal("1"))
        reason = f"High liquidation: {liq_count} trades, {liq_ratio:.2%}"
    elif liq_count <= low_liq_count and liq_ratio < low_liq_ratio:
        classification = "overreaction"
        strategy = "mean_reversion"
        confidence = Decimal("0.6")
        reason = f"Low liquidation: {liq_count} trades, {liq_ratio:.2%}"
    else:
        classification = "uncertain"
        strategy = "abandon"
        confidence = Decimal("0")
        reason = f"Unclear liquidation: {liq_count} trades, {liq_ratio:.2%}"

    logger.info(f"Classification result: {classification} ({strategy}), conf={confidence}, {reason}, CVD_follows={cvd_follows}")

    return ClassificationResult(
        impact=impact,
        classification=classification,
        strategy=strategy,
        confidence=confidence,
        liq_count=liq_count,
        liq_value=liq_value,
        liq_ratio=liq_ratio,
        cvd_follows=cvd_follows,
        wait_seconds=wait_seconds
    )

__all__ = ["ImpactEvent", "ClassificationResult", "detect_impact", "classify_impact"]