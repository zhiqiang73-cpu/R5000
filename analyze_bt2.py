"""Deeper analysis of backtest results — check environment bias vs trade direction."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

with open("bt_result.json", encoding="utf-8") as f:
    data = json.load(f)

print("=== Trade-Level Environment Analysis ===")
for i, t in enumerate(data["trades"]):
    impact_ts = t["impact_time"]
    matched_impact = None
    for imp in data.get("detected_impacts", []):
        if imp["detected_at_ms"] == impact_ts:
            matched_impact = imp
            break
    env = matched_impact["environment"] if matched_impact else {}
    print(f"\n  #{i+1} {t['strategy']} {t['side']} grade={t['grade']}")
    print(f"      env_bias={env.get('direction_bias','?')} liq_side={env.get('liquidation_side','?')} stress={env.get('adjustments',{}).get('market_stress','?')}")
    print(f"      volume_ratio={env.get('volume_ratio',0):.2f}x status={env.get('status','?')}")
    print(f"      pnl=${t['pnl']:.2f} exit={t['exit_reason']}")

print("\n=== Classification Breakdown by Type ===")
for c in data.get("classifications", []):
    liq_c = c.get("liq_count", 0)
    liq_v = float(c.get("liq_value", 0))
    liq_r = float(c.get("liq_ratio", 0))
    print(f"  {c['classification']:6s} dir={c['impact']['direction']:4s} liq_count={liq_c:2d} liq_value=${liq_v:>10,.0f} liq_ratio={liq_r:.6f} conf={float(c.get('confidence',0)):.2f}")
