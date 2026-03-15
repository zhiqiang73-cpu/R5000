# config/params.py
# 所有参数集中管理，带验证状态

from enum import Enum
from dataclasses import dataclass
from typing import Optional
from datetime import datetime


class ParamStatus(Enum):
    UNVERIFIED = "UNVERIFIED"    # 未验证，纯猜测
    LOGICAL = "LOGICAL"          # 有逻辑依据，未用数据验证
    BACKTESTED = "BACKTESTED"    # 回测验证过
    VALIDATED = "VALIDATED"      # 样本外验证通过


@dataclass
class Param:
    value: float
    status: ParamStatus
    source: str
    validation_method: str
    last_validated: Optional[datetime] = None
    notes: str = ""


# ═══════════════════════════════════════════════════════════════════
# 第1层参数：环境评估
# ═══════════════════════════════════════════════════════════════════

LAYER1_PARAMS = {
    "liquidity_hibernate_threshold": Param(
        value=0.20,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="统计低流动性时段的滑点和成交成本，确定影响交易的边界",
        notes="当前成交量/本时段正常成交量 < 此值时休眠"
    ),
    
    "liquidity_reduce_threshold": Param(
        value=0.40,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="同上",
        notes="低于此值时仓位减半"
    ),
    
    "fr_zscore_extreme": Param(
        value=2.0,
        status=ParamStatus.LOGICAL,
        source="统计学惯例：2σ = 95%分位",
        validation_method="验证BTC资金费率分布是否正态，2σ是否合适作为极端定义",
        notes="FR Z-Score超过此值视为极端"
    ),
    
    "oi_change_threshold": Param(
        value=0.03,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="统计OI变化分布，确定异常边界",
        notes="1小时OI变化超过3%视为活跃"
    ),
    
    "stop_multiplier_high_vol": Param(
        value=1.3,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="回测不同倍数的存活率和盈亏",
        notes="高波动时止损放宽到1.3倍"
    ),
    
    "stop_multiplier_low_vol": Param(
        value=0.8,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="同上",
        notes="低波动时止损收紧到0.8倍"
    ),
}


# ═══════════════════════════════════════════════════════════════════
# 第2层参数：冲击检测 + 分类
# ═══════════════════════════════════════════════════════════════════

LAYER2_PARAMS = {
    "impact_threshold_pct": Param(
        value=0.15,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="统计30秒价格变动分布，确定异常边界（如75分位）",
        notes="30秒内价格变动超过0.15%视为冲击，需要动态调整"
    ),
    
    "volume_surge_ratio": Param(
        value=2.0,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="统计30秒成交量分布，确定激增定义",
        notes="成交量超过基准2倍视为激增"
    ),
    
    "liq_count_true_breakout": Param(
        value=5,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="【核心】统计冲击后清算数量分布，验证>=5时趋势延续率",
        notes="⭐ 核心假设，必须优先验证"
    ),
    
    "liq_ratio_true_breakout": Param(
        value=0.15,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="【核心】验证清算占比与趋势延续的关系",
        notes="⭐ 核心假设"
    ),
    
    "liq_count_overreaction": Param(
        value=2,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="【核心】验证<=2笔清算时回归率",
        notes="⭐ 核心假设"
    ),
    
    "liq_ratio_overreaction": Param(
        value=0.05,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="【核心】验证低清算占比与回归的关系",
        notes="⭐ 核心假设"
    ),
    
    "observe_window_seconds": Param(
        value=45,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="测试不同窗口（30、45、60、90秒）的分类准确率",
        notes="冲击后等待观察的时间"
    ),
}


# ═══════════════════════════════════════════════════════════════════
# 第3层参数：执行 + 风控
# ═══════════════════════════════════════════════════════════════════

LAYER3_PARAMS = {
    "rr_ratio_mean_reversion": Param(
        value=1.3,
        status=ParamStatus.LOGICAL,
        source="手续费覆盖计算：0.15%成本，50%胜率需盈亏比>1.3",
        validation_method="验证真实胜率后重新计算",
        notes="均值回归最小盈亏比要求"
    ),
    
    "rr_ratio_trend_follow": Param(
        value=1.8,
        status=ParamStatus.LOGICAL,
        source="同上，趋势跟随胜率假设较低",
        validation_method="验证真实胜率后重新计算",
        notes="趋势跟随最小盈亏比要求"
    ),
    
    "time_stop_mean_reversion": Param(
        value=180,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="统计均值回归完成时间分布",
        notes="均值回归时间止损：3分钟"
    ),
    
    "time_stop_trend_follow": Param(
        value=600,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="统计趋势持续时间分布",
        notes="趋势跟随时间止损：10分钟"
    ),
    
    "pullback_entry_pct": Param(
        value=0.25,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="测试不同回调比例（15%、25%、35%）的成功率",
        notes="趋势跟随等回调25%入场"
    ),
    
    "risk_pct_grade_a": Param(
        value=0.015,
        status=ParamStatus.UNVERIFIED,
        source="凯利公式估算",
        validation_method="确定真实胜率和盈亏比后重新计算",
        notes="A级信号风险1.5%"
    ),
    
    "risk_pct_grade_b": Param(
        value=0.010,
        status=ParamStatus.UNVERIFIED,
        source="同上",
        validation_method="同上",
        notes="B级信号风险1.0%"
    ),
    
    "risk_pct_grade_c": Param(
        value=0.005,
        status=ParamStatus.UNVERIFIED,
        source="同上",
        validation_method="同上",
        notes="C级信号风险0.5%"
    ),
}


