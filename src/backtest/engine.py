from __future__ import annotations

import argparse
import json
import logging
from bisect import bisect_left, bisect_right
from collections import OrderedDict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.backtest.metrics import build_metrics
from src.layers.classifier import ClassificationResult, ImpactEvent
from src.layers.environment import EnvironmentResult
from src.layers.executor import TradeSignal, execute_signal
from src.risk.circuit_breaker import RiskManager

logger = logging.getLogger(__name__)
UTC = timezone.utc


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    return Decimal(str(value))


def _merge_config(base: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(base)
    if overrides:
        merged.update(overrides)
    return merged


def _utc_day(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _parse_date_window(start_date: str, end_date: str) -> tuple[int, int]:
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=UTC)
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(start_dt.timestamp() * 1000), int((end_dt + timedelta(days=1)).timestamp() * 1000) - 1


def _iter_dates(start_date: str, end_date: str) -> Iterable[str]:
    current = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    while current <= end_dt:
        yield current.strftime("%Y-%m-%d")
        current += timedelta(days=1)


@dataclass(frozen=True)
class TradeTick:
    timestamp: int
    price: Decimal
    quantity: Decimal
    is_buyer_maker: bool
    symbol: str = "BTCUSDT"

    @property
    def notional(self) -> Decimal:
        return self.price * self.quantity


@dataclass(frozen=True)
class LiquidationTick:
    timestamp: int
    symbol: str
    side: str
    price: Decimal
    quantity: Decimal

    @property
    def value(self) -> Decimal:
        return self.price * self.quantity


@dataclass(frozen=True)
class BacktestTimeContext:
    timestamp: int
    utc_hour: int
    hours_to_funding: int


@dataclass(frozen=True)
class ImpactCandidate:
    impact: ImpactEvent
    environment: EnvironmentResult
    trigger_type: str = "price_surge"      # "price_surge" | "liq_accum"
    pre_liq_value: Decimal = Decimal("0")  # liq_accum路径：60s窗口主导方清算金额
    pre_liq_count: int = 0                 # liq_accum路径：60s窗口主导方清算笔数


@dataclass
class ExecutedTrade:
    strategy: str
    grade: str
    side: str
    entry_type: str
    entry_time: int
    exit_time: int
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    planned_rr: Decimal
    confidence: Decimal
    impact_time: int
    signal_time: int
    entry_reference: Decimal
    exit_reference: Decimal
    entry_fee: Decimal
    exit_fee: Decimal
    slippage_cost: Decimal
    gross_pnl: Decimal
    pnl: Decimal
    exit_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "grade": self.grade,
            "side": self.side,
            "entry_type": self.entry_type,
            "entry_time": self.entry_time,
            "exit_time": self.exit_time,
            "entry_price": float(self.entry_price),
            "exit_price": float(self.exit_price),
            "quantity": float(self.quantity),
            "stop_loss": float(self.stop_loss),
            "take_profit": float(self.take_profit),
            "planned_rr": float(self.planned_rr),
            "confidence": float(self.confidence),
            "impact_time": self.impact_time,
            "signal_time": self.signal_time,
            "entry_reference": float(self.entry_reference),
            "exit_reference": float(self.exit_reference),
            "entry_fee": float(self.entry_fee),
            "exit_fee": float(self.exit_fee),
            "slippage_cost": float(self.slippage_cost),
            "gross_pnl": float(self.gross_pnl),
            "pnl": float(self.pnl),
            "exit_reason": self.exit_reason,
        }


