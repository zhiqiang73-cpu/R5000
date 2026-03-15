# -*- coding: utf-8 -*-
"""
Layer 1 环境过滤仪表盘 (Streamlit)

运行方式（从项目根目录）:
    python -m streamlit run src/layers/layer1_frontend.py
"""

import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import pandas as pd

# 将 src/ 目录加入路径，确保包内相对导入（from .time_context import ...）正常工作
sys.path.insert(0, str(Path(__file__).parent.parent))

from layers.environment import evaluate_environment, EnvironmentEvaluator  # noqa: E402

# ── 页面配置 ───────────────────────────────────────────────────────────
st.set_page_config(
    layout="wide",
    page_title="Layer 1: 环境过滤仪表盘",
    page_icon="📊"
)
st.title("Layer 1: 环境过滤仪表盘")

# ── Session State 初始化（历史图表数据）──────────────────────────────
if "fr_zscore_history" not in st.session_state:
    st.session_state["fr_zscore_history"] = pd.DataFrame(
        columns=["time", "fr_zscore"]
    )
if "volume_ratio_history" not in st.session_state:
    st.session_state["volume_ratio_history"] = pd.DataFrame(
        columns=["time", "volume_ratio"]
    )

# ── 获取当前环境评估 ──────────────────────────────────────────────────
result = evaluate_environment()
tc = result.time_context

# ── KPI 指标行（第一行：状态 + 方向 + 清算目标 + FR结算倒计时）────────
col1, col2, col3, col4 = st.columns(4)
with col1:
    status_icon = "🟢" if result.status == "可交易" else "🔴"
    st.metric(label="交易状态", value=f"{status_icon} {result.status}")
with col2:
    st.metric(label="方向偏向", value=result.direction_bias)
with col3:
    st.metric(label="清算目标", value=result.liquidation_side or "无")
with col4:
    h = tc.hours_to_funding
    fr_countdown = f"{h:.2f}h"
    fr_note = "⚠ 结算前" if h < 2 else ("↩ 刚结算" if h > 6 else "✓ 正常")
    st.metric(label="距FR结算", value=fr_countdown, delta=fr_note, delta_color="off")

# ── 评估理由 + 休眠触发详情 ───────────────────────────────────────────
if result.status == "休眠" and result.dormant_trigger:
    # 展开显示具体触发因素
    with st.expander(f"🔴 休眠原因：{result.reason}", expanded=True):
        cols = st.columns(len(result.dormant_trigger))
        for i, (factor, detail) in enumerate(result.dormant_trigger.items()):
            with cols[i]:
                st.error(f"**{factor}**\n\n{detail}")
else:
    st.metric(label="评估理由", value=result.reason)

# ── 适宜度进度条 ──────────────────────────────────────────────────────
st.header("环境适宜度")
suitability_float = float(result.suitability)
st.progress(suitability_float, text=f"适宜度: {suitability_float:.2f}")

st.markdown("---")

# ── 时间与市场上下文表格 ──────────────────────────────────────────────
st.header("时间与市场上下文")

fr_zscore_str = (
    f"{float(result.fr_zscore):.2f}" if result.fr_zscore is not None else "N/A"
)
utc_readable = datetime.utcfromtimestamp(
    tc.utc_now_ms / 1000
).strftime("%Y-%m-%d %H:%M:%S UTC")

exp = tc.same_hour_expectations
vol_mean_str = (
    f"{float(exp.vol_mean):,.0f} BTC"
    f"  (n={exp.sample_count}, {exp.data_source})"
    if exp is not None else "N/A"
)
oi_change_mean_str = (
    f"{float(exp.oi_change_mean)/1e6:+.1f}M USDT"
    if exp is not None and exp.oi_change_mean != 0 else "N/A"
)
fr_reliability_str = (
    "0.6 (结算前2h)" if tc.hours_to_funding < 2
    else "0.4 (刚结算完)" if tc.hours_to_funding > 6
    else "1.0 (正常)"
)

# 成交量状态标签（含阈值）
vol_ratio_float = float(result.volume_ratio)
if vol_ratio_float < 0.3:
    vol_ratio_label = "⚠ 休眠 (< 0.3x)"
elif vol_ratio_float < 0.5:
    vol_ratio_label = "⚡ 仓位减半 (0.3–0.5x)"
else:
    vol_ratio_label = "✓ 正常 (≥ 0.5x)"

# 清算量状态标签（含阈值）
liq_vol_float = float(result.liq_volume)
ev = EnvironmentEvaluator
if liq_vol_float < float(ev.LIQ_VOLUME_LOW):
    liq_label = f"⚠ 极低 (< ${float(ev.LIQ_VOLUME_LOW):,.0f})"
elif liq_vol_float < float(ev.LIQ_VOLUME_HIGH):
    liq_label = f"✓ 正常 ({float(ev.LIQ_VOLUME_LOW)/1000:.0f}k–{float(ev.LIQ_VOLUME_HIGH)/1000:.0f}k)"
else:
    liq_label = f"🔥 高 (> ${float(ev.LIQ_VOLUME_HIGH):,.0f})"

# OI活跃度标签（含阈值）
oi_pct_float = float(result.oi_change_pct)
if abs(oi_pct_float) > float(ev.OI_CHANGE_HIGH):
    oi_label = f"🔥 高 (> ±{float(ev.OI_CHANGE_HIGH):.0%})"
elif abs(oi_pct_float) < float(ev.OI_CHANGE_LOW):
    oi_label = f"⚠ 低 (< ±{float(ev.OI_CHANGE_LOW):.0%})"
