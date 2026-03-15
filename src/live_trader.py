# -*- coding: utf-8 -*-
"""
BTCUSDT 实盘交易主循环（Testnet）

数据流：
  WebSocket aggTrade + forceOrder → 滚动缓冲区
  → 冲击检测 → 等待45s → 分类 → 执行 → 持仓监控

运行：
  python -m src.live_trader
"""

import asyncio
import io
import json
import logging
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import websockets

from src.backtest.engine import (
    DEFAULT_CONFIG,
    _to_decimal,
    EventDrivenBacktestEngine,
    ImpactCandidate,
    LiquidationTick,
    TradeTick,
)
from src.broker.live_broker import BinanceError, LiveBroker
from src.layers.classifier import ClassificationResult, ImpactEvent
from src.layers.environment import EnvironmentResult
from src.layers.executor import TradeSignal, execute_signal
from src.risk.circuit_breaker import RiskManager

logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
WS_BASE = "wss://stream.binancefuture.com/stream"   # Testnet WebSocket
STREAMS = f"btcusdt@aggTrade/!forceOrder@arr"


# ── 加载配置 ──────────────────────────────────────────────────────────

def _load_env() -> dict:
    """从 .env 文件读取配置"""
    env: dict = {}
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    # 环境变量覆盖
    for key in ("BINANCE_API_KEY", "BINANCE_SECRET_KEY", "TESTNET"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


# ── 核心交易器 ────────────────────────────────────────────────────────

class LiveTrader:
    """实盘交易主体：数据 → 信号 → 执行 → 持仓管理"""

    def __init__(self, broker: LiveBroker, config: dict | None = None, leverage: int = 2):
        self.broker = broker
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}
        self.symbol = SYMBOL
        self.leverage = leverage

        # 滚动缓冲区（与回测引擎保持一致）
        self.trades_30s: deque[TradeTick] = deque()
        self.trades_15m: deque[TradeTick] = deque()
        self.trades_1h: deque[TradeTick] = deque()
        self.trades_6h: deque[TradeTick] = deque()
        self.liqs_30m: deque[LiquidationTick] = deque()

        self.vol_30s = Decimal("0")
        self.vol_1h = Decimal("0")
        self.vol_6h = Decimal("0")
        self.liq_val_30m = Decimal("0")
        self.buy_liq_val_30m = Decimal("0")
        self.sell_liq_val_30m = Decimal("0")
        self.sum_price_1h = Decimal("0")
        self.sum_sq_1h = Decimal("0")

        self.last_impact_ms: int = -1
        self.open_until_ms: int = 0      # 当前持仓结束前不开新仓
        self.paused_until_ms: int = 0    # 熔断暂停

        self.balance = Decimal("10000")  # 将在启动时从 API 刷新
        self.risk_manager = RiskManager(self.balance)
        self._engine = EventDrivenBacktestEngine(self.cfg)   # 复用分类逻辑

        self._running = False
        self._current_position: Optional[dict] = None        # 当前持仓信息
        self._next_mode_health_check_ms: int = 0

        # PnL 统计
        self.total_pnl = Decimal("0")
        self.total_trades = 0
        self.win_trades = 0

    # ── WebSocket 入口 ────────────────────────────────────────────────

    async def run(self):
        """启动 WebSocket 监听循环"""
        self._running = True

        # 启动清理：平掉幽灵仓位 → 切单向模式
        self._startup_cleanup()

        self.balance = self.broker.get_balance()
        self.risk_manager = RiskManager(self.balance)
        logger.info("实盘启动 余额=%.2f USDT  可用=%.2f  testnet=%s",
                    self.balance, float(self.broker.get_available_balance()), self.broker.testnet)

        url = f"{WS_BASE}?streams={STREAMS}"
        reconnect_delay_sec = 3
        dns_failures = 0
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    logger.info("WebSocket 已连接: %s", url)
                    reconnect_delay_sec = 3
                    dns_failures = 0
                    async for raw in ws:
                        await self._on_message(raw)
            except Exception as exc:
                err_msg = str(exc).lower()
                is_dns_error = "getaddrinfo" in err_msg or "name resolution" in err_msg
                if is_dns_error:
                    dns_failures += 1
                else:
                    dns_failures = 0

                if dns_failures >= 10:
                    logger.error("WebSocket DNS 连续失败%d次，暂停300秒后重连...", dns_failures)
                    await asyncio.sleep(300)
                    reconnect_delay_sec = 3
                    dns_failures = 0
                    continue

                logger.warning(
                    "WebSocket 断线: %s，%ds 后重连... (dns_failures=%d)",
                    exc, reconnect_delay_sec, dns_failures,
                )
                await asyncio.sleep(reconnect_delay_sec)
                reconnect_delay_sec = min(reconnect_delay_sec * 2, 60)

    # ── 启动清理 ──────────────────────────────────────────────────────

    def _startup_cleanup(self):
        """
        启动前自动清理：
          1. 设置杠杆倍数
          2. 若账户处于 HEDGE 模式且有幽灵仓位 → 全部市价平仓
          3. 切换到单向持仓模式
        测试网每次重启会自动生成对冲仓位，不清理则可用保证金几乎为零。
        """
        logger.info("启动清理：检查持仓模式与幽灵仓位...")

        # 1. 设置杠杆（在切换模式之前）
        try:
            self.broker.set_leverage(self.symbol, self.leverage)
        except BinanceError as exc:
            logger.warning("设置杠杆失败 code=%d: %s", exc.code, exc.msg)

        # 2. 确保单向持仓模式（先尝试直接切换）
        try:
            self.broker.ensure_one_way_mode()
            logger.info("持仓模式确认：单向模式，无幽灵仓位")
            return
        except BinanceError as exc:
            if exc.code != -4068:   # -4068 = 有仓位，不能切换；其他错误直接抛出
                raise
            logger.warning("检测到现有仓位（HEDGE 模式），开始清理幽灵仓位...")

        # 有仓位：先全部市价平仓
        self._close_all_hedge_positions()

        # 再切换模式
        try:
            self.broker.ensure_one_way_mode()
            logger.info("启动清理完成：已切换为单向持仓模式")
        except BinanceError as exc:
            logger.error("切换单向模式失败 code=%d msg=%s，继续运行（将依赖软件监控）", exc.code, exc.msg)

    def _close_all_hedge_positions(self):
        """
        在 HEDGE 模式下平掉所有品种的所有仓位。
        HEDGE 模式下下单必须带 positionSide=LONG/SHORT。
        """
        try:
            all_positions = self.broker._get("/fapi/v2/positionRisk")
        except Exception as exc:
            logger.error("查询所有仓位失败: %s", exc)
            return

        closed, skipped = 0, 0
        for pos in all_positions:
            amt = Decimal(str(pos.get("positionAmt", "0")))
            if amt == 0:
                continue

            sym      = pos.get("symbol", "")
            pos_side = pos.get("positionSide", "BOTH")   # "LONG" or "SHORT"
            side     = "SELL" if amt > 0 else "BUY"      # 平仓方向
            qty      = abs(amt)

            # 先撤掉该品种所有挂单，避免冲突
            try:
                self.broker.cancel_all_orders(sym)
            except Exception:
                pass

            try:
                params = {
                    "symbol":       sym,
                    "side":         side,
                    "type":         "MARKET",
                    "quantity":     f"{float(qty):.3f}",
                    "positionSide": pos_side,
                }
                result = self.broker._post("/fapi/v1/order", params)
                logger.info(
                    "幽灵仓位已清理: %s %s %s %s  orderId=%s",
                    sym, pos_side, side, qty, result.get("orderId"),
                )
                closed += 1
            except BinanceError as exc:
                logger.error("清理 %s %s 失败 code=%d: %s", sym, pos_side, exc.code, exc.msg)
                skipped += 1
            except Exception as exc:
                logger.error("清理 %s %s 异常: %s", sym, pos_side, exc)
                skipped += 1

        logger.info("幽灵仓位清理完成: %d 个已平仓, %d 个失败", closed, skipped)

    async def _on_message(self, raw: str):
        try:
            outer = json.loads(raw)
        except Exception:
            return
        stream = outer.get("stream", "")
        data = outer.get("data", outer)

        if "aggTrade" in stream:
            await self._on_trade(data)
        elif "forceOrder" in stream:
            self._on_liquidation(data)

    # ── 数据处理 ──────────────────────────────────────────────────────

    async def _on_trade(self, data: dict):
        """处理 aggTrade 消息，推进冲击检测"""
        sym = data.get("s", "")
        if sym != self.symbol:
            return

        ts = int(data.get("T", 0))
        price = _to_decimal(data.get("p"))
        qty = _to_decimal(data.get("q"))
        is_buyer_maker = bool(data.get("m", False))
        tick = TradeTick(timestamp=ts, price=price, quantity=qty,
                         is_buyer_maker=is_buyer_maker, symbol=sym)

        self._ensure_mode_health(ts)
        self._update_trade_buffers(tick)
        await self._try_detect_impact(tick)

    def _on_liquidation(self, data: dict):
        """处理 forceOrder 消息，更新清算缓冲区"""
        order = data.get("o", {})
        sym = order.get("s", "") or data.get("s", "")
        if sym != self.symbol:
            return
        ts = int(order.get("T", data.get("E", 0)))
        price = _to_decimal(order.get("p", "0"))
        qty = _to_decimal(order.get("q", "0"))
        side = str(order.get("S", ""))
        liq = LiquidationTick(timestamp=ts, symbol=sym, side=side, price=price, quantity=qty)
        self._update_liq_buffers(liq)

    # ── 缓冲区维护 ────────────────────────────────────────────────────

    def _update_trade_buffers(self, tick: TradeTick):
        ts = tick.timestamp

        self.trades_30s.append(tick)
        self.vol_30s += tick.quantity
        while self.trades_30s and self.trades_30s[0].timestamp < ts - int(self.cfg["impact_window_ms"]):
            self.vol_30s -= self.trades_30s[0].quantity
            self.trades_30s.popleft()

        self.trades_15m.append(tick)
        while self.trades_15m and self.trades_15m[0].timestamp < ts - 900_000:
            self.trades_15m.popleft()

        self.trades_1h.append(tick)
        self.vol_1h += tick.quantity
        self.sum_price_1h += tick.price
        self.sum_sq_1h += tick.price * tick.price
        while self.trades_1h and self.trades_1h[0].timestamp < ts - 3_600_000:
            old = self.trades_1h.popleft()
            self.vol_1h -= old.quantity
            self.sum_price_1h -= old.price
            self.sum_sq_1h -= old.price * old.price

        self.trades_6h.append(tick)
        self.vol_6h += tick.quantity
        while self.trades_6h and self.trades_6h[0].timestamp < ts - 21_600_000:
            self.vol_6h -= self.trades_6h[0].quantity
            self.trades_6h.popleft()

    def _update_liq_buffers(self, liq: LiquidationTick):
        ts = liq.timestamp
        self.liqs_30m.append(liq)
        self.liq_val_30m += liq.value
        if liq.side == "BUY":
            self.buy_liq_val_30m += liq.value
        elif liq.side == "SELL":
            self.sell_liq_val_30m += liq.value
        while self.liqs_30m and self.liqs_30m[0].timestamp < ts - 1_800_000:
            old = self.liqs_30m.popleft()
            self.liq_val_30m -= old.value
            if old.side == "BUY":
                self.buy_liq_val_30m -= old.value
            elif old.side == "SELL":
                self.sell_liq_val_30m -= old.value

    # ── 冲击检测 ──────────────────────────────────────────────────────

    async def _try_detect_impact(self, tick: TradeTick):
        ts = tick.timestamp
        cooldown = int(self.cfg["impact_cooldown_ms"])
        min_trades = int(self.cfg["min_trade_count"])

        if ts - self.last_impact_ms < cooldown:
            return
        if len(self.trades_30s) < min_trades:
            return
        if ts < self.paused_until_ms or ts < self.open_until_ms:
            return

        price_before = self.trades_30s[0].price
        if price_before <= 0:
            return
        price_after = tick.price
        price_change_pct = abs(price_after - price_before) / price_before

        # 动态阈值
        if len(self.trades_1h) > 1:
            mean_p = self.sum_price_1h / Decimal(len(self.trades_1h))
            var = max(Decimal("0"),
                      (self.sum_sq_1h / Decimal(len(self.trades_1h))) - mean_p * mean_p)
            volatility = Decimal(str(float(var) ** 0.5)) / mean_p if mean_p > 0 else Decimal("0.002")
        else:
            volatility = Decimal("0.002")

        threshold = max(_to_decimal(self.cfg["min_price_change"]),
                        volatility * _to_decimal(self.cfg["volatility_multiplier"]))
        baseline = (self.vol_1h / Decimal("120")) if self.vol_1h > 0 else (self.vol_30s * Decimal("0.5"))
        surge = self.vol_30s / baseline if baseline > 0 else Decimal("0")

        if price_change_pct <= threshold or surge <= _to_decimal(self.cfg["volume_surge_threshold"]):
            return

        # 冲击确认
        momentum_15m = Decimal("0")
        if self.trades_15m and self.trades_15m[0].price > 0:
            momentum_15m = (price_after - self.trades_15m[0].price) / self.trades_15m[0].price

        impact = ImpactEvent(
            detected_at_ms=ts,
            direction="up" if price_after > price_before else "down",
            price_before=price_before,
            price_after=price_after,
            price_change_pct=price_change_pct,
            volume_30s=self.vol_30s,
            volume_baseline=baseline,
            volume_surge_ratio=surge,
        )
        env = self._build_env(ts, momentum_15m)
        self.last_impact_ms = ts

        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%H:%M:%S")
        logger.info("冲击检测 %s  方向=%s  变幅=%.3f%%  清算=$%.0f",
                    dt, impact.direction, float(price_change_pct) * 100, float(self.liq_val_30m))

        # 异步等待分类窗口，不阻塞数据接收
        asyncio.create_task(self._classify_and_execute(impact, env))

    # ── 分类与执行 ────────────────────────────────────────────────────

    async def _classify_and_execute(self, impact: ImpactEvent, env: EnvironmentResult):
        wait_ms = int(self.cfg["classification_wait_ms"])
        await asyncio.sleep(wait_ms / 1000)

        # 熔断/已开仓检查
        now_ms = int(time.time() * 1000)
        if now_ms < self.paused_until_ms:
            logger.info("熔断暂停中，跳过分类")
            return
        if now_ms < self.open_until_ms:
            logger.info("持仓中，跳过新信号")
            return

        # 从30m内的清算数据中截取冲击后的窗口
        window_liqs = [
            liq for liq in self.liqs_30m
            if impact.detected_at_ms <= liq.timestamp <= impact.detected_at_ms + wait_ms
        ]
        relevant = [
            liq for liq in window_liqs
            if (impact.direction == "down" and liq.side == "SELL")
            or (impact.direction == "up" and liq.side == "BUY")
        ]
        liq_count = len(relevant)
        liq_value = sum((liq.value for liq in relevant), Decimal("0"))

        window_trades = [
            t for t in self.trades_1h
            if impact.detected_at_ms <= t.timestamp <= impact.detected_at_ms + wait_ms
        ]
        total_val = sum((t.notional for t in window_trades), Decimal("0"))
        liq_ratio = liq_value / total_val if total_val > 0 else Decimal("0")

        cvd = sum(
            ((-t.quantity if t.is_buyer_maker else t.quantity) for t in window_trades),
            Decimal("0"),
        )
        cvd_follows = (cvd > 0 and impact.direction == "up") or (cvd < 0 and impact.direction == "down")

        # 分类逻辑（与 engine.py 保持一致）
        if (liq_count >= int(self.cfg["liq_count_threshold"])
                and liq_ratio > _to_decimal(self.cfg["liq_ratio_threshold"])
                and liq_value >= _to_decimal(self.cfg["liq_value_min"])):
            threshold = _to_decimal(self.cfg["liq_ratio_threshold"])
            confidence = min(liq_ratio / (threshold * Decimal("2")), Decimal("1"))
            classification, strategy = "真突破", "趋势跟随"
        elif liq_count <= int(self.cfg["liq_count_low"]) and liq_ratio < _to_decimal(self.cfg["liq_ratio_low"]):
            classification, strategy, confidence = "过度反应", "均值回归", Decimal("0.6")
        else:
            classification, strategy, confidence = "不确定", "放弃", Decimal("0")

        result = ClassificationResult(
            impact=impact,
            classification=classification,
            strategy=strategy,
            confidence=confidence,
            liq_count=liq_count,
            liq_value=liq_value,
            liq_ratio=liq_ratio,
            cvd_follows=cvd_follows,
            wait_seconds=wait_ms // 1000,
        )

        logger.info("分类结果: %s  清算%d笔 $%.0f  信心%.2f",
                    classification, liq_count, float(liq_value), float(confidence))

        # 真突破：无论信号是否最终执行，都后台追踪回调幅度，为25%参数提供验证数据
        if classification == "真突破":
            asyncio.create_task(self._track_pullback(impact, result))

        if classification == "不确定":
            return

        # 获取当前价格
        current_price = self.trades_30s[-1].price if self.trades_30s else None
        if current_price is None:
            logger.warning("无法获取当前价格，跳过")
            return

        # 熔断检查
        self.risk_manager.recent_trades = []   # 实盘暂简化，不传历史
        action, reason = self.risk_manager.check_circuit_breakers()
        if reason:
            self.paused_until_ms = self._pause_until_ms(int(time.time() * 1000), reason)
            logger.warning("熔断触发: %s  暂停至 %s", reason, action)
            return

        # 刷新余额（总余额用于仓位计算，可用余额检查保证金）
        try:
            self.balance = self.broker.get_balance()
            available = self.broker.get_available_balance()
        except Exception as exc:
            logger.error("获取余额失败: %s", exc)
            return

        if available <= 0:
            logger.warning("可用保证金为0，跳过开仓")
            return

        # 小资金阶段将杠杆限制为2x，避免保证金贴边导致 -2019
        effective_leverage = self.leverage
        if available <= Decimal("1000") and effective_leverage > 2:
            effective_leverage = 2
            logger.info("小资金模式启用：杠杆从 %dx 降至 %dx", self.leverage, effective_leverage)

        signal = execute_signal(
            result,
            env,
            current_price,
            self.balance,
            available_balance=available,
            leverage=effective_leverage,
        )
        if signal is None:
            logger.info("execute_signal 返回 None（不符合条件）")
            return

        # 用信号名义仓位估算所需保证金并加安全垫，避免下单后报 -2019
        est_margin = (signal.quantity * signal.entry_price) / Decimal(str(max(effective_leverage, 1)))
        if est_margin > available * Decimal("0.8"):
            logger.warning(
                "预计保证金过高 est=%.2f available=%.2f，跳过开仓",
                float(est_margin), float(available),
            )
            return

        logger.info("信号生成: side=%s grade=%s entry=%.1f sl=%.1f tp=%.1f qty=%.4f",
                    signal.side, signal.grade, float(signal.entry_price),
                    float(signal.stop_loss), float(signal.take_profit), float(signal.quantity))

        await self._execute(signal)

    # ── 下单执行 ──────────────────────────────────────────────────────

    async def _execute(self, signal: TradeSignal):
        """提交订单并启动持仓监控"""
        try:
            # 下单前先做轻量模式检查，若失败再做强制修复
            try:
                self.broker.ensure_one_way_mode()
            except BinanceError as exc:
                if exc.code in {-4067, -4068}:
                    self.broker.force_one_way_mode(self.symbol)
                else:
                    raise

            if signal.entry_type == "MARKET":
                order = self.broker.place_market_order(self.symbol, signal.side, signal.quantity)
                entry_filled = True
            else:
                order = self.broker.place_limit_order(
                    self.symbol, signal.side, signal.quantity, signal.entry_price
                )
                entry_filled = False

            order_id = order.get("orderId")
            if not order_id:
                logger.error("下单失败，无 orderId: %s", order)
                return

            # 等待限价单成交（最多 entry_expiry 秒）
            if not entry_filled:
                expiry = signal.entry_expiry or 180
                filled = await self._wait_for_fill(order_id, expiry)
                if not filled:
                    self.broker.cancel_order(self.symbol, order_id)
                    logger.info("限价单超时未成交，已撤单 orderId=%s", order_id)
                    return

            # 尝试挂硬件止损/止盈（测试网可能不支持条件单，失败时降级到软件监控）
            exit_side = "SELL" if signal.side == "BUY" else "BUY"
            hw_sl_ok = False
            hw_tp_ok = False
            try:
                self.broker.place_stop_loss(self.symbol, exit_side, signal.quantity, signal.stop_loss)
                hw_sl_ok = True
            except BinanceError as exc:
                logger.warning("硬件止损单失败(code=%d)，将由软件监控接管: %s", exc.code, exc.msg)
            try:
                self.broker.place_take_profit(self.symbol, exit_side, signal.quantity, signal.take_profit)
                hw_tp_ok = True
            except BinanceError as exc:
                logger.warning("硬件止盈单失败(code=%d)，将由软件监控接管: %s", exc.code, exc.msg)

            logger.info("硬件SL=%s  硬件TP=%s → 软件价格监控启动", hw_sl_ok, hw_tp_ok)

            # 记录开仓信息（用于PnL计算）
            entry_price = signal.entry_price if signal.entry_type == "LIMIT" else self.trades_30s[-1].price if self.trades_30s else signal.entry_price
            self._current_position = {
                "side": signal.side,
                "quantity": signal.quantity,
                "entry_price": entry_price,
                "entry_time": int(time.time()),
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
            }

            # 标记持仓开始，启动软件监控（价格止损/止盈 + 时间止损）
            self.open_until_ms = int(time.time() * 1000) + signal.time_stop * 1000
            asyncio.create_task(self._position_monitor(signal))

        except BinanceError as exc:
            logger.error("下单 Binance 错误: %s", exc)
        except Exception as exc:
            logger.error("下单异常: %s", exc, exc_info=True)

    def _log_trade_pnl(self):
        """计算并记录交易PnL"""
        if not self._current_position:
            return

        try:
            # 获取平仓价格
            exit_price = self.trades_30s[-1].price if self.trades_30s else None
            if not exit_price:
                return

            side = self._current_position["side"]
            qty = self._current_position["quantity"]
            entry_price = self._current_position["entry_price"]
            entry_time = self._current_position["entry_time"]
            exit_time = int(time.time())
            duration_sec = exit_time - entry_time

            # 计算PnL（扣除双边手续费）
            # Binance Taker费率：0.05% * 2 = 0.10%
            FEE_RATE = Decimal("0.001")  # 0.1%

            if side == "BUY":
                gross_pnl = (exit_price - entry_price) * qty
                # 双边手续费：入场费 + 出场费
                fee = (entry_price * qty + exit_price * qty) * FEE_RATE
                pnl = gross_pnl - fee
            else:  # SELL
                gross_pnl = (entry_price - exit_price) * qty
                fee = (entry_price * qty + exit_price * qty) * FEE_RATE
                pnl = gross_pnl - fee

            # 更新统计
            self.total_pnl += pnl
            self.total_trades += 1
            if pnl > 0:
                self.win_trades += 1

            win_rate = self.win_trades / self.total_trades * 100 if self.total_trades > 0 else 0

            # 记录PnL
            logger.info(
                "【交易完成】%s  qty=%.4f  entry=%.1f  exit=%.1f  pnl=%.2f  duration=%ds  累计:%d笔 %.2f(胜率%.1f%%)",
                side, float(qty), float(entry_price), float(exit_price),
                float(pnl), duration_sec, self.total_trades, float(self.total_pnl), win_rate
            )

        except Exception as exc:
            logger.warning("PnL计算异常: %s", exc)
        finally:
            self._current_position = None

    def _ensure_mode_health(self, now_ms: int):
        """每5分钟做一次持仓模式健康检查。"""
        if now_ms < self._next_mode_health_check_ms:
            return
        self._next_mode_health_check_ms = now_ms + 300_000
        try:
            self.broker.ensure_one_way_mode()
        except BinanceError as exc:
            # 有仓位/挂单时先不强切，避免打断持仓；仅在空仓阶段做强修复
            if exc.code in {-4067, -4068} and now_ms >= self.open_until_ms:
                try:
                    self.broker.force_one_way_mode(self.symbol)
                except Exception as force_exc:
                    logger.warning("持仓模式强修复失败: %s", force_exc)
            else:
                logger.warning("持仓模式健康检查失败: %s", exc)
        except Exception as exc:
            logger.warning("持仓模式健康检查失败: %s", exc)

    async def _wait_for_fill(self, order_id: int, timeout_sec: int) -> bool:
        """轮询订单状态，等待成交"""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                order = self.broker.get_order(self.symbol, order_id)
                status = order.get("status", "")
                if status == "FILLED":
                    logger.info("限价单已成交 orderId=%s", order_id)
                    return True
                if status in ("CANCELED", "EXPIRED", "REJECTED"):
                    return False
            except Exception as exc:
                logger.warning("查询订单异常: %s", exc)
            await asyncio.sleep(2)
        return False

    async def _position_monitor(self, signal: TradeSignal):
        """
        综合持仓监控：每2秒检查价格，满足以下任一条件即平仓：
          1. 价格触及止损线（软件SL）
          2. 价格触及止盈线（软件TP）
          3. 超过 time_stop 秒（时间止损）
        注：若硬件SL/TP已成交，get_position 会返回 None，监控自动退出。
        """
        deadline = time.time() + signal.time_stop
        close_reason = "时间止损"

        while time.time() < deadline:
            await asyncio.sleep(2)
            # 先确认仓位还在（硬件订单可能已平仓）
            try:
                pos = self.broker.get_position(self.symbol)
            except Exception as exc:
                logger.warning("监控查仓异常: %s", exc)
                continue
            if pos is None:
                logger.info("持仓已不存在（可能硬件SL/TP已触发），监控退出")
                self.open_until_ms = 0
                return

            # 软件价格检查
            if self.trades_30s:
                cur = self.trades_30s[-1].price
                if signal.side == "BUY":
                    if cur <= signal.stop_loss:
                        close_reason = f"软件止损 cur={float(cur):.1f} sl={float(signal.stop_loss):.1f}"
                        break
                    if cur >= signal.take_profit:
                        close_reason = f"软件止盈 cur={float(cur):.1f} tp={float(signal.take_profit):.1f}"
                        break
                else:  # SELL
                    if cur >= signal.stop_loss:
                        close_reason = f"软件止损 cur={float(cur):.1f} sl={float(signal.stop_loss):.1f}"
                        break
                    if cur <= signal.take_profit:
                        close_reason = f"软件止盈 cur={float(cur):.1f} tp={float(signal.take_profit):.1f}"
                        break

        # 触发平仓
        try:
            pos = self.broker.get_position(self.symbol)
            if pos:
                logger.warning("持仓监控触发平仓 原因=%s", close_reason)
                self.broker.cancel_all_orders(self.symbol)
                self.broker.close_position(self.symbol, pos)
            else:
                logger.info("监控结束时已无持仓（原因=%s）", close_reason)
        except Exception as exc:
            logger.error("监控平仓异常: %s", exc)
        finally:
            # 计算并记录PnL
            self._log_trade_pnl()
            self.open_until_ms = 0

    # ── 环境构建（复用回测引擎逻辑）─────────────────────────────────

    def _build_env(self, ts_ms: int, momentum_15m: Decimal) -> EnvironmentResult:
        six_h_start = self.trades_6h[0].timestamp if self.trades_6h else ts_ms
        hours_covered = max(Decimal("1"), Decimal(str((ts_ms - six_h_start) / 3_600_000)))
        baseline_1h = self.vol_6h / hours_covered if self.vol_6h > 0 else self.vol_1h
        volume_ratio = self.vol_1h / baseline_1h if baseline_1h > 0 else Decimal("1")
        return self._engine._build_environment(
            timestamp_ms=ts_ms,
            hour_volume=self.vol_1h,
            six_hour_volume=self.vol_6h,
            momentum_15m=momentum_15m,
            liq_value_30m=self.liq_val_30m,
            buy_liq_value_30m=self.buy_liq_val_30m,
            sell_liq_value_30m=self.sell_liq_val_30m,
            six_hour_start_ts=six_h_start,
        )

    # ── 真突破回调追踪 ────────────────────────────────────────────────

    async def _track_pullback(self, impact: ImpactEvent, result: ClassificationResult):
        """
        真突破后监控180秒内价格行为，记录到 data/processed/breakout_pullbacks.jsonl
        核心问题：25%回调限价入场，实际能有多少成交？
        积累数据后由 _report_pullback_stats() 自动分析并给出参数建议。
        """
        WATCH_SEC = 180   # 与 entry_expiry 对齐
        INTERVAL  = 2     # 每2秒采样一次

        price_after  = float(impact.price_after)
        price_before = float(impact.price_before)
        range_impact = abs(price_after - price_before)
        if range_impact <= 0:
            return

        # 采集180秒内的价格序列
        samples: list[float] = []
        for _ in range(WATCH_SEC // INTERVAL):
            await asyncio.sleep(INTERVAL)
            if self.trades_30s:
                samples.append(float(self.trades_30s[-1].price))

        if not samples:
            return

        # 计算最大回调（以冲击幅度为分母，归一化为%）
        if impact.direction == "up":
            worst        = min(samples)                                       # 最低价
            pullback_pct = (price_after - worst) / range_impact * 100
            entry_25     = price_after - range_impact * 0.25
            filled_25    = worst <= entry_25
        else:
            worst        = max(samples)                                       # 最高价
            pullback_pct = (worst - price_after) / range_impact * 100
            entry_25     = price_after + range_impact * 0.25
            filled_25    = worst >= entry_25

        record = {
            "dt":             datetime.fromtimestamp(
                                  impact.detected_at_ms / 1000, tz=timezone.utc
                              ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "direction":      impact.direction,
            "price_before":   round(price_before, 1),
            "price_after":    round(price_after,  1),
            "range_impact":   round(range_impact, 1),
            "entry_25pct":    round(entry_25, 1),
            "max_pullback_pct": round(pullback_pct, 2),
            "filled_25pct":   filled_25,
            "liq_count":      result.liq_count,
            "liq_value_usd":  round(float(result.liq_value), 0),
            "confidence":     round(float(result.confidence), 3),
        }

        out_path = Path(__file__).parent.parent / "data" / "processed" / "breakout_pullbacks.jsonl"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.info(
            "回调追踪完成 方向=%s 范围=%.0f 最大回调=%.1f%% 25%%成交=%s",
            impact.direction, range_impact, pullback_pct, filled_25,
        )

        # 每新增一笔就尝试输出分析（5笔起算，50笔后给调参建议）
        _report_pullback_stats(out_path)

    # ── 工具 ──────────────────────────────────────────────────────────

    @staticmethod
    def _pause_until_ms(now_ms: int, reason: str) -> int:
        if reason == "daily_loss_limit":
            from datetime import timedelta
            dt = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
            next_day = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc) + timedelta(days=1)
            return int(next_day.timestamp() * 1000)
        if reason in {"large_single_loss", "consecutive_losses"}:
            return now_ms + 30 * 60 * 1000
        if reason == "low_win_rate":
            return now_ms + 60 * 60 * 1000
        return now_ms


# ── 回调统计分析 ──────────────────────────────────────────────────────

def _report_pullback_stats(path: Path) -> None:
    """
    读取 breakout_pullbacks.jsonl，输出回调分布与入场参数建议。
    5笔起输出基础统计；50笔起给出明确的调参建议。
    """
    try:
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        records = [json.loads(l) for l in lines]
    except Exception:
        return

    n = len(records)
    if n < 5:
        return

    pullbacks  = sorted(r["max_pullback_pct"] for r in records)
    fill_rate  = sum(1 for r in records if r["filled_25pct"]) / n * 100
    avg        = sum(pullbacks) / n
    median     = pullbacks[n // 2]

    sep = "─" * 52
    logger.info(sep)
    logger.info("真突破回调统计（%d笔）", n)
    logger.info("  平均=%.1f%%  中位数=%.1f%%  最小=%.1f%%  最大=%.1f%%",
                avg, median, pullbacks[0], pullbacks[-1])
    logger.info("  当前25%%回调入场成交率: %.1f%%", fill_rate)
    logger.info("  各阈值击中概率:")
    for thr in [10, 15, 20, 25, 30, 40, 50]:
        cnt = sum(1 for p in pullbacks if p >= thr)
        logger.info("    ≥%2d%%: %d/%d (%.0f%%)", thr, cnt, n, cnt / n * 100)

    if n >= 50:
        # 各百分位回调深度
        p50 = pullbacks[int(n * 0.50)]
        p70 = pullbacks[int(n * 0.70)]
        p80 = pullbacks[int(n * 0.80)]
        logger.info("  百分位: P50=%.1f%%  P70=%.1f%%  P80=%.1f%%", p50, p70, p80)
        # 给出建议
        if fill_rate < 40:
            logger.info("  ★ 建议：25%%入场成交率仅%.1f%%，考虑改用市价单入场", fill_rate)
        elif fill_rate < 60:
            logger.info("  ★ 建议：入场回调改为 %.0f%%（覆盖70%%样本）", p70)
        else:
            logger.info("  ★ 建议：25%%入场成交率%.1f%%，当前参数合理", fill_rate)

    logger.info(sep)


# ── 入口 ─────────────────────────────────────────────────────────────

def main():
    # ── 日志配置：控制台（UTF-8）+ 文件 ──────────────────────────────
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"live_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    # 控制台 Handler：强制 UTF-8，解决 Windows GBK 乱码
    import io as _io
    console_stream = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True) \
        if hasattr(sys.stdout, "buffer") else sys.stdout
    console_handler = logging.StreamHandler(console_stream)
    console_handler.setFormatter(formatter)

    # 文件 Handler：UTF-8 持久记录
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    logging.basicConfig(level=logging.INFO, handlers=[console_handler, file_handler])
    env = _load_env()
    api_key = env.get("BINANCE_API_KEY", "")
    secret = env.get("BINANCE_SECRET_KEY", "")
    testnet = env.get("TESTNET", "true").lower() == "true"

    if not api_key or not secret:
        raise RuntimeError("缺少 BINANCE_API_KEY / BINANCE_SECRET_KEY，请检查 .env")

    broker = LiveBroker(api_key, secret, testnet=testnet)
    trader = LiveTrader(broker, leverage=2)  # 小资金阶段遵循 2x 上限
    asyncio.run(trader.run())


if __name__ == "__main__":
    main()