@dataclass(frozen=True)
class BacktestDataset:
    trades: tuple[TradeTick, ...] = field(default_factory=tuple)
    liquidations: tuple[LiquidationTick, ...] = field(default_factory=tuple)
    symbol: str = "BTCUSDT"
    trade_timestamps: tuple[int, ...] = field(init=False, repr=False)
    liquidation_timestamps: tuple[int, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        sorted_trades = tuple(sorted(self.trades, key=lambda item: item.timestamp))
        sorted_liqs = tuple(sorted(self.liquidations, key=lambda item: item.timestamp))
        object.__setattr__(self, "trades", sorted_trades)
        object.__setattr__(self, "liquidations", sorted_liqs)
        object.__setattr__(self, "trade_timestamps", tuple(item.timestamp for item in sorted_trades))
        object.__setattr__(self, "liquidation_timestamps", tuple(item.timestamp for item in sorted_liqs))

    @property
    def start_time(self) -> int:
        values = []
        if self.trades:
            values.append(self.trades[0].timestamp)
        if self.liquidations:
            values.append(self.liquidations[0].timestamp)
        return min(values) if values else 0

    @property
    def end_time(self) -> int:
        values = []
        if self.trades:
            values.append(self.trades[-1].timestamp)
        if self.liquidations:
            values.append(self.liquidations[-1].timestamp)
        return max(values) if values else 0

    def slice(self, start_ms: int | None = None, end_ms: int | None = None) -> "BacktestDataset":
        if not self.trades and not self.liquidations:
            return self
        start_value = self.start_time if start_ms is None else start_ms
        end_value = self.end_time if end_ms is None else end_ms
        trade_left = bisect_left(self.trade_timestamps, start_value)
        trade_right = bisect_right(self.trade_timestamps, end_value)
        liq_left = bisect_left(self.liquidation_timestamps, start_value)
        liq_right = bisect_right(self.liquidation_timestamps, end_value)
        return BacktestDataset(
            trades=self.trades[trade_left:trade_right],
            liquidations=self.liquidations[liq_left:liq_right],
            symbol=self.symbol,
        )

    def is_empty(self) -> bool:
        return not self.trades


DEFAULT_CONFIG: dict[str, Any] = {
    "initial_capital": Decimal("10000"),
    "impact_window_ms": 30_000,
    "impact_cooldown_ms": 60_000,              # 30s cooldown (was 60s) — 允许更密集冲击检测
    "min_trade_count": 5,
    "min_price_change": Decimal("0.0015"),
    "volatility_multiplier": Decimal("1.5"),
    "volume_surge_threshold": Decimal("2.0"),  # 1.5x (was 2.0) — 降低成交量门槛让更多冲击通过
    "classification_wait_ms": 45_000,          # 30s (was 45s) — 缩短等待，减少价格已回归的废信号
    "liq_count_threshold": 3,                  # 3笔 (was 5) — 更多事件可被识别为真突破
    "liq_count_low": 2,               # 4笔以下清算视为过度反应 (was 2) — 扩大MR信号池
    "liq_ratio_threshold": Decimal("0.0005"),
    "liq_ratio_low": Decimal("0.001"),
    "liq_value_min": Decimal("30000"),
    "liq_value_breakout_min": Decimal("200000"),
    "taker_fee": Decimal("0.0005"),
    "maker_fee": Decimal("0.0002"),
    "taker_slippage": Decimal("0.0003"),
    "maker_slippage": Decimal("0.0001"),
    "market_impact": Decimal("0.0002"),
    "symbol": "BTCUSDT",
}


class EventDrivenBacktestEngine:
    def __init__(self, config: dict[str, Any] | None = None, data_root: str | Path = "data/raw", symbol: str = "BTCUSDT") -> None:
        self.config = _merge_config(DEFAULT_CONFIG, config)
        self.symbol = symbol or self.config.get("symbol", "BTCUSDT")
        self.data_root = Path(data_root)
        self.dataset = BacktestDataset(symbol=self.symbol)

    def load_data(self, start_date: str, end_date: str) -> BacktestDataset:
        start_ms, end_ms = _parse_date_window(start_date, end_date)
        trades: list[TradeTick] = []
        liquidations: list[LiquidationTick] = []
        for date_str in _iter_dates(start_date, end_date):
            trade_dir = self.data_root / "trades" / date_str
            if trade_dir.exists():
                for path in sorted(trade_dir.glob("*.jsonl")):
                    trades.extend(self._load_trade_file(path, start_ms, end_ms))
            liq_path = self.data_root / "liquidations" / f"{date_str}.jsonl"
            if liq_path.exists():
                liquidations.extend(self._load_liquidation_file(liq_path, start_ms, end_ms))
        self.dataset = BacktestDataset(trades=tuple(trades), liquidations=tuple(liquidations), symbol=self.symbol)
        logger.info("Loaded dataset: trades=%s liquidations=%s range=%s -> %s", len(self.dataset.trades), len(self.dataset.liquidations), start_date, end_date)
        return self.dataset

    def run_backtest(self, start_date: str | None = None, end_date: str | None = None, dataset: BacktestDataset | None = None) -> dict[str, Any]:
        if dataset is None:
            if start_date and end_date:
                dataset = self.load_data(start_date, end_date)
            else:
                dataset = self.dataset
        if dataset.is_empty():
            initial_capital = _to_decimal(self.config["initial_capital"])
            metrics = build_metrics(initial_capital, initial_capital, [], OrderedDict())
            metrics.update({"impact_count": 0, "classification_count": 0, "circuit_breaker_count": 0})
            return {
                "metrics": metrics,
                "trades": [],
                "trade_objects": [],
                "detected_impacts": [],
                "impact_objects": [],
                "classifications": [],
                "classification_objects": [],
                "daily_pnl": {},
                "daily_pnl_raw": OrderedDict(),
                "circuit_breaker_triggers": [],
                "start_time": 0,
                "end_time": 0,
            }

        impacts = self.detect_impacts(dataset)
        initial_capital = _to_decimal(self.config["initial_capital"])
        balance = initial_capital
        risk_manager = RiskManager(initial_capital)
        trades: list[ExecutedTrade] = []
        classifications: list[ClassificationResult] = []
        circuit_breakers: list[dict[str, Any]] = []
        daily_pnl: OrderedDict[str, Decimal] = OrderedDict()
        daily_loss: dict[str, Decimal] = {}
        paused_until = 0
        open_until = 0
        # 防止熔断器在无新成交时无限重复触发同类原因
        # key=熔断原因, value=触发时的成交笔数；下次只有产生了新成交才能再触发同类
        _cb_fired_at_trade_n: dict[str, int] = {}

        for candidate in impacts:
            impact = candidate.impact
            if impact.detected_at_ms < paused_until or impact.detected_at_ms < open_until:
                continue

            current_day = _utc_day(impact.detected_at_ms)
            risk_manager.daily_loss = daily_loss.get(current_day, Decimal("0"))
            risk_manager.recent_trades = trades[-10:]
            action, reason = risk_manager.check_circuit_breakers()
            if reason:
                # 同类熔断在同一批成交（无新交易）下只触发一次，防止无限循环
                if _cb_fired_at_trade_n.get(reason) == len(trades):
                    # 已经为这批成交触发过同类熔断，暂停时间已设置，直接放行继续分类
                    pass
                else:
                    # 首次（或有新成交后）触发：记录并设置暂停时间
                    _cb_fired_at_trade_n[reason] = len(trades)
                    pause_until = self._pause_until(impact.detected_at_ms, reason)
                    paused_until = max(paused_until, pause_until)
                    circuit_breakers.append({"timestamp": impact.detected_at_ms, "action": action, "reason": reason, "pause_until": pause_until})
                    continue  # 首次触发时跳过此次冲击

            classification = self.classify_impact(dataset, candidate)
            classifications.append(classification)
            signal_time = impact.detected_at_ms + int(self.config["classification_wait_ms"])
            if signal_time >= dataset.end_time or signal_time < paused_until or signal_time < open_until:
                continue

            current_price = self._price_at_or_after(dataset, signal_time)
            if current_price is None:
                continue

            signal = execute_signal(classification, candidate.environment, current_price, balance, leverage=1)
            if signal is None:
                continue
            signal = self._apply_position_multiplier(signal, candidate.environment)
            if signal is None:
                continue

            executed = self._simulate_trade(dataset, signal, classification, signal_time)
            if executed is None:
                continue

            trades.append(executed)
            balance += executed.pnl
            open_until = executed.exit_time

            day_key = _utc_day(executed.exit_time)
            daily_pnl.setdefault(day_key, Decimal("0"))
            daily_pnl[day_key] += executed.pnl
            if executed.pnl < 0:
                daily_loss[day_key] = daily_loss.get(day_key, Decimal("0")) + abs(executed.pnl)
            else:
                daily_loss.setdefault(day_key, Decimal("0"))

            risk_manager.daily_loss = daily_loss[day_key]
            risk_manager.recent_trades = trades[-10:]
            action, reason = risk_manager.check_circuit_breakers()
            if reason:
                pause_until = self._pause_until(executed.exit_time, reason)
                paused_until = max(paused_until, pause_until)
                circuit_breakers.append({"timestamp": executed.exit_time, "action": action, "reason": reason, "pause_until": pause_until})

        metrics = build_metrics(initial_capital, balance, trades, daily_pnl)
        metrics.update({
            "impact_count": len(impacts),
            "classification_count": len(classifications),
            "circuit_breaker_count": len(circuit_breakers),
        })
        return {
            "metrics": metrics,
            "trades": [trade.to_dict() for trade in trades],
            "trade_objects": trades,
            "detected_impacts": [self._impact_to_dict(item) for item in impacts],
            "impact_objects": impacts,
            "classifications": [self._classification_to_dict(item) for item in classifications],
            "classification_objects": classifications,
            "daily_pnl": {day: float(value) for day, value in daily_pnl.items()},
            "daily_pnl_raw": daily_pnl,
            "circuit_breaker_triggers": circuit_breakers,
            "start_time": dataset.start_time,
            "end_time": dataset.end_time,
        }

    def detect_impacts(self, dataset: BacktestDataset) -> list[ImpactCandidate]:
        impacts: list[ImpactCandidate] = []
        liqs = dataset.liquidations
        liq_index = 0
        recent_liqs: deque[LiquidationTick] = deque()
        liq_value_30m = Decimal("0")
        buy_liq_value_30m = Decimal("0")
        sell_liq_value_30m = Decimal("0")
        # ── 60s 清算累积窗口（分钟级触发器） ───────────────────────────────
        recent_liqs_60s: deque[LiquidationTick] = deque()
        liq_buy_60s  = Decimal("0")
        liq_sell_60s = Decimal("0")
        _LIQ_ACCUM_THRESHOLD  = _to_decimal(self.config.get("liq_accum_threshold", "200000"))
        _LIQ_DOMINANCE_MIN    = _to_decimal(self.config.get("liq_dominance_min", "0.70"))
        _LIQ_ACCUM_WINDOW_MS  = 60_000
        # ─────────────────────────────────────────────────────────────────
        recent_30s: deque[TradeTick] = deque()
        recent_15m: deque[TradeTick] = deque()
        recent_1h: deque[TradeTick] = deque()
        recent_6h: deque[TradeTick] = deque()
        volume_30s = Decimal("0")
        volume_1h = Decimal("0")
        volume_6h = Decimal("0")
        sum_price_1h = Decimal("0")
        sum_sq_price_1h = Decimal("0")
        last_impact_at = -1

        for trade in dataset.trades:
            ts = trade.timestamp
            while liq_index < len(liqs) and liqs[liq_index].timestamp <= ts:
                liq = liqs[liq_index]
                liq_index += 1
                # 30min 窗口
                recent_liqs.append(liq)
                liq_value_30m += liq.value
                if liq.side == "BUY":
                    buy_liq_value_30m += liq.value
                elif liq.side == "SELL":
                    sell_liq_value_30m += liq.value
                # 60s 清算累积窗口
                recent_liqs_60s.append(liq)
                if liq.side == "BUY":
                    liq_buy_60s += liq.value
                else:
                    liq_sell_60s += liq.value
            # 30min 窗口过期清理
            while recent_liqs and recent_liqs[0].timestamp < ts - 1_800_000:
                old_liq = recent_liqs.popleft()
                liq_value_30m -= old_liq.value
                if old_liq.side == "BUY":
                    buy_liq_value_30m -= old_liq.value
                elif old_liq.side == "SELL":
                    sell_liq_value_30m -= old_liq.value
            # 60s 清算窗口过期清理
            while recent_liqs_60s and recent_liqs_60s[0].timestamp < ts - _LIQ_ACCUM_WINDOW_MS:
                old = recent_liqs_60s.popleft()
                if old.side == "BUY":
                    liq_buy_60s -= old.value
                else:
                    liq_sell_60s -= old.value

            recent_30s.append(trade)
            volume_30s += trade.quantity
            while recent_30s and recent_30s[0].timestamp < ts - int(self.config["impact_window_ms"]):
                volume_30s -= recent_30s[0].quantity
                recent_30s.popleft()

            recent_15m.append(trade)
            while recent_15m and recent_15m[0].timestamp < ts - 900_000:
                recent_15m.popleft()

            recent_1h.append(trade)
            volume_1h += trade.quantity
            sum_price_1h += trade.price
            sum_sq_price_1h += trade.price * trade.price
            while recent_1h and recent_1h[0].timestamp < ts - 3_600_000:
                old_trade = recent_1h.popleft()
                volume_1h -= old_trade.quantity
                sum_price_1h -= old_trade.price
                sum_sq_price_1h -= old_trade.price * old_trade.price

            recent_6h.append(trade)
            volume_6h += trade.quantity
            while recent_6h and recent_6h[0].timestamp < ts - 21_600_000:
                volume_6h -= recent_6h[0].quantity
                recent_6h.popleft()

            if ts - last_impact_at < int(self.config["impact_cooldown_ms"]) or len(recent_30s) < int(self.config["min_trade_count"]):
                continue

            price_before = recent_30s[0].price
            if price_before <= 0:
                continue
            price_after = trade.price
            price_change_pct = abs(price_after - price_before) / price_before

            if len(recent_1h) > 1:
                mean_price = sum_price_1h / Decimal(len(recent_1h))
                variance = max(Decimal("0"), (sum_sq_price_1h / Decimal(len(recent_1h))) - (mean_price * mean_price))
                volatility = Decimal(str(float(variance) ** 0.5)) / mean_price if mean_price > 0 else Decimal("0")
            else:
                volatility = Decimal("0.002")

            dynamic_threshold = max(_to_decimal(self.config["min_price_change"]), volatility * _to_decimal(self.config["volatility_multiplier"]))
            volume_baseline = (volume_1h / Decimal("120")) if volume_1h > 0 else (volume_30s * Decimal("0.5"))
            volume_surge_ratio = volume_30s / volume_baseline if volume_baseline > 0 else Decimal("0")
            price_surge_triggered = (
                price_change_pct > dynamic_threshold
                and volume_surge_ratio > _to_decimal(self.config["volume_surge_threshold"])
            )

            # ── 分钟级清算累积检测（作为置信度加成，不单独触发）──────────
            # 注：liq_accum 作为独立触发器时（无价格冲击）表现极差（胜率12%），
            # 原因：无价格冲击说明清算被市场流动性吸收，不是趋势信号。
            # 现在改为：仅在 price_surge 同时触发时，将清算数据作为加成传入分类器。
            liq_total_60s  = liq_buy_60s + liq_sell_60s
            liq_dominant   = max(liq_buy_60s, liq_sell_60s)
            liq_accum_triggered = (
                liq_total_60s > 0
                and liq_dominant >= _LIQ_ACCUM_THRESHOLD
                and liq_dominant / liq_total_60s >= _LIQ_DOMINANCE_MIN
            )
            # ───────────────────────────────────────────────────────────────

            # 只有 price_surge 触发才进入后续处理（liq_accum 不再单独触发）
            if not price_surge_triggered:
                continue

            momentum_15m = Decimal("0")
            if recent_15m and recent_15m[0].price > 0:
                momentum_15m = (price_after - recent_15m[0].price) / recent_15m[0].price

            # 方向：由价格冲击方向决定
            direction = "up" if price_after > price_before else "down"

            impact = ImpactEvent(
                detected_at_ms=ts,
                direction=direction,
                price_before=price_before,
                price_after=price_after,
                price_change_pct=price_change_pct,
                volume_30s=volume_30s,
                volume_baseline=volume_baseline,
                volume_surge_ratio=volume_surge_ratio,
            )
            environment = self._build_environment(
                timestamp_ms=ts,
                hour_volume=volume_1h,
                six_hour_volume=volume_6h,
                momentum_15m=momentum_15m,
                liq_value_30m=liq_value_30m,
                buy_liq_value_30m=buy_liq_value_30m,
                sell_liq_value_30m=sell_liq_value_30m,
                six_hour_start_ts=recent_6h[0].timestamp if recent_6h else ts,
            )
            # 当 price_surge + liq_accum 同时触发时，传入清算加成数据供分类器使用
            if liq_accum_triggered:
                dominant_side = "BUY" if liq_buy_60s > liq_sell_60s else "SELL"
                pre_count = sum(1 for l in recent_liqs_60s if l.side == dominant_side)
                impacts.append(ImpactCandidate(
                    impact=impact, environment=environment,
                    trigger_type="price_surge",
                    pre_liq_value=liq_dominant,
                    pre_liq_count=pre_count,
                ))
            else:
                impacts.append(ImpactCandidate(impact=impact, environment=environment))
            last_impact_at = ts

        return impacts

    def classify_impact(self, dataset: BacktestDataset, candidate: ImpactCandidate) -> ClassificationResult:
        impact = candidate.impact
        end_ms = impact.detected_at_ms + int(self.config["classification_wait_ms"])
        trades_window = self._trade_window(dataset, impact.detected_at_ms, end_ms)
        liq_window = self._liquidation_window(dataset, impact.detected_at_ms, end_ms)
        relevant_liqs = [
            liq for liq in liq_window
            if (impact.direction == "down" and liq.side == "SELL") or (impact.direction == "up" and liq.side == "BUY")
        ]
        liq_count = len(relevant_liqs)
        liq_value = sum((liq.value for liq in relevant_liqs), Decimal("0"))
        total_trade_value = sum((trade.notional for trade in trades_window), Decimal("0"))
        liq_ratio = liq_value / total_trade_value if total_trade_value > 0 else Decimal("0")
        cvd = sum(((-trade.quantity if trade.is_buyer_maker else trade.quantity) for trade in trades_window), Decimal("0"))
        cvd_follows = (cvd > 0 and impact.direction == "up") or (cvd < 0 and impact.direction == "down")

        liq_value_breakout_min = _to_decimal(self.config.get("liq_value_breakout_min", "200000"))

        # ── 价格冲击触发路径 ──────────────────────────────────────────────────
        # 当 price_surge + liq_accum 同时触发时，pre_liq_value > 0，用于加成置信度
        # 合并 pre-60s 清算数据与 post-45s 清算数据，取最大值（两个窗口可能有重叠）
        if candidate.pre_liq_value > 0:
            effective_liq_value = max(liq_value, candidate.pre_liq_value)
            effective_liq_count = max(liq_count, candidate.pre_liq_count)
        else:
            effective_liq_value = liq_value
            effective_liq_count = liq_count

        if (effective_liq_count >= int(self.config["liq_count_threshold"])
                and liq_ratio > _to_decimal(self.config["liq_ratio_threshold"])
                and effective_liq_value >= _to_decimal(self.config["liq_value_min"])):
            # confidence 按超出阈值的倍数计算：需超出阈值2倍才达到1.0
            threshold = _to_decimal(self.config["liq_ratio_threshold"])
            base_conf = min(liq_ratio / (threshold * Decimal("2")), Decimal("1"))
            # pre_liq_value 加成：大额清算在冲击前已积累，进一步提升置信度（最多+0.2）
            if candidate.pre_liq_value >= liq_value_breakout_min:
                bonus = min((candidate.pre_liq_value - liq_value_breakout_min) / Decimal("1800000") * Decimal("0.2"), Decimal("0.2"))
            else:
                bonus = Decimal("0")
            confidence = min(base_conf + bonus, Decimal("1"))
            classification, strategy = "真突破", "趋势跟随"
        elif effective_liq_count <= int(self.config["liq_count_low"]) and liq_ratio < _to_decimal(self.config["liq_ratio_low"]):
            classification, strategy, confidence = "过度反应", "均值回归", Decimal("0.6")
        else:
            classification, strategy, confidence = "不确定", "放弃", Decimal("0")

        return ClassificationResult(
            impact=impact,
            classification=classification,
            strategy=strategy,
            confidence=confidence,
            liq_count=effective_liq_count,
            liq_value=effective_liq_value,
            liq_ratio=liq_ratio,
            cvd_follows=cvd_follows,
            wait_seconds=int(self.config["classification_wait_ms"]) // 1000,
        )

    def _build_environment(
        self,
        timestamp_ms: int,
        hour_volume: Decimal,
        six_hour_volume: Decimal,
        momentum_15m: Decimal,
        liq_value_30m: Decimal,
        buy_liq_value_30m: Decimal,
        sell_liq_value_30m: Decimal,
        six_hour_start_ts: int,
    ) -> EnvironmentResult:
        hours_covered = max(Decimal("1"), Decimal(str((timestamp_ms - six_hour_start_ts) / 3_600_000)))
        baseline_hour_volume = six_hour_volume / hours_covered if six_hour_volume > 0 else hour_volume
        volume_ratio = hour_volume / baseline_hour_volume if baseline_hour_volume > 0 else Decimal("1")
        if momentum_15m > Decimal("0.003"):
            direction_bias = "\u504f\u591a"
        elif momentum_15m < Decimal("-0.003"):
            direction_bias = "\u504f\u7a7a"
        else:
            direction_bias = "\u4e2d\u6027"

        if sell_liq_value_30m > buy_liq_value_30m * Decimal("1.25"):
            liquidation_side = "\u591a\u5934"
        elif buy_liq_value_30m > sell_liq_value_30m * Decimal("1.25"):
            liquidation_side = "\u7a7a\u5934"
        else:
            liquidation_side = None

        if liq_value_30m > Decimal("1000000"):
            market_stress = "\u9ad8"
        elif liq_value_30m < Decimal("100000"):
            market_stress = "\u4f4e"
        else:
            market_stress = "\u6b63\u5e38"

        if abs(momentum_15m) > Decimal("0.01") or volume_ratio > Decimal("1.5") or liq_value_30m > Decimal("1000000"):
            oi_change_pct, activity_level = Decimal("0.04"), "\u9ad8"
        elif volume_ratio < Decimal("0.5") and liq_value_30m < Decimal("100000"):
            oi_change_pct, activity_level = Decimal("0.005"), "\u4f4e"
        else:
            oi_change_pct, activity_level = Decimal("0.015"), "\u6b63\u5e38"

        if volume_ratio < Decimal("0.3"):
            status, suitability, reason = "\u4f11\u7720", Decimal("0"), "\u6d41\u52a8\u6027\u771f\u7a7a"
            dormant_trigger = {"\u6210\u4ea4\u91cf\u6bd4\u7387": f"{float(volume_ratio):.2f}x < 0.30x"}
        elif liq_value_30m < Decimal("100000") and abs(momentum_15m) < Decimal("0.002") and volume_ratio < Decimal("0.8"):
            status, suitability, reason = "\u4f11\u7720", Decimal("0"), "\u5e02\u573a\u6c89\u5bc2"
            dormant_trigger = {"\u6e05\u7b97\u91cf30m": f"${float(liq_value_30m):,.0f} < $100,000", "15m\u52a8\u91cf": f"{float(momentum_15m):.3%} < 0.2%"}
        else:
            status = "\u53ef\u4ea4\u6613"
            suitability = Decimal("0.9") if market_stress == "\u9ad8" and activity_level == "\u9ad8" else Decimal("0.7")
            if direction_bias == "\u4e2d\u6027":
                suitability *= Decimal("0.8")
            reason = "\u9ad8\u6ce2\u52a8\u73af\u5883" if market_stress == "\u9ad8" and activity_level == "\u9ad8" else "\u6b63\u5e38\u73af\u5883"
            dormant_trigger = None

        stop_multiplier = self._calculate_stop_multiplier(market_stress, activity_level, volume_ratio)
        adjustments: dict[str, Any] = {"stop_multiplier": float(stop_multiplier), "activity_level": activity_level, "market_stress": market_stress}
        if volume_ratio < Decimal("0.5"):
            adjustments["position_multiplier"] = 0.5

        context = BacktestTimeContext(timestamp=timestamp_ms, utc_hour=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).hour, hours_to_funding=self._hours_to_next_funding(timestamp_ms))
        return EnvironmentResult(
            status=status,
            direction_bias=direction_bias,
            liquidation_side=liquidation_side,
            suitability=suitability,
            time_context=context,
            adjustments=adjustments,
            reason=reason,
            fr_zscore=None,
            oi_change_pct=oi_change_pct,
            liq_volume=liq_value_30m,
            volume_ratio=volume_ratio,
            dormant_trigger=dormant_trigger,
        )

    def _calculate_stop_multiplier(self, market_stress: str, activity_level: str, volume_ratio: Decimal) -> Decimal:
        base = Decimal("1.0")
        if market_stress == "\u9ad8":
            base += Decimal("0.3")
        elif market_stress == "\u4f4e":
            base -= Decimal("0.2")
        if activity_level == "\u9ad8":
            base += Decimal("0.2")
        elif activity_level == "\u4f4e":
            base -= Decimal("0.1")
        if volume_ratio > Decimal("1.5"):
            base += Decimal("0.1")
        elif volume_ratio < Decimal("0.7"):
            base -= Decimal("0.1")
        return max(Decimal("0.8"), min(Decimal("1.3"), base))

    def _simulate_trade(self, dataset: BacktestDataset, signal: TradeSignal, classification: ClassificationResult, signal_time: int) -> ExecutedTrade | None:
        start_index = bisect_left(dataset.trade_timestamps, signal_time)
        if start_index >= len(dataset.trades):
            return None
        entry = self._find_entry(dataset, signal, start_index, signal_time)
        if entry is None:
            return None

        entry_index, entry_tick, entry_reference, entry_liquidity = entry
        entry_price = self._apply_execution_price(entry_reference, signal.side, entry_liquidity)
        entry_fee = entry_price * signal.quantity * self._fee_rate(entry_liquidity)
        entry_slippage = abs(entry_price - entry_reference) * signal.quantity

        exit_result = self._find_exit(dataset, signal, entry_index + 1, entry_price, entry_tick.timestamp)
        if exit_result is None:
            return None

        _, exit_tick, exit_reference, exit_reason, exit_liquidity = exit_result
        exit_action = "SELL" if signal.side == "BUY" else "BUY"
        exit_price = self._apply_execution_price(exit_reference, exit_action, exit_liquidity)
        exit_fee = exit_price * signal.quantity * self._fee_rate(exit_liquidity)
        exit_slippage = abs(exit_price - exit_reference) * signal.quantity

        if signal.side == "BUY":
            gross_pnl = (exit_price - entry_price) * signal.quantity
        else:
            gross_pnl = (entry_price - exit_price) * signal.quantity
        pnl = gross_pnl - entry_fee - exit_fee

        risk_distance = abs(entry_price - signal.stop_loss)
        reward_distance = abs(signal.take_profit - entry_price)
        planned_rr = reward_distance / risk_distance if risk_distance > 0 else Decimal("0")

        return ExecutedTrade(
            strategy=classification.strategy,
            grade=signal.grade,
            side=signal.side,
            entry_type=signal.entry_type,
            entry_time=entry_tick.timestamp,
            exit_time=exit_tick.timestamp,
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=signal.quantity,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            planned_rr=planned_rr,
            confidence=classification.confidence,
            impact_time=classification.impact.detected_at_ms,
            signal_time=signal_time,
            entry_reference=entry_reference,
            exit_reference=exit_reference,
            entry_fee=entry_fee,
            exit_fee=exit_fee,
            slippage_cost=entry_slippage + exit_slippage,
            gross_pnl=gross_pnl,
            pnl=pnl,
            exit_reason=exit_reason,
        )

    def _find_entry(self, dataset: BacktestDataset, signal: TradeSignal, start_index: int, signal_time: int) -> tuple[int, TradeTick, Decimal, str] | None:
        if signal.entry_type == "MARKET":
            tick = dataset.trades[start_index]
            return start_index, tick, tick.price, "taker"

        expiry_ms = (signal.entry_expiry or 0) * 1000
        deadline = signal_time + expiry_ms if expiry_ms else dataset.end_time
        for index in range(start_index, len(dataset.trades)):
            tick = dataset.trades[index]
            if tick.timestamp > deadline:
                return None
            if signal.side == "BUY" and tick.price <= signal.entry_price:
                return index, tick, signal.entry_price, "maker"
            if signal.side == "SELL" and tick.price >= signal.entry_price:
                return index, tick, signal.entry_price, "maker"
        return None

    def _find_exit(self, dataset: BacktestDataset, signal: TradeSignal, start_index: int, entry_price: Decimal, entry_time: int) -> tuple[int, TradeTick, Decimal, str, str] | None:
        if start_index >= len(dataset.trades):
            return None
        deadline = entry_time + signal.time_stop * 1000
        initial_risk = abs(entry_price - signal.stop_loss)
        dynamic_stop = signal.stop_loss
        best_price = entry_price
        last_tick = dataset.trades[start_index - 1]

        for index in range(start_index, len(dataset.trades)):
            tick = dataset.trades[index]
            last_tick = tick
            if tick.timestamp > deadline:
                return index, tick, tick.price, "time_stop", "taker"
            if signal.side == "BUY":
                if signal.trailing_stop:
                    best_price = max(best_price, tick.price)
                    if initial_risk > 0 and best_price - entry_price >= initial_risk:
                        dynamic_stop = max(dynamic_stop, best_price - initial_risk)
                if tick.price <= dynamic_stop:
                    return index, tick, tick.price, "stop_loss", "taker"
                if tick.price >= signal.take_profit:
                    return index, tick, signal.take_profit, "take_profit", "maker"
            else:
                if signal.trailing_stop:
                    best_price = min(best_price, tick.price)
                    if initial_risk > 0 and entry_price - best_price >= initial_risk:
                        dynamic_stop = min(dynamic_stop, best_price + initial_risk)
                if tick.price >= dynamic_stop:
                    return index, tick, tick.price, "stop_loss", "taker"
                if tick.price <= signal.take_profit:
                    return index, tick, signal.take_profit, "take_profit", "maker"

        return len(dataset.trades) - 1, last_tick, last_tick.price, "end_of_data", "taker"

    def _apply_position_multiplier(self, signal: TradeSignal, environment: EnvironmentResult) -> TradeSignal | None:
        multiplier = _to_decimal(environment.adjustments.get("position_multiplier", 1))
        quantity = signal.quantity * multiplier
        if quantity < Decimal("0.001"):
            return None
        return replace(signal, quantity=quantity)

    def _price_at_or_after(self, dataset: BacktestDataset, timestamp_ms: int) -> Decimal | None:
        index = bisect_left(dataset.trade_timestamps, timestamp_ms)
        if index >= len(dataset.trades):
            return None
        return dataset.trades[index].price

    def _trade_window(self, dataset: BacktestDataset, start_ms: int, end_ms: int) -> Sequence[TradeTick]:
        left = bisect_left(dataset.trade_timestamps, start_ms)
        right = bisect_right(dataset.trade_timestamps, end_ms)
        return dataset.trades[left:right]

    def _liquidation_window(self, dataset: BacktestDataset, start_ms: int, end_ms: int) -> Sequence[LiquidationTick]:
        left = bisect_left(dataset.liquidation_timestamps, start_ms)
        right = bisect_right(dataset.liquidation_timestamps, end_ms)
        return dataset.liquidations[left:right]

    def _load_trade_file(self, path: Path, start_ms: int, end_ms: int) -> list[TradeTick]:
        results: list[TradeTick] = []
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                trade = self._parse_trade_record(record)
                if trade is not None and start_ms <= trade.timestamp <= end_ms:
                    results.append(trade)
        return results

    def _load_liquidation_file(self, path: Path, start_ms: int, end_ms: int) -> list[LiquidationTick]:
        results: list[LiquidationTick] = []
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                liquidation = self._parse_liquidation_record(record)
                if liquidation is not None and start_ms <= liquidation.timestamp <= end_ms:
                    results.append(liquidation)
        return results

    def _parse_trade_record(self, record: dict[str, Any]) -> TradeTick | None:
        if "data" in record:
            data = record.get("data", {})
            timestamp = record.get("server_ts") or data.get("T") or data.get("E") or data.get("local_ts")
        else:
            data = record
            timestamp = record.get("T") or record.get("E") or record.get("local_ts")
        symbol = data.get("s")
        if symbol != self.symbol or timestamp is None or data.get("p") is None or data.get("q") is None:
            return None
        return TradeTick(
            timestamp=int(timestamp),
            price=_to_decimal(data.get("p")),
            quantity=_to_decimal(data.get("q")),
            is_buyer_maker=bool(data.get("m", False)),
            symbol=symbol,
        )

    def _parse_liquidation_record(self, record: dict[str, Any]) -> LiquidationTick | None:
        if "data" in record:
            payload = record.get("data", {})
            order = payload.get("o", {})
            symbol = payload.get("s") or order.get("s")
            timestamp = record.get("server_ts") or order.get("T") or payload.get("E")
        else:
            payload = record
            order = payload.get("o", {})
            symbol = payload.get("s") or order.get("s")
            timestamp = order.get("T") or payload.get("E") or payload.get("local_ts")
        if symbol != self.symbol or timestamp is None or order.get("p") is None or order.get("q") is None:
            return None
        return LiquidationTick(
            timestamp=int(timestamp),
            symbol=symbol,
            side=str(order.get("S", "")),
            price=_to_decimal(order.get("p")),
            quantity=_to_decimal(order.get("q")),
        )

    def _apply_execution_price(self, reference_price: Decimal, action_side: str, liquidity: str) -> Decimal:
        if liquidity == "maker":
            slippage = _to_decimal(self.config["maker_slippage"])
        else:
            slippage = _to_decimal(self.config["taker_slippage"]) + _to_decimal(self.config["market_impact"])
        if action_side == "BUY":
            return reference_price * (Decimal("1") + slippage)
        return reference_price * (Decimal("1") - slippage)

    def _fee_rate(self, liquidity: str) -> Decimal:
        return _to_decimal(self.config["maker_fee"] if liquidity == "maker" else self.config["taker_fee"])

    def _hours_to_next_funding(self, timestamp_ms: int) -> int:
        dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
        hours = 8 - (dt.hour % 8)
        return 8 if hours == 0 else hours

    def _pause_until(self, timestamp_ms: int, reason: str) -> int:
        if reason == "daily_loss_limit":
            dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
            next_day = datetime(dt.year, dt.month, dt.day, tzinfo=UTC) + timedelta(days=1)
            return int(next_day.timestamp() * 1000)
        if reason == "large_single_loss":
            return timestamp_ms + 30 * 60 * 1000
        if reason == "consecutive_losses":
            # 连续亏损后当天不再交易（而非仅暂停30分钟），避免在不利行情中持续下单
            dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
            next_day = datetime(dt.year, dt.month, dt.day, tzinfo=UTC) + timedelta(days=1)
            return int(next_day.timestamp() * 1000)
        if reason == "low_win_rate":
            return timestamp_ms + 60 * 60 * 1000
        return timestamp_ms

    def _impact_to_dict(self, item: ImpactCandidate) -> dict[str, Any]:
        return {
            "detected_at_ms": item.impact.detected_at_ms,
            "direction": item.impact.direction,
            "price_before": float(item.impact.price_before),
            "price_after": float(item.impact.price_after),
            "price_change_pct": float(item.impact.price_change_pct),
            "volume_30s": float(item.impact.volume_30s),
            "volume_baseline": float(item.impact.volume_baseline),
            "volume_surge_ratio": float(item.impact.volume_surge_ratio),
            "environment": {
                "status": item.environment.status,
                "direction_bias": item.environment.direction_bias,
                "liquidation_side": item.environment.liquidation_side,
                "suitability": float(item.environment.suitability),
                "reason": item.environment.reason,
                "oi_change_pct": float(item.environment.oi_change_pct),
                "liq_volume": float(item.environment.liq_volume),
                "volume_ratio": float(item.environment.volume_ratio),
                "adjustments": item.environment.adjustments,
            },
        }

    def _classification_to_dict(self, item: ClassificationResult) -> dict[str, Any]:
        return {
            "impact_time": item.impact.detected_at_ms,
            "classification": item.classification,
            "strategy": item.strategy,
            "confidence": float(item.confidence),
            "liq_count": item.liq_count,
            "liq_value": float(item.liq_value),
            "liq_ratio": float(item.liq_ratio),
            "cvd_follows": item.cvd_follows,
            "wait_seconds": item.wait_seconds,
        }


