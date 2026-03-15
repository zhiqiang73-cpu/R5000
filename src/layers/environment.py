# -*- coding: utf-8 -*-
"""
环境过滤层 (Layer 1)

目的：判断当前市场环境是否值得交易
输入：资金费率、持仓量(OI)、成交量、清算流
输出：可交易/休眠 + 方向偏向 + 调整参数

核心逻辑参考 CLAUDE.md 伪代码
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Tuple, Optional, Dict, Any
import logging

from .time_context import (
    TimeContext, get_time_context,
    get_current_fr, get_current_oi, get_oi_change_1h,
    get_recent_volume_1h, get_recent_liquidations_30m
)

logger = logging.getLogger(__name__)


@dataclass
class EnvironmentResult:
    """环境评估结果"""
    status: str                      # "可交易" | "休眠"
    direction_bias: str              # "偏多" | "偏空" | "中性"
    liquidation_side: Optional[str]  # "多头" | "空头" | None
    suitability: Decimal             # 适合度 0-1
    time_context: TimeContext        # 时间上下文
    adjustments: Dict[str, Any]      # 调整参数（含 stop_multiplier）
    reason: str                      # 原因说明
    # 诊断字段：供仪表盘和下游层直接使用
    fr_zscore: Optional[Decimal] = None   # 资金费率 Z-score
    oi_change_pct: Decimal = Decimal("0") # OI 1小时变化率
    liq_volume: Decimal = Decimal("0")    # 近30分钟清算量 (USDT)
    volume_ratio: Decimal = Decimal("1")  # 成交量比率 (1H / 历史均值)
    # 休眠触发详情：{"指标名": "实际值 vs 阈值"}，仅休眠时填充
    dormant_trigger: Dict[str, str] = None


class EnvironmentEvaluator:
    """环境评估器 - 第1层核心逻辑"""

    # Z-score阈值
    FR_ZSCORE_EXTREME = Decimal("2.0")    # 资金费率极端阈值
    FR_ZSCORE_MODERATE = Decimal("1.0")   # 资金费率中等阈值

    # OI变化阈值
    OI_CHANGE_HIGH = Decimal("0.03")      # OI变化>3%为高
    OI_CHANGE_LOW = Decimal("0.01")       # OI变化<1%为低

    # 成交量阈值（三段式）
    VOLUME_RATIO_DORMANT = Decimal("0.3")  # < 0.3 → 休眠（流动性真空）
    VOLUME_RATIO_REDUCED = Decimal("0.5")  # 0.3–0.5 → 可交易但仓位减半

    # 清算量阈值 (单位需要统一，这里用USDT计)
    LIQ_VOLUME_HIGH = Decimal("1000000")   # 高清算量阈值
    LIQ_VOLUME_LOW = Decimal("100000")     # 低清算量阈值

    def __init__(self, time_ctx: Optional[TimeContext] = None):
        """
        初始化环境评估器

        Args:
            time_ctx: 时间上下文，如果为None则自动构建
        """
        self.time_ctx = time_ctx or get_time_context()

    def evaluate(self) -> EnvironmentResult:
        """
        评估当前市场环境

        Returns:
            EnvironmentResult: 评估结果
        """
        logger.info("=" * 50)
        logger.info("开始环境评估 (Layer 1)")

        # Step 1: 资金费率分析
        fr_result = self._evaluate_funding_rate()
        direction_bias = fr_result["direction_bias"]
        liquidation_side = fr_result["liquidation_side"]
        fr_reliability = fr_result["reliability"]

        logger.info(f"资金费率分析: bias={direction_bias}, "
                   f"liq_side={liquidation_side}, rel={fr_reliability}")

        # Step 2: OI变化分析
        oi_result = self._evaluate_oi_change()
        activity_level = oi_result["activity_level"]

        logger.info(f"OI变化分析: activity={activity_level}, "
                   f"change_pct={oi_result['change_pct']:.2%}")

        # Step 3: 成交量分析
        vol_result = self._evaluate_volume()
        volume_ratio = vol_result["ratio"]
        vol_state = vol_result["vol_state"]

        logger.info(f"成交量分析: ratio={volume_ratio:.2%}, state={vol_state}")

        # Step 4: 清算流分析
        liq_result = self._evaluate_liquidations()
        market_stress = liq_result["stress"]
        liq_volume = liq_result["volume"]

        logger.info(f"清算流分析: stress={market_stress}, vol={liq_volume}")

        # Step 5: 综合判断（携带诊断字段）
        final_result = self._make_final_decision(
            direction_bias=direction_bias,
            liquidation_side=liquidation_side,
            activity_level=activity_level,
            volume_ratio=volume_ratio,
            vol_state=vol_state,
            market_stress=market_stress,
            fr_reliability=fr_reliability,
            fr_zscore=fr_result["zscore"],
            oi_change_pct=oi_result["change_pct"],
            liq_volume=liq_result["volume"],
        )

        logger.info(f"环境评估结果: status={final_result.status}, "
                   f"bias={final_result.direction_bias}, "
                   f"suitability={final_result.suitability}")
        logger.info("=" * 50)

        return final_result

    def _evaluate_funding_rate(self) -> Dict[str, Any]:
        """
        评估资金费率

        Returns:
            dict: {
                'direction_bias': 方向偏向,
                'liquidation_side': 可能的清算方,
                'reliability': 资金费率可靠性
            }
        """
        # 获取当前资金费率 (stub)
        current_fr = get_current_fr()

        # 获取历史期望
        exp = self.time_ctx.same_hour_expectations
        fr_mean = exp.fr_mean
        fr_std = exp.fr_std

        # 避免除零
        if fr_std == 0:
            fr_std = Decimal("0.0001")

        # 计算Z-score
        fr_zscore = (current_fr - fr_mean) / fr_std

        logger.debug(f"资金费率: current={current_fr:.6f}, "
                    f"mean={fr_mean:.6f}, zscore={fr_zscore:.2f}")

        # 判断方向
        if fr_zscore > self.FR_ZSCORE_EXTREME:
            direction_bias = "偏空"
            liquidation_side = "多头"  # 多头过热，容易被清算
        elif fr_zscore < -self.FR_ZSCORE_EXTREME:
            direction_bias = "偏多"
            liquidation_side = "空头"  # 空头过热
        elif fr_zscore > self.FR_ZSCORE_MODERATE:
            direction_bias = "偏空"
            liquidation_side = "多头"
        elif fr_zscore < -self.FR_ZSCORE_MODERATE:
            direction_bias = "偏多"
            liquidation_side = "空头"
        else:
            direction_bias = "中性"
            liquidation_side = None

        # 资金费率可靠性 (基于距离结算时间)
        hours_to = self.time_ctx.hours_to_funding
        if hours_to < 2:
            reliability = Decimal("0.6")  # 结算前2小时
        elif hours_to > 6:
            reliability = Decimal("0.4")  # 刚结算完
        else:
            reliability = Decimal("1.0")  # 正常时段

        return {
            "direction_bias": direction_bias,
            "liquidation_side": liquidation_side,
            "reliability": reliability,
            "zscore": fr_zscore
        }

    def _evaluate_oi_change(self) -> Dict[str, Any]:
        """
        评估持仓量变化

        Returns:
            dict: {
                'activity_level': 活跃度 "高"/"正常"/"低",
                'change_pct': 变化百分比（相对当前OI，符合CLAUDE.md规范）
            }
        """
        oi_change = get_oi_change_1h()
        # CLAUDE.md 规范：分母用 current_oi 而非历史均值
        current_oi = get_current_oi()
        if current_oi == 0:
            current_oi = self.time_ctx.same_hour_expectations.oi_mean or Decimal("500000000")

        change_pct = oi_change / current_oi

        # 判断活跃度
        if abs(change_pct) > self.OI_CHANGE_HIGH:
            activity_level = "高"
        elif abs(change_pct) < self.OI_CHANGE_LOW:
            activity_level = "低"
        else:
            activity_level = "正常"

        return {
            "activity_level": activity_level,
            "change_pct": change_pct,
            "oi_change": oi_change
        }

    def _evaluate_volume(self) -> Dict[str, Any]:
        """
        评估成交量状态（三段式）

        Returns:
            dict: {
                'ratio': 成交量比率,
                'vol_state': "dormant" | "reduced" | "normal"
            }
        """
        current_vol = get_recent_volume_1h()
        exp = self.time_ctx.same_hour_expectations
        vol_mean = exp.vol_mean

        if vol_mean == 0:
            vol_mean = Decimal("15000")

        ratio = current_vol / vol_mean

        if ratio < self.VOLUME_RATIO_DORMANT:
            vol_state = "dormant"   # < 0.3 → 休眠
        elif ratio < self.VOLUME_RATIO_REDUCED:
            vol_state = "reduced"   # 0.3–0.5 → 仓位减半
        else:
            vol_state = "normal"    # ≥ 0.5 → 正常

        return {
            "ratio": ratio,
            "vol_state": vol_state,
            "current_vol": current_vol,
            "mean_vol": vol_mean
        }

    def _evaluate_liquidations(self) -> Dict[str, Any]:
        """
        评估清算流活跃度

        Returns:
            dict: {
                'stress': 市场压力 "高"/"正常"/"低",
                'volume': 清算量
            }
        """
        # 获取30分钟清算量 (stub)
        liq_volume = get_recent_liquidations_30m()

        # 判断市场压力
        if liq_volume > self.LIQ_VOLUME_HIGH:
            stress = "高"
        elif liq_volume < self.LIQ_VOLUME_LOW:
            stress = "低"
        else:
            stress = "正常"

        return {
            "stress": stress,
            "volume": liq_volume
        }

    def _make_final_decision(
        self,
        direction_bias: str,
        liquidation_side: Optional[str],
        activity_level: str,
        volume_ratio: Decimal,
        vol_state: str,
        market_stress: str,
        fr_reliability: Decimal,
        fr_zscore: Optional[Decimal] = None,
        oi_change_pct: Decimal = Decimal("0"),
        liq_volume: Decimal = Decimal("0"),
    ) -> EnvironmentResult:
        """
        综合所有因素，做出最终决策

        vol_state: "dormant" (<0.3) | "reduced" (0.3–0.5) | "normal" (≥0.5)

        Returns:
            EnvironmentResult: 最终决策
        """
        # 1. 流动性检查 - 流动性真空直接休眠
        if vol_state == "dormant":
            return EnvironmentResult(
                status="休眠",
                direction_bias="中性",
                liquidation_side=None,
                suitability=Decimal("0"),
                time_context=self.time_ctx,
                adjustments={"stop_multiplier": 1.0},
                reason="流动性真空",
                fr_zscore=fr_zscore,
                oi_change_pct=oi_change_pct,
                liq_volume=liq_volume,
                volume_ratio=volume_ratio,
                dormant_trigger={
                    "成交量比率": (
                        f"{float(volume_ratio):.2f}x"
                        f" < 阈值 {float(self.VOLUME_RATIO_DORMANT):.1f}x（流动性真空）"
                    ),
                },
            )

        # 2. 市场状态组合判断
        if market_stress == "高" and activity_level == "高":
            # 高波动环境，适合交易
            status = "可交易"
            suitability = Decimal("0.9") * fr_reliability
            reason = "高波动环境"
        elif market_stress == "低" and activity_level == "低":
            # 市场沉寂（无清算 + 无OI动能），休眠
            return EnvironmentResult(
                status="休眠",
                direction_bias="中性",
                liquidation_side=None,
                suitability=Decimal("0"),
                time_context=self.time_ctx,
                adjustments={"stop_multiplier": 1.0},
                reason="市场沉寂",
                fr_zscore=fr_zscore,
                oi_change_pct=oi_change_pct,
                liq_volume=liq_volume,
                volume_ratio=volume_ratio,
                dormant_trigger={
                    "清算量(30m)": (
                        f"${float(liq_volume):,.0f} USDT"
                        f" < 阈值 ${float(self.LIQ_VOLUME_LOW):,.0f}（极低）"
                    ),
                    "OI活跃度(1h)": (
                        f"{float(oi_change_pct):.3%}"
                        f" < 阈值 ±{float(self.OI_CHANGE_LOW):.0%}（低动能）"
                    ),
                },
            )
        else:
            # 正常环境
            status = "可交易"
            suitability = Decimal("0.7") * fr_reliability
            reason = "正常环境"

        # 3. 根据方向偏向调整适合度
        if direction_bias == "中性":
            suitability = suitability * Decimal("0.8")

        # 4. 计算止损乘数
        stop_mult = self._calculate_stop_multiplier(
            market_stress=market_stress,
            activity_level=activity_level,
            volume_ratio=volume_ratio
        )

        adjustments: Dict[str, Any] = {
            "stop_multiplier": float(stop_mult),
            "activity_level": activity_level,
            "market_stress": market_stress,
        }
        if vol_state == "reduced":
            adjustments["position_multiplier"] = 0.5

        return EnvironmentResult(
            status=status,
            direction_bias=direction_bias,
            liquidation_side=liquidation_side,
            suitability=suitability,
            time_context=self.time_ctx,
            adjustments=adjustments,
            reason=reason,
            fr_zscore=fr_zscore,
            oi_change_pct=oi_change_pct,
            liq_volume=liq_volume,
            volume_ratio=volume_ratio,
        )

    def _calculate_stop_multiplier(
        self,
        market_stress: str,
        activity_level: str,
        volume_ratio: Decimal
    ) -> Decimal:
        """
        计算止损乘数

        波动越大，止损越宽
        """
        base_mult = Decimal("1.0")

        # 市场压力调整
        if market_stress == "高":
            base_mult = base_mult + Decimal("0.3")
        elif market_stress == "低":
            base_mult = base_mult - Decimal("0.2")

        # 活跃度调整
        if activity_level == "高":
            base_mult = base_mult + Decimal("0.2")
        elif activity_level == "低":
            base_mult = base_mult - Decimal("0.1")

        # 成交量调整 (成交量大时可以承受更宽止损)
        if volume_ratio > Decimal("1.5"):
            base_mult = base_mult + Decimal("0.1")
        elif volume_ratio < Decimal("0.7"):
            base_mult = base_mult - Decimal("0.1")

        # 限制范围
        stop_mult = max(Decimal("0.8"), min(Decimal("1.3"), base_mult))

        logger.debug(f"止损乘数: stress={market_stress}, "
                    f"activity={activity_level}, "
                    f"result={stop_mult}")

        return stop_mult


def evaluate_environment() -> EnvironmentResult:
    """
    便捷函数：评估当前市场环境

    Returns:
        EnvironmentResult: 环境评估结果
    """
    evaluator = EnvironmentEvaluator()
    return evaluator.evaluate()


if __name__ == "__main__":
    # 测试
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    result = evaluate_environment()

    print("\n" + "=" * 60)
    print("环境评估结果")
    print("=" * 60)
    print(f"状态: {result.status}")
    print(f"方向偏向: {result.direction_bias}")
    print(f"清算方: {result.liquidation_side}")
    print(f"适合度: {result.suitability:.2f}")
    print(f"原因: {result.reason}")
    print(f"调整参数: {result.adjustments}")
    print("=" * 60)
