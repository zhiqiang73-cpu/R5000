# -*- coding: utf-8 -*-
"""
时间上下文模块 - 为环境过滤提供时间相关数据

构建时间上下文:
- UTC/Binance时间戳
- 上海时间 (UTC+8)
- 距离资金费率结算时间
- 历史同期期望值
- 时间约束条件
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, Optional, List
import logging

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - runtime dependency
    requests = None

logger = logging.getLogger(__name__)

# ==================== Binance REST 配置 ====================

BINANCE_FAPI_BASE = "https://fapi.binance.com"
# 清算数据文件目录（websocket.py 写入的路径）
_LIQUIDATIONS_DIR = Path(__file__).parent.parent.parent / "data" / "raw" / "liquidations"


def _binance_get(path: str, params: dict = None, max_attempts: int = 3) -> dict | list:
    """
    Binance REST GET 请求，带超时和简单重试。
    失败时抛出异常，由调用方负责 fallback。
    """
    url = f"{BINANCE_FAPI_BASE}{path}"
    if requests is None:
        raise RuntimeError("requests is not installed")
    for attempt in range(max_attempts):
        try:
            resp = requests.get(url, params=params or {}, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt == max_attempts - 1:
                raise
            time.sleep(1 * (attempt + 1))  # 简单退避
    return {}


@dataclass
class SameHourExpectations:
    """同小时历史期望值（来自过去7天同UTC小时的真实数据）"""
    fr_mean: Decimal = Decimal("0")           # 资金费率均值（近21天 FR 历史）
    fr_std: Decimal = Decimal("0.0001")       # 资金费率标准差
    vol_mean: Decimal = Decimal("0")          # 同UTC小时成交量均值（近7天，≥3样本）
    vol_median: Decimal = Decimal("0")        # 同UTC小时成交量中位数
    oi_mean: Decimal = Decimal("500000000")   # 持仓量均值（当前OI已单独获取，此字段为后备）
    oi_change_mean: Decimal = Decimal("0")    # 同UTC小时OI 1h变化均值（USDT，近7天）
    liq_mean: Decimal = Decimal("0")          # 清算量均值（暂无历史，保留字段）
    sample_count: int = 0                     # 计算 vol_mean 所用的样本数
    data_source: str = "hardcoded"            # "api" | "hardcoded"，便于排查


@dataclass
class TimeContext:
    """时间上下文数据类"""
    utc_now_ms: int                          # 当前UTC时间(毫秒)
    shanghai_now_str: str                    # 上海时间字符串 "YYYY-MM-DD HH:MM"
    hours_to_funding: float                  # 距离下次资金费率结算的小时数
    funding_times: List[datetime] = field(default_factory=list)  # 今日资金费率时间点
    same_hour_expectations: SameHourExpectations = None  # 历史同期期望
    deviation_duration_min: int = 0          # 偏离持续分钟数(资金费率极端)
    constraints: List[str] = field(default_factory=list)  # 约束条件
    weekday: int = 0                         # 星期几(0=周一)


# ── 同小时期望值缓存（避免每10秒调用一次API）──────────────────────
# key = utc_hour(0-23)，value = (SameHourExpectations, cached_at_epoch_sec)
_SAME_HOUR_CACHE: Dict[int, tuple] = {}
_SAME_HOUR_CACHE_TTL = 3600  # 1小时刷新一次即可


def _compute_same_hour_expectations(utc_hour: int) -> SameHourExpectations:
    """
    从 Binance 历史数据计算指定 UTC 小时的真实期望值。

    成交量：取过去 7 天（168 根 1h K线）中与 utc_hour 相同的那些 K 线，
           计算成交量均值和中位数（≈7 个样本）。
    资金费率：取过去 63 条 FR 记录（≈21 天），计算全域均值/标准差。
    """
    try:
        # 1. 获取 168 根 1h K线（≈7天）
        klines = _binance_get(
            "/fapi/v1/klines",
            {"symbol": "BTCUSDT", "interval": "1h", "limit": 168}
        )
        same_hour_vols = []
        for k in klines:
            open_dt = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
            if open_dt.hour == utc_hour:
                same_hour_vols.append(Decimal(str(k[5])))  # volume (BTC)

        if len(same_hour_vols) >= 3:
            vol_mean = sum(same_hour_vols) / len(same_hour_vols)
            vol_sorted = sorted(same_hour_vols)
            mid = len(vol_sorted) // 2
            vol_median = vol_sorted[mid]
            sample_count = len(same_hour_vols)
        else:
            # 样本不足时保守使用所有K线均值
            all_vols = [Decimal(str(k[5])) for k in klines] if klines else []
            vol_mean = (sum(all_vols) / len(all_vols)) if all_vols else Decimal("15000")
            vol_sorted = sorted(all_vols) if all_vols else [Decimal("12000")]
            mid = len(vol_sorted) // 2
            vol_median = vol_sorted[mid]
            sample_count = len(same_hour_vols)
            logger.warning(
                f"UTC小时 {utc_hour} 样本数不足({sample_count})，"
                f"使用全时段均值 {vol_mean:.0f} BTC 作为后备"
            )

        # 2. 获取近 63 条资金费率（≈21天）
        fr_data = _binance_get(
            "/fapi/v1/fundingRate",
            {"symbol": "BTCUSDT", "limit": 63}
        )
        fr_rates = [Decimal(str(d["fundingRate"])) for d in fr_data]

        if len(fr_rates) >= 5:
            fr_mean = sum(fr_rates) / len(fr_rates)
            variance = sum((r - fr_mean) ** 2 for r in fr_rates) / len(fr_rates)
            fr_std = Decimal(str(float(variance) ** 0.5))
            if fr_std == 0:
                fr_std = Decimal("0.0001")
        else:
            fr_mean = Decimal("0.0001")
            fr_std = Decimal("0.0003")

        # 3. 获取同小时 OI 1h 变化均值（≈7天×1样本/天 = 7样本）
        # 取 169 条 1h OI 快照，计算相邻差值，过滤出目标 UTC 小时的差值
        oi_change_mean = Decimal("0")
        try:
            oi_hist = _binance_get(
                "/futures/data/openInterestHist",
                {"symbol": "BTCUSDT", "period": "1h", "limit": 169}
            )
            same_hour_oi_changes = []
            for i in range(1, len(oi_hist)):
                ts_ms = oi_hist[i].get("timestamp", 0)
                ts_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                if ts_dt.hour == utc_hour:
                    oi_curr = Decimal(str(oi_hist[i]["sumOpenInterestValue"]))
                    oi_prev = Decimal(str(oi_hist[i - 1]["sumOpenInterestValue"]))
                    same_hour_oi_changes.append(oi_curr - oi_prev)
            if len(same_hour_oi_changes) >= 3:
                oi_change_mean = sum(same_hour_oi_changes) / len(same_hour_oi_changes)
        except Exception as oi_exc:
            logger.warning(f"OI同小时均值计算失败: {oi_exc}，使用默认值0")

        logger.info(
            f"同小时期望值 UTC{utc_hour:02d}h: "
            f"vol_mean={float(vol_mean):.0f} BTC (n={sample_count}), "
            f"fr_mean={float(fr_mean):.6f}, fr_std={float(fr_std):.6f}, "
            f"oi_change_mean={float(oi_change_mean)/1e6:.1f}M USDT"
        )

        return SameHourExpectations(
            fr_mean=fr_mean,
            fr_std=fr_std,
            vol_mean=vol_mean,
            vol_median=vol_median,
            oi_mean=Decimal("500000000"),   # 保留后备，current_oi 已实时获取
            oi_change_mean=oi_change_mean,
            liq_mean=Decimal("0"),
            sample_count=sample_count,
            data_source="api",
        )

    except Exception as exc:
        logger.warning(f"_compute_same_hour_expectations 失败: {exc}，使用硬编码后备值")
        return _hardcoded_fallback_expectations(utc_hour)


def _hardcoded_fallback_expectations(utc_hour: int) -> SameHourExpectations:
    """API 失败时的硬编码后备值（保守估计，仅用于降级）"""
    # 粗略按 UTC 时段划分（亚洲盘 / 欧美盘）
    if 0 <= utc_hour < 8:    # 亚洲盘（UTC 0-8 = 上海 8-16）
        vol_mean = Decimal("20000")
    elif 8 <= utc_hour < 16:  # 欧洲盘（UTC 8-16 = 上海 16-24）
        vol_mean = Decimal("25000")
    else:                      # 美洲盘（UTC 16-24 = 上海 0-8）
        vol_mean = Decimal("18000")
    return SameHourExpectations(
        fr_mean=Decimal("0.0001"),
        fr_std=Decimal("0.0003"),
        vol_mean=vol_mean,
        vol_median=vol_mean * Decimal("0.85"),
        oi_mean=Decimal("500000000"),
        oi_change_mean=Decimal("0"),
        liq_mean=Decimal("0"),
        sample_count=0,
        data_source="hardcoded",
    )


class TimeContextBuilder:
    """时间上下文构建器"""

    # 资金费率结算时间 (UTC): 00:00, 08:00, 16:00
    FUNDING_HOURS_UTC = [0, 8, 16]

    def __init__(self):
        self._stubs_loaded = False

    def build(self) -> TimeContext:
        """
        构建当前时间上下文

        Returns:
            TimeContext: 包含所有时间相关数据
        """
        # 获取当前UTC时间
        utc_now = datetime.now(timezone.utc)
        utc_now_ms = int(utc_now.timestamp() * 1000)

        # 计算上海时间 (UTC+8)
        shanghai_tz = timezone(timedelta(hours=8))
        shanghai_now = utc_now.astimezone(shanghai_tz)
        shanghai_now_str = shanghai_now.strftime("%Y-%m-%d %H:%M")

        # 计算距离下次资金费率结算的小时数
        hours_to_funding = self._calc_hours_to_funding(utc_now)

        # 获取今日资金费率结算时间点
        funding_times = self._get_funding_times_today(utc_now)

        # 星期几 (Python: 0=周一, 6=周日)
        weekday = utc_now.weekday()

        # 获取历史同期期望值（用UTC小时，与Binance K线时间对齐）
        expectations = self._get_same_hour_expectations(utc_now.hour)

        # 计算资金费率偏离持续时间 (stub)
        deviation_duration_min = self._get_deviation_duration()

        # 生成时间约束
        constraints = self._generate_constraints(hours_to_funding, weekday)

        ctx = TimeContext(
            utc_now_ms=utc_now_ms,
            shanghai_now_str=shanghai_now_str,
            hours_to_funding=hours_to_funding,
            funding_times=funding_times,
            same_hour_expectations=expectations,
            deviation_duration_min=deviation_duration_min,
            constraints=constraints,
            weekday=weekday
        )

        logger.info(f"TimeContext built: {ctx.shanghai_now_str}, "
                   f"hours_to_funding={hours_to_funding:.2f}, "
                   f"weekday={weekday}")

        return ctx

    def _calc_hours_to_funding(self, utc_now: datetime) -> float:
        """
        计算距离下次资金费率结算的小时数

        资金费率结算时间: UTC 00:00, 08:00, 16:00 (每天3次)
        """
        current_hour = utc_now.hour
        current_minute = utc_now.minute
        current_min_of_day = current_hour * 60 + current_minute

        # 找出下一个结算时间
        next_funding_min = None
        for fh in self.FUNDING_HOURS_UTC:
            funding_min = fh * 60
            if funding_min > current_min_of_day:
                next_funding_min = funding_min
                break

        # 如果今天没有了，取明天的第一个
        if next_funding_min is None:
            next_funding_min = self.FUNDING_HOURS_UTC[0] * 60 + 24 * 60

        hours_to = (next_funding_min - current_min_of_day) / 60.0
        return max(0, hours_to)

    def _get_funding_times_today(self, utc_now: datetime) -> List[datetime]:
        """获取今日资金费率结算时间点"""
        times = []
        today = utc_now.date()

        for fh in self.FUNDING_HOURS_UTC:
            ft = datetime(today.year, today.month, today.day,
                         fh, 0, 0, tzinfo=timezone.utc)
            # 只返回未来的时间点
            if ft >= utc_now:
                times.append(ft)

        return times

    def _get_same_hour_expectations(self, utc_hour: int) -> SameHourExpectations:
        """
        获取同小时历史期望值（带缓存，1小时TTL）。

        优先读缓存；未命中时调用 _compute_same_hour_expectations() 从 Binance
        历史 K线和资金费率计算真实的 UTC 同小时统计量。
        """
        now_sec = time.time()
        cached = _SAME_HOUR_CACHE.get(utc_hour)
        if cached and (now_sec - cached[1]) < _SAME_HOUR_CACHE_TTL:
            logger.debug(f"Same-hour expectations UTC{utc_hour:02d}h: cache hit")
            return cached[0]

        exp = _compute_same_hour_expectations(utc_hour)
        _SAME_HOUR_CACHE[utc_hour] = (exp, now_sec)

        logger.debug(
            f"Same-hour expectations UTC{utc_hour:02d}h: "
            f"vol_mean={float(exp.vol_mean):.0f} BTC "
            f"(n={exp.sample_count}, src={exp.data_source}), "
            f"fr_mean={float(exp.fr_mean):.6f}"
        )
        return exp

    def _get_deviation_duration(self) -> int:
        """
        获取资金费率偏离持续的分钟数

        Stub: 返回0，未来需要结合实时资金费率数据计算
        """
        # TODO: 结合FR zscore计算偏离持续时间
        return 0

    def _generate_constraints(self, hours_to_funding: float, weekday: int) -> List[str]:
        """
        生成时间约束条件

        基于资金费率结算时间、星期等因素
        """
        constraints = []

        # 结算前2小时：套利者活跃，信号可靠性降低
        if hours_to_funding < 2:
            constraints.append("settle-2h: rel0.6")
        elif hours_to_funding < 4:
            constraints.append("settle-4h: rel0.8")
        else:
            constraints.append("settle-normal: rel1.0")

        # 刚结算完后：资金费率信息不足
        if hours_to_funding > 6 and hours_to_funding < 7.5:
            constraints.append("post-settle: rel0.4")

        # 周末流动性降低
        if weekday >= 5:  # 周六、周日
            constraints.append("weekend: vol-reduced")

        # 周一早晨：市场恢复期
        if weekday == 0 and hours_to_funding > 6:
            constraints.append("monday-morning: rel0.7")

        logger.debug(f"Generated constraints: {constraints}")
        return constraints


def get_time_context() -> TimeContext:
    """
    便捷函数：获取当前时间上下文

    Returns:
        TimeContext: 当前时间上下文
    """
    builder = TimeContextBuilder()
    return builder.build()


# ==================== 以下是环境层需要的辅助函数 ====================

def get_current_fr() -> Decimal:
    """
    获取当前资金费率。
    接口: /fapi/v1/premiumIndex  字段: lastFundingRate
    失败时返回 0.0001（中性值），并打 warning。
    """
    try:
        data = _binance_get("/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"})
        return Decimal(str(data["lastFundingRate"]))
    except Exception as exc:
        logger.warning(f"get_current_fr 失败: {exc}，使用默认值 0.0001")
        return Decimal("0.0001")


def get_current_oi() -> Decimal:
    """
    获取当前持仓量（USDT计价），取最近一条 OI 历史快照。
    接口: /futures/data/openInterestHist  字段: sumOpenInterestValue
    失败时返回 500_000_000。
    """
    try:
        data = _binance_get(
            "/futures/data/openInterestHist",
            {"symbol": "BTCUSDT", "period": "5m", "limit": 1}
        )
        if data:
            return Decimal(str(data[0]["sumOpenInterestValue"]))
    except Exception as exc:
        logger.warning(f"get_current_oi 失败: {exc}，使用默认值")
    return Decimal("500000000")


def get_oi_change_1h() -> Decimal:
    """
    获取过去 1 小时持仓量变化（USDT 计价）。
    用 13 个 5min 快照计算首尾差值（≈65 分钟覆盖）。
    接口: /futures/data/openInterestHist  字段: sumOpenInterestValue
    失败时返回 0。
    """
    try:
        data = _binance_get(
            "/futures/data/openInterestHist",
            {"symbol": "BTCUSDT", "period": "5m", "limit": 13}
        )
        if len(data) >= 2:
            current = Decimal(str(data[-1]["sumOpenInterestValue"]))
            one_hr_ago = Decimal(str(data[0]["sumOpenInterestValue"]))
            return current - one_hr_ago
    except Exception as exc:
        logger.warning(f"get_oi_change_1h 失败: {exc}，使用默认值 0")
    return Decimal("0")


def get_recent_volume_1h() -> Decimal:
    """
    获取过去 1 小时 BTC 成交量（合约 BTC 数量）。
    取最近 60 根 1min K 线的 volume 字段（index 5）求和。
    接口: /fapi/v1/klines
    失败时返回 15000。
    """
    try:
        data = _binance_get(
            "/fapi/v1/klines",
            {"symbol": "BTCUSDT", "interval": "1m", "limit": 60}
        )
        total = sum(Decimal(str(k[5])) for k in data)
        return total
    except Exception as exc:
        logger.warning(f"get_recent_volume_1h 失败: {exc}，使用默认值 15000")
        return Decimal("15000")


def get_recent_liquidations_30m() -> Decimal:
    """
    获取过去 30 分钟 BTCUSDT 清算总量（USDT 计价）。
    从 websocket.py 写入的 JSONL 文件读取。
    文件格式: {"local_ts": ms, "server_ts": ms, "data": {"o": {"s", "q", "p", ...}}}
    若文件不存在（websocket 采集器未运行），返回 0 并打 warning。
    """
    try:
        if not _LIQUIDATIONS_DIR.exists():
            logger.warning(
                f"清算数据目录不存在 ({_LIQUIDATIONS_DIR})，"
                "请先运行 websocket 采集器，返回 0"
            )
            return Decimal("0")

        cutoff_ms = int((datetime.now(timezone.utc).timestamp() - 1800) * 1000)
        total_liq = Decimal("0")
        found_any_file = False

        # 防止跨天时漏掉前一天的数据
        today = datetime.now(timezone.utc)
        dates_to_check = [
            today.strftime("%Y-%m-%d"),
            (today - timedelta(days=1)).strftime("%Y-%m-%d"),
        ]

        for date_str in dates_to_check:
            fpath = _LIQUIDATIONS_DIR / f"{date_str}.jsonl"
            if not fpath.exists():
                continue
            found_any_file = True

            with open(fpath, encoding="utf-8") as f:
                for line in f:
                    try:
                        record = json.loads(line)
                        # 兼容两种格式：
                        # 新格式（websocket.py 包装）: {"local_ts":..., "data":{"o":{...}}}
                        # 旧格式（原始消息）:          {"E":..., "o":{...}, "local_ts":...}
                        ts = record.get("local_ts", record.get("E", 0))
                        if ts < cutoff_ms:
                            continue
                        if "data" in record:
                            order = record["data"].get("o", {})
                        else:
                            order = record.get("o", {})
                        if order.get("s") != "BTCUSDT":
                            continue
                        qty = Decimal(str(order.get("q", 0)))
                        price = Decimal(str(order.get("ap", order.get("p", 0))))
                        if qty > 0 and price > 0:
                            total_liq += qty * price
                    except Exception:
                        continue

        if not found_any_file:
            logger.warning("未找到清算 JSONL 文件，websocket 采集器可能未运行，返回 0")

        return total_liq

    except Exception as exc:
        logger.warning(f"get_recent_liquidations_30m 失败: {exc}，返回 0")
        return Decimal("0")


if __name__ == "__main__":
    # 测试
    logging.basicConfig(level=logging.INFO)
    ctx = get_time_context()
    print(f"UTC now (ms): {ctx.utc_now_ms}")
    print(f"Shanghai time: {ctx.shanghai_now_str}")
    print(f"Hours to funding: {ctx.hours_to_funding:.2f}")
    print(f"Weekday: {ctx.weekday}")
    print(f"Constraints: {ctx.constraints}")
    print(f"FR mean: {ctx.same_hour_expectations.fr_mean}")
    print(f"Volume mean: {ctx.same_hour_expectations.vol_mean}")