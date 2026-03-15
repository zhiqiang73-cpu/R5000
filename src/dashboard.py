# -*- coding: utf-8 -*-
"""
BTCUSDT 量化交易系统 - 综合监控仪表盘
涵盖：Layer 1 环境过滤 / Layer 2 冲击检测 / Layer 3 信号 / 数据采集状态

运行方式（从项目根目录）:
    python -m streamlit run src/dashboard.py
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

# ── 路径 ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent          # 项目根目录
SRC  = Path(__file__).parent                 # src/
sys.path.insert(0, str(ROOT))                # 使 from src.xxx import 可用

from src.layers.environment import evaluate_environment, EnvironmentEvaluator  # noqa: E402
from src.backtest.engine import EventDrivenBacktestEngine                       # noqa: E402

# ── 页面配置 ─────────────────────────────────────────────────────────────────
st.set_page_config(
    layout="wide",
    page_title="BTCUSDT 量化系统",
    page_icon="🎯",
)

# ── Session State 初始化 ──────────────────────────────────────────────────────
for key, default in [
    ("fr_zscore_history",   pd.DataFrame({"time": pd.Series(dtype="datetime64[ns]"), "fr_zscore": pd.Series(dtype=float)})),
    ("volume_ratio_history", pd.DataFrame({"time": pd.Series(dtype="datetime64[ns]"), "volume_ratio": pd.Series(dtype=float)})),
    ("last_backtest_time", 0),
    ("backtest_cache", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=30)
def get_data_status() -> dict:
    """读取本地数据文件状态（30s 缓存）"""
    raw = ROOT / "data" / "raw"
    trade_days = []
    all_trade_files = []

    trade_root = raw / "trades"
    if trade_root.exists():
        for day_dir in sorted(trade_root.iterdir(), reverse=True):
            if not day_dir.is_dir():
                continue
            files = sorted(day_dir.glob("*.jsonl"))
            all_trade_files.extend(files)
            total_bytes = sum(f.stat().st_size for f in files)
            latest_mtime = max((f.stat().st_mtime for f in files), default=0)
            trade_days.append({
                "日期": day_dir.name,
                "文件数": len(files),
                "大小": f"{total_bytes / 1024 / 1024:.1f} MB",
                "最后写入": datetime.fromtimestamp(latest_mtime).strftime("%H:%M:%S") if latest_mtime else "-",
                "_mtime": latest_mtime,
            })

    last_mtime = max((f.stat().st_mtime for f in all_trade_files), default=0)

    liq_root = raw / "liquidations"
    liq_days = []
    if liq_root.exists():
        for f in sorted(liq_root.glob("*.jsonl"), reverse=True):
            total_lines = btc_count = 0
            try:
                with open(f, encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        total_lines += 1
                        try:
                            r = json.loads(line)
                            d = r.get("data", r)
                            o = d.get("o", {})
                            if o.get("s") == "BTCUSDT":
                                btc_count += 1
                        except Exception:
                            pass
            except Exception:
                pass
            liq_days.append({
                "日期": f.stem,
                "总行数（全币种）": total_lines,
                "BTCUSDT 清算": btc_count,
                "大小": f"{f.stat().st_size / 1024:.1f} KB",
            })

    return {
        "trade_days": trade_days,
        "liq_days": liq_days,
        "last_trade_mtime": last_mtime,
    }


@st.cache_data(ttl=300)
def run_today_backtest() -> dict | None:
    """运行今日回测分析（5 分钟缓存）"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        engine = EventDrivenBacktestEngine()
        dataset = engine.load_data(today, today)
        if dataset.is_empty():
            return None
        return engine.run_backtest(dataset=dataset)
    except Exception as exc:
        return {"error": str(exc)}


def _fmt_ts(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%H:%M:%S")


# ══════════════════════════════════════════════════════════════════════════════
# 数据获取（每次 rerun 都执行）
# ══════════════════════════════════════════════════════════════════════════════
data_status = get_data_status()
last_mtime   = data_status["last_trade_mtime"]
seconds_ago  = time.time() - last_mtime if last_mtime else 999_999

# WebSocket 状态徽章
if seconds_ago < 120:
    ws_badge  = "🟢 采集中"
    ws_detail = f"{int(seconds_ago)}s 前收到数据"
elif seconds_ago < 600:
    ws_badge  = "🟡 可能断开"
    ws_detail = f"{int(seconds_ago // 60)} 分钟前"
else:
    ws_badge  = "🔴 未运行"
    ws_detail = "超过 10 分钟无数据"

# Layer 1 评估
try:
    env_result = evaluate_environment()
    tc         = env_result.time_context
    env_ok     = True
    env_error  = ""
except Exception as exc:
    env_result = None
    tc         = None
    env_ok     = False
    env_error  = str(exc)

# 今日回测（缓存）
analysis = run_today_backtest()

# ══════════════════════════════════════════════════════════════════════════════
# 顶部全局状态栏
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("## 🎯 BTCUSDT 量化交易系统")
hc1, hc2, hc3, hc4, hc5 = st.columns(5)
hc1.metric("数据采集", ws_badge, ws_detail, delta_color="off")
if env_ok and env_result:
    status_icon = "🟢" if env_result.status == "可交易" else "🔴"
    hc2.metric("Layer 1 状态", f"{status_icon} {env_result.status}")
    hc3.metric("方向偏向", env_result.direction_bias)
    hc4.metric("适宜度", f"{float(env_result.suitability):.0%}")
    if tc:
        h = tc.hours_to_funding
        note = "⚠ 结算前" if h < 2 else ("↩ 刚结算" if h > 6 else "✓ 正常")
        hc5.metric("FR 结算", f"{h:.1f}h", note, delta_color="off")
else:
    hc2.metric("Layer 1 状态", "⚠ 错误")
    hc3.metric("方向偏向", "--")
    hc4.metric("适宜度", "--")
    hc5.metric("FR 结算", "--")

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# 主标签页
# ══════════════════════════════════════════════════════════════════════════════
tab_overview, tab_layer1, tab_signals, tab_data = st.tabs([
    "📊 总览", "🌍 环境过滤 (L1)", "📡 冲击信号 (L2/L3)", "💾 数据状态"
])


# ─────────────────────────────────────────────────────────────────────────────
# Tab 1: 总览
# ─────────────────────────────────────────────────────────────────────────────
with tab_overview:
    st.subheader("当日运行摘要")

    oc1, oc2, oc3, oc4 = st.columns(4)
    if analysis and "error" not in analysis:
        m = analysis["metrics"]
        oc1.metric("今日冲击检测", m["impact_count"])
        oc2.metric("今日分类完成", m["classification_count"])
        oc3.metric("今日成交笔数", m["total_trades"])
        pnl_val = m["total_pnl"]
        oc4.metric("今日 PnL", f"{pnl_val:.2f} USDT",
                   delta_color="inverse" if pnl_val < 0 else "normal")
    elif analysis and "error" in analysis:
        st.error(f"回测引擎报错：{analysis['error']}")
        for c in (oc1, oc2, oc3, oc4):
            c.metric("--", "--")
    else:
        for c, label in zip((oc1, oc2, oc3, oc4), ["今日冲击检测", "今日分类完成", "今日成交笔数", "今日 PnL"]):
            c.metric(label, "0")
        st.info("今日暂无交易数据，正在等待数据采集…")

    # 今日最近冲击
    if analysis and "error" not in analysis and analysis.get("classifications"):
        st.markdown("#### 今日最近冲击事件（最新 10 条）")
        rows = []
        for cls in analysis["classifications"][-10:][::-1]:
            flag = {"真突破": "🔴", "过度反应": "🔵", "不确定": "⚪"}.get(cls["classification"], "")
            rows.append({
                "时间": _fmt_ts(cls["impact_time"]),
                "分类": f"{flag} {cls['classification']}",
                "策略": cls["strategy"],
                "清算数": cls["liq_count"],
                "清算量": f"${cls['liq_value']:,.0f}",
                "信心": f"{cls['confidence']:.2f}",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        st.info("今日尚无冲击分类记录。")

    # 今日策略分布
    if analysis and "error" not in analysis and analysis.get("classifications"):
        from collections import Counter
        dist = Counter(c["classification"] for c in analysis["classifications"])
        total = sum(dist.values())
        st.markdown("#### 分类分布")
        dc1, dc2, dc3 = st.columns(3)
        dc1.metric("🔴 真突破",   dist.get("真突破", 0),   f"占 {dist.get('真突破',0)/total:.0%}")
        dc2.metric("🔵 过度反应", dist.get("过度反应", 0), f"占 {dist.get('过度反应',0)/total:.0%}")
        dc3.metric("⚪ 不确定",   dist.get("不确定", 0),   f"占 {dist.get('不确定',0)/total:.0%}")


# ─────────────────────────────────────────────────────────────────────────────
# Tab 2: Layer 1 环境过滤（完整内容）
# ─────────────────────────────────────────────────────────────────────────────
with tab_layer1:
    if not env_ok or env_result is None:
        st.error(f"Layer 1 评估失败：{env_error}")
        st.stop()

    ev = EnvironmentEvaluator

    # ── KPI 行 ────────────────────────────────────────────────────────────────
    l1c1, l1c2, l1c3, l1c4 = st.columns(4)
    with l1c1:
        icon = "🟢" if env_result.status == "可交易" else "🔴"
        st.metric("交易状态", f"{icon} {env_result.status}")
    with l1c2:
        st.metric("方向偏向", env_result.direction_bias)
    with l1c3:
        st.metric("清算目标", env_result.liquidation_side or "无")
    with l1c4:
        h = tc.hours_to_funding
        note = "⚠ 结算前" if h < 2 else ("↩ 刚结算" if h > 6 else "✓ 正常")
        st.metric("距 FR 结算", f"{h:.2f}h", note, delta_color="off")

    # ── 休眠原因 / 评估理由 ───────────────────────────────────────────────────
    if env_result.status == "休眠" and env_result.dormant_trigger:
        with st.expander(f"🔴 休眠原因：{env_result.reason}", expanded=True):
            cols = st.columns(len(env_result.dormant_trigger))
            for i, (factor, detail) in enumerate(env_result.dormant_trigger.items()):
                with cols[i]:
                    st.error(f"**{factor}**\n\n{detail}")
    else:
        st.metric("评估理由", env_result.reason)

    # ── 适宜度 ────────────────────────────────────────────────────────────────
    st.subheader("环境适宜度")
    suit = float(env_result.suitability)
    st.progress(suit, text=f"适宜度：{suit:.2f}")
    st.markdown("---")

    # ── 指标详情表 ────────────────────────────────────────────────────────────
    st.subheader("时间与市场上下文")

    fr_z_str = f"{float(env_result.fr_zscore):.2f}" if env_result.fr_zscore is not None else "N/A"
    utc_str = datetime.fromtimestamp(tc.utc_now_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    exp = tc.same_hour_expectations
    vol_mean_str = (
        f"{float(exp.vol_mean):,.0f} BTC  (n={exp.sample_count}, {exp.data_source})"
        if exp else "N/A"
    )
    oi_mean_str = (
        f"{float(exp.oi_change_mean) / 1e6:+.1f}M USDT"
        if exp and exp.oi_change_mean != 0 else "N/A"
    )
    fr_rel = (
        "0.6（结算前 2h）" if tc.hours_to_funding < 2
        else "0.4（刚结算完）" if tc.hours_to_funding > 6
        else "1.0（正常）"
    )

    vr = float(env_result.volume_ratio)
    lv = float(env_result.liq_volume)
    oi = float(env_result.oi_change_pct)

    if vr < 0.3:     vol_lbl = "⚠ 休眠 (< 0.3x)"
    elif vr < 0.5:   vol_lbl = "⚡ 仓位减半 (0.3–0.5x)"
    else:            vol_lbl = "✓ 正常 (≥ 0.5x)"

    if lv < float(ev.LIQ_VOLUME_LOW):   liq_lbl = f"⚠ 极低 (< ${float(ev.LIQ_VOLUME_LOW):,.0f})"
    elif lv < float(ev.LIQ_VOLUME_HIGH):liq_lbl = f"✓ 正常"
    else:                                liq_lbl = f"🔥 高 (> ${float(ev.LIQ_VOLUME_HIGH):,.0f})"

    if abs(oi) > float(ev.OI_CHANGE_HIGH):  oi_lbl = f"🔥 高  (> ±{float(ev.OI_CHANGE_HIGH):.0%})"
    elif abs(oi) < float(ev.OI_CHANGE_LOW): oi_lbl = f"⚠ 低  (< ±{float(ev.OI_CHANGE_LOW):.0%})"
    else:                                    oi_lbl = "✓ 正常"

    pm = env_result.adjustments.get("position_multiplier", 1.0)
    pm_str = f"{pm:.1f}x" if pm != 1.0 else "1.0x（全仓）"

    rows = [
        {"指标": "UTC 时间",                       "值": utc_str},
        {"指标": "上海时间",                       "值": tc.shanghai_now_str},
        {"指标": "距下次 FR 结算（小时）",          "值": f"{tc.hours_to_funding:.2f}"},
        {"指标": "FR 可靠性",                      "值": fr_rel},
        {"指标": "FR Z-Score",                    "值": fr_z_str},
        {"指标": "本时段正常成交量（7D 同 UTC 小时均值）", "值": vol_mean_str},
        {"指标": "成交量比率（1H / 本时段均值）",   "值": f"{vr:.2f}x  {vol_lbl}"},
        {"指标": "本时段正常 OI 变化（7D 均值）",   "值": oi_mean_str},
        {"指标": "1H OI 变化率",                   "值": f"{oi:.4%}  {oi_lbl}"},
        {"指标": "近 30 分钟清算量（USDT）",        "值": f"${lv:,.0f}  {liq_lbl}"},
        {"指标": "止损乘数",                       "值": str(env_result.adjustments.get("stop_multiplier", "N/A"))},
        {"指标": "仓位乘数",                       "值": pm_str},
    ]
    st.table(pd.DataFrame(rows))

    # ── 阈值参考 ─────────────────────────────────────────────────────────────
    with st.expander("📐 判断阈值参考（调试用）", expanded=False):
        thresh_rows = [
            {"因素": "成交量比率 → 休眠",       "阈值": f"< {float(ev.VOLUME_RATIO_DORMANT):.1f}x"},
            {"因素": "成交量比率 → 仓位减半",   "阈值": f"{float(ev.VOLUME_RATIO_DORMANT):.1f}x – {float(ev.VOLUME_RATIO_REDUCED):.1f}x"},
            {"因素": "成交量比率 → 正常",       "阈值": f"≥ {float(ev.VOLUME_RATIO_REDUCED):.1f}x"},
            {"因素": "OI 活跃度 → 高",          "阈值": f"> ±{float(ev.OI_CHANGE_HIGH):.0%}"},
            {"因素": "OI 活跃度 → 低",          "阈值": f"< ±{float(ev.OI_CHANGE_LOW):.0%}"},
            {"因素": "清算量(30m) → 高",        "阈值": f"> ${float(ev.LIQ_VOLUME_HIGH):,.0f} USDT"},
            {"因素": "清算量(30m) → 低",        "阈值": f"< ${float(ev.LIQ_VOLUME_LOW):,.0f} USDT"},
            {"因素": "FR Z-Score → 方向极端",   "阈值": f"> ±{float(ev.FR_ZSCORE_EXTREME):.1f}"},
            {"因素": "FR Z-Score → 方向中性",   "阈值": f"< ±{float(ev.FR_ZSCORE_MODERATE):.1f}"},
            {"因素": "FR 可靠性 → 结算前",      "阈值": "距结算 < 2h → 0.6x"},
            {"因素": "FR 可靠性 → 刚结算",      "阈值": "距结算 > 6h → 0.4x"},
            {"因素": "休眠：流动性真空",        "阈值": "成交量比率 < 0.3x（单独触发）"},
            {"因素": "休眠：市场沉寂",          "阈值": "OI 低 AND 清算低（同时满足）"},
        ]
        st.table(pd.DataFrame(thresh_rows))

    # ── 时间约束 ─────────────────────────────────────────────────────────────
    if tc.constraints:
        st.subheader("时间约束")
        for c in tc.constraints:
            st.info(c)

    # ── 历史图表 ─────────────────────────────────────────────────────────────
    now_dt = datetime.now()
    st.session_state["fr_zscore_history"] = pd.concat([
        st.session_state["fr_zscore_history"],
        pd.DataFrame([{"time": now_dt, "fr_zscore": float(env_result.fr_zscore) if env_result.fr_zscore else 0.0}])
    ], ignore_index=True).tail(60)
    st.session_state["volume_ratio_history"] = pd.concat([
        st.session_state["volume_ratio_history"],
        pd.DataFrame([{"time": now_dt, "volume_ratio": float(env_result.volume_ratio)}])
    ], ignore_index=True).tail(60)

    st.subheader("实时数据图表")
    gc1, gc2 = st.columns(2)
    with gc1:
        st.markdown("**资金费率 Z-Score 历史**")
        h_fr = st.session_state["fr_zscore_history"]
        if len(h_fr) > 1:
            st.line_chart(h_fr, x="time", y="fr_zscore")
        else:
            st.info("数据积累中，需等待第二个刷新周期…")
    with gc2:
        st.markdown("**成交量比率历史（1H / 7D 均值）**")
        h_vr = st.session_state["volume_ratio_history"]
        if len(h_vr) > 1:
            st.line_chart(h_vr, x="time", y="volume_ratio")
        else:
            st.info("数据积累中，需等待第二个刷新周期…")


# ─────────────────────────────────────────────────────────────────────────────
# Tab 3: Layer 2 / Layer 3 冲击信号
# ─────────────────────────────────────────────────────────────────────────────
with tab_signals:
    st.subheader("今日冲击检测 & 信号分析")
    st.caption("数据每 5 分钟刷新一次（回测引擎）")

    if analysis is None:
        st.info("今日暂无交易数据，等待数据采集后自动更新…")
    elif "error" in analysis:
        st.error(f"分析报错：{analysis['error']}")
    else:
        m = analysis["metrics"]

        # 汇总指标
        sc1, sc2, sc3, sc4, sc5 = st.columns(5)
        sc1.metric("冲击检测", m["impact_count"])
        sc2.metric("分类完成", m["classification_count"])
        sc3.metric("交易执行", m["total_trades"])
        sc4.metric("胜率", f"{m['win_rate']:.0%}" if m["total_trades"] else "--")
        sc5.metric("Sharpe",  f"{m['sharpe']:.2f}"   if m["total_trades"] else "--")

        st.markdown("---")

        # 冲击事件列表
        if analysis["detected_impacts"]:
            st.markdown("#### 冲击事件列表")
            imp_rows = []
            for item in analysis["detected_impacts"]:
                env = item.get("environment", {})
                imp_rows.append({
                    "时间":     _fmt_ts(item["detected_at_ms"]),
                    "方向":     "↑ 上涨" if item["direction"] == "up" else "↓ 下跌",
                    "价格变化": f"{item['price_change_pct']:.3%}",
                    "激增倍数": f"{item['volume_surge_ratio']:.1f}x",
                    "环境":     env.get("status", "-"),
                    "清算量":   f"${env.get('liq_volume', 0):,.0f}",
                    "成交量比": f"{env.get('volume_ratio', 0):.2f}x",
                })
            st.dataframe(pd.DataFrame(imp_rows), hide_index=True, use_container_width=True)
        else:
            st.info("今日未检测到价格冲击（成交量激增不足或价格变化太小）。")

        # 分类结果
        if analysis["classifications"]:
            st.markdown("#### 冲击分类详情（Layer 2）")
            cls_rows = []
            for item in analysis["classifications"]:
                flag = {"真突破": "🔴", "过度反应": "🔵", "不确定": "⚪"}.get(item["classification"], "")
                cls_rows.append({
                    "时间":     _fmt_ts(item["impact_time"]),
                    "分类":     f"{flag} {item['classification']}",
                    "策略":     item["strategy"],
                    "清算笔数": item["liq_count"],
                    "清算金额": f"${item['liq_value']:,.0f}",
                    "清算占比": f"{item['liq_ratio']:.4%}",
                    "信心":     f"{item['confidence']:.2f}",
                    "CVD跟随":  "✓" if item["cvd_follows"] else "✗",
                })
            st.dataframe(pd.DataFrame(cls_rows), hide_index=True, use_container_width=True)

        # 交易记录
        if analysis["trades"]:
            st.markdown("#### 今日交易记录（Layer 3）")
            trade_rows = []
            for t in analysis["trades"]:
                pnl = t["pnl"]
                trade_rows.append({
                    "策略":     t["strategy"],
                    "方向":     t["side"],
                    "等级":     t["grade"],
                    "类型":     t["entry_type"],
                    "入场价":   f"{t['entry_price']:.1f}",
                    "出场价":   f"{t['exit_price']:.1f}",
                    "数量":     f"{t['quantity']:.4f} BTC",
                    "PnL":      f"{'🟢' if pnl >= 0 else '🔴'} {pnl:+.2f} USDT",
                    "出场原因": t["exit_reason"],
                    "计划 RR":  f"{t['planned_rr']:.2f}",
                })
            st.dataframe(pd.DataFrame(trade_rows), hide_index=True, use_container_width=True)

            # 回测指标
            st.markdown("#### 当日回测指标")
            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("总 PnL",       f"{m['total_pnl']:.2f} USDT")
            mc2.metric("总收益率",      f"{m['total_return_pct']:.2%}")
            mc3.metric("最大回撤",      f"{m['max_drawdown']:.2%}")
            mc4.metric("平均 RR",       f"{m['avg_rr']:.2f}")
        else:
            # 无交易时给出诊断说明
            with st.expander("ℹ️ 为什么今日没有成交？", expanded=True):
                if m["impact_count"] == 0:
                    st.write("**原因：** 未检测到价格冲击。")
                    st.write("• 当前成交量激增倍数 < 2.0x，或价格变化 < 0.15%")
                    st.write("• 周末 / 假日市场活跃度低，属于正常现象")
                elif m["classification_count"] == 0:
                    st.write("**原因：** 检测到冲击，但 45s 分类窗口内数据不足。")
                else:
                    st.write("**原因：** 分类完成，但信号被过滤：")
                    st.write("• **环境休眠**：Layer 1 判断市场活跃度不足")
                    st.write("• **RR 不足**：均值回归策略要求 RR ≥ 1.3，趋势跟随要求 ≥ 1.8")
                    st.write("• **价格恢复过快**：均值回归等 45s 后价格已大幅回归，偏离不足 0.04%")
                    st.write("• **限价单未成交**：趋势跟随挂 LIMIT 单，在 180s 内未触碰入场价")


# ─────────────────────────────────────────────────────────────────────────────
# Tab 4: 数据采集状态
# ─────────────────────────────────────────────────────────────────────────────
with tab_data:
    st.subheader("数据采集状态")

    # WebSocket 健康状态
    if seconds_ago < 120:
        st.success(f"✅ WebSocket 正常运行（{int(seconds_ago)}s 前收到数据）")
    elif seconds_ago < 600:
        st.warning(f"⚠️ WebSocket 可能断开（{int(seconds_ago // 60)} 分钟前）")
    else:
        st.error("❌ WebSocket 未运行（超过 10 分钟无新数据）")
        st.code("python -m src.data.websocket --streams liquidations,trades", language="bash")

    st.markdown("---")

    # 逐笔成交数据
    st.subheader("逐笔成交数据（aggTrade）")
    if data_status["trade_days"]:
        clean = [{k: v for k, v in d.items() if k != "_mtime"} for d in data_status["trade_days"]]
        st.dataframe(pd.DataFrame(clean), hide_index=True, use_container_width=True)
        total_mb = sum(
            float(d["大小"].split()[0]) for d in clean if d["大小"] != "-"
        )
        st.caption(f"本地存储合计：{total_mb:.1f} MB")
    else:
        st.info("暂无数据，请先启动 WebSocket 采集器。")

    st.markdown("---")

    # 清算数据
    st.subheader("清算数据（Liquidation Stream）")
    if data_status["liq_days"]:
        st.dataframe(pd.DataFrame(data_status["liq_days"]), hide_index=True, use_container_width=True)
        st.caption("清算流为全市场（所有币种），回测引擎只使用 BTCUSDT 部分。")
    else:
        st.info("暂无清算数据。")

    st.markdown("---")

    # 说明
    with st.expander("📖 数据说明", expanded=False):
        st.markdown("""
**aggTrade（逐笔成交）**
- 来源：`wss://fstream.binance.com/ws/btcusdt@aggTrade`
- 存储：`data/raw/trades/YYYY-MM-DD/HH.jsonl`（按 UTC 小时分割）
- 用途：冲击检测、环境评估

**清算流（Liquidation）**
- 来源：`wss://fstream.binance.com/ws/!forceOrder@arr`（全市场）
- 存储：`data/raw/liquidations/YYYY-MM-DD.jsonl`
- 用途：冲击分类（真突破 vs 过度反应）

**注意：** 历史清算数据 Binance 不提供存档，只能通过 WebSocket 实时采集。
因此回测只能使用 WebSocket 开始采集后的数据。
        """)


# ══════════════════════════════════════════════════════════════════════════════
# 自动刷新（30 秒）
# ══════════════════════════════════════════════════════════════════════════════
time.sleep(30)
st.rerun()