def _build_cli_summary(results: dict[str, Any]) -> str:
    metrics = results["metrics"]
    lines = [
        f"Trades: {metrics['total_trades']}",
        f"Total PnL: {metrics['total_pnl']:.2f}",
        f"Win Rate: {metrics['win_rate']:.2%}",
        f"Expectancy: {metrics['expectancy']:.4f}",
        f"Sharpe: {metrics['sharpe']:.2f}",
        f"Max Drawdown: {metrics['max_drawdown']:.2%}",
        f"Impacts: {metrics['impact_count']}",
        f"Classifications: {metrics['classification_count']}",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the three-layer event-driven backtest.")
    parser.add_argument("--start", required=True, help="Start date in YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date in YYYY-MM-DD")
    parser.add_argument("--data-root", default="data/raw", help="Raw data root directory")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading symbol")
    parser.add_argument("--capital", default="10000", help="Initial capital")
    parser.add_argument("--json", action="store_true", help="Print raw JSON output")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    engine = EventDrivenBacktestEngine(
        config={"initial_capital": Decimal(str(args.capital))},
        data_root=args.data_root,
        symbol=args.symbol,
    )
    results = engine.run_backtest(start_date=args.start, end_date=args.end)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    else:
        print(_build_cli_summary(results))


if __name__ == "__main__":
    main()