# ═══════════════════════════════════════════════════════════════════
# 风控参数
# ═══════════════════════════════════════════════════════════════════

RISK_PARAMS = {
    "daily_loss_limit": Param(
        value=0.03,
        status=ParamStatus.LOGICAL,
        source="风险偏好决定",
        validation_method="回测确认此限制下的长期生存率",
        notes="日亏损超过3%停止交易"
    ),
    
    "consecutive_loss_limit": Param(
        value=3,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="统计连续止损后继续交易的表现",
        notes="连续3笔止损后暂停"
    ),
    
    "low_winrate_threshold": Param(
        value=0.30,
        status=ParamStatus.UNVERIFIED,
        source="猜测",
        validation_method="同上",
        notes="最近10笔胜率低于30%暂停"
    ),
    
    "max_leverage": Param(
        value=3.0,
        status=ParamStatus.LOGICAL,
        source="保守风险管理",
        validation_method="回测不同杠杆的风险收益",
        notes="最大杠杆3倍"
    ),
}


# ═══════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════

ALL_PARAMS = {
    **LAYER1_PARAMS,
    **LAYER2_PARAMS,
    **LAYER3_PARAMS,
    **RISK_PARAMS,
}


def get_param(name: str, warn: bool = True) -> float:
    """获取参数值，未验证参数会打印警告"""
    if name not in ALL_PARAMS:
        raise KeyError(f"未知参数: {name}")
    
    param = ALL_PARAMS[name]
    
    if warn and param.status == ParamStatus.UNVERIFIED:
        print(f"⚠️  WARNING: 参数 {name}={param.value} 未经验证")
    
    return param.value


def check_all_params():
    """检查所有参数状态，生成报告"""
    
    unverified = []
    logical = []
    backtested = []
    validated = []
    
    for name, param in ALL_PARAMS.items():
        entry = f"  {name}: {param.value}"
        if param.status == ParamStatus.UNVERIFIED:
            unverified.append(entry)
        elif param.status == ParamStatus.LOGICAL:
            logical.append(entry)
        elif param.status == ParamStatus.BACKTESTED:
            backtested.append(entry)
        elif param.status == ParamStatus.VALIDATED:
            validated.append(entry)
    
    print("\n" + "═" * 60)
    print("参数状态报告")
    print("═" * 60)
    
    print(f"\n[UNVERIFIED] {len(unverified)}个 - ❌ 不可上线")
    for entry in unverified:
        print(entry)
    
    print(f"\n[LOGICAL] {len(logical)}个 - ⚠️ 谨慎使用")
    for entry in logical:
        print(entry)
    
    print(f"\n[BACKTESTED] {len(backtested)}个 - ⚠️ 需样本外验证")
    for entry in backtested:
        print(entry)
    
    print(f"\n[VALIDATED] {len(validated)}个 - ✅ 可以上线")
    for entry in validated:
        print(entry)
    
    print("\n" + "═" * 60)
    
    if unverified:
        print(f"⚠️  警告：{len(unverified)}个参数未验证，不可上线")
    else:
        print("✅ 所有参数已验证")
    
    print("═" * 60 + "\n")


def get_core_assumptions():
    """返回需要优先验证的核心假设"""
    core = [
        "liq_count_true_breakout",
        "liq_ratio_true_breakout", 
        "liq_count_overreaction",
        "liq_ratio_overreaction",
        "impact_threshold_pct",
    ]
    
    print("\n⭐ 核心假设（必须优先验证）：\n")
    for name in core:
        param = ALL_PARAMS[name]
        print(f"  {name}")
        print(f"    当前值: {param.value}")
        print(f"    状态: {param.status.value}")
        print(f"    验证方法: {param.validation_method}")
        print()


if __name__ == "__main__":
    check_all_params()
    get_core_assumptions()