else:
    oi_label = f"✓ 正常 ({float(ev.OI_CHANGE_LOW):.0%}–{float(ev.OI_CHANGE_HIGH):.0%})"

# 仓位乘数
pos_mult = result.adjustments.get("position_multiplier", 1.0)
pos_mult_str = f"{pos_mult:.1f}x" if pos_mult != 1.0 else "1.0x (全仓)"

context_rows = [
    {"指标": "UTC 时间",                          "值": utc_readable},
    {"指标": "上海时间",                          "值": tc.shanghai_now_str},
    {"指标": "距下次资金费率结算 (小时)",           "值": f"{tc.hours_to_funding:.2f}"},
    {"指标": "FR 可靠性",                         "值": fr_reliability_str},
    {"指标": "FR Z-Score",                        "值": fr_zscore_str},
    {"指标": "本时段正常成交量 (7D同UTC小时均值)",   "值": vol_mean_str},
    {"指标": "成交量比率 (1H / 本时段均值)",        "值": f"{vol_ratio_float:.2f}x  {vol_ratio_label}"},
    {"指标": "本时段正常OI变化 (7D同UTC小时均值)",   "值": oi_change_mean_str},
    {"指标": "1H OI 变化率",                      "值": f"{oi_pct_float:.4%}  {oi_label}"},
    {"指标": "近30分钟清算量 (USDT)",              "值": f"${liq_vol_float:,.0f}  {liq_label}"},
    {"指标": "止损乘数",                          "值": f"{result.adjustments.get('stop_multiplier', 'N/A')}"},
    {"指标": "仓位乘数",                          "值": pos_mult_str},
]
st.table(pd.DataFrame(context_rows))

# ── 判断阈值参考 (可折叠) ──────────────────────────────────────────────
with st.expander("📐 判断阈值参考（调试用）", expanded=False):
    threshold_rows = [
        {"因素": "成交量比率 → 休眠",        "阈值": f"< {float(ev.VOLUME_RATIO_DORMANT):.1f}x"},
        {"因素": "成交量比率 → 仓位减半",    "阈值": f"{float(ev.VOLUME_RATIO_DORMANT):.1f}x – {float(ev.VOLUME_RATIO_REDUCED):.1f}x"},
        {"因素": "成交量比率 → 正常",        "阈值": f"≥ {float(ev.VOLUME_RATIO_REDUCED):.1f}x"},
        {"因素": "OI活跃度 → 高",            "阈值": f"> ±{float(ev.OI_CHANGE_HIGH):.0%}"},
        {"因素": "OI活跃度 → 低",            "阈值": f"< ±{float(ev.OI_CHANGE_LOW):.0%}"},
        {"因素": "清算量(30m) → 高",         "阈值": f"> ${float(ev.LIQ_VOLUME_HIGH):,.0f} USDT"},
        {"因素": "清算量(30m) → 低",         "阈值": f"< ${float(ev.LIQ_VOLUME_LOW):,.0f} USDT"},
        {"因素": "FR Z-Score → 方向极端",    "阈值": f"> ±{float(ev.FR_ZSCORE_EXTREME):.1f}"},
        {"因素": "FR Z-Score → 方向中性",    "阈值": f"< ±{float(ev.FR_ZSCORE_MODERATE):.1f}"},
        {"因素": "FR可靠性 → 结算前",        "阈值": "距结算 < 2h → 0.6x"},
        {"因素": "FR可靠性 → 刚结算",        "阈值": "距结算 > 6h → 0.4x"},
        {"因素": "休眠触发：市场沉寂",       "阈值": "OI低 AND 清算低（同时满足）"},
        {"因素": "休眠触发：流动性真空",     "阈值": "成交量比率 < 0.3x（单独触发）"},
    ]
    st.table(pd.DataFrame(threshold_rows))

# ── 时间约束 ──────────────────────────────────────────────────────────
if tc.constraints:
    st.header("时间约束")
    for constraint in tc.constraints:
        st.info(constraint)

# ── 历史图表 ──────────────────────────────────────────────────────────
current_time = datetime.now()

new_fr_row = pd.DataFrame([{
    "time": current_time,
    "fr_zscore": float(result.fr_zscore) if result.fr_zscore is not None else 0.0,
}])
st.session_state["fr_zscore_history"] = pd.concat(
    [st.session_state["fr_zscore_history"], new_fr_row], ignore_index=True
).tail(50)

new_vol_row = pd.DataFrame([{
    "time": current_time,
    "volume_ratio": float(result.volume_ratio),
}])
st.session_state["volume_ratio_history"] = pd.concat(
    [st.session_state["volume_ratio_history"], new_vol_row], ignore_index=True
).tail(50)

st.header("实时数据图表")
col_chart1, col_chart2 = st.columns(2)

with col_chart1:
    st.subheader("资金费率 Z-Score 历史")
    history = st.session_state["fr_zscore_history"]
    if len(history) > 1:
        st.line_chart(history, x="time", y="fr_zscore")
    else:
        st.info("数据积累中，首次运行需等待第二个周期...")

with col_chart2:
    st.subheader("成交量比率历史 (1H / 7D均值)")
    history = st.session_state["volume_ratio_history"]
    if len(history) > 1:
        st.line_chart(history, x="time", y="volume_ratio")
    else:
        st.info("数据积累中，首次运行需等待第二个周期...")

# ── 10 秒自动刷新 ─────────────────────────────────────────────────────
time.sleep(10)
st.rerun()
