"""Debug why trade #6 isn't being filtered by direction conflict."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

with open("bt_result.json", encoding="utf-8") as f:
    data = json.load(f)

trade = data["trades"][5]
impact_ts = trade["impact_time"]
print(f"Trade #6: {trade['strategy']} {trade['side']} grade={trade['grade']}")
print(f"  impact_time={impact_ts}")

for c in data.get("classifications", []):
    if c.get("impact_time") == impact_ts or c.get("impact", {}).get("detected_at_ms") == impact_ts:
        print(f"  Classification: {c['classification']}, conf={c.get('confidence')}")
        print(f"  liq_count={c.get('liq_count')}, liq_value={c.get('liq_value')}")
        break

for imp in data.get("detected_impacts", []):
    if imp["detected_at_ms"] == impact_ts:
        env = imp["environment"]
        print(f"  env_status={env.get('status')}")
        print(f"  env_direction_bias={env.get('direction_bias')}")
        print(f"  env_liquidation_side={env.get('liquidation_side')}")
        print(f"  env_volume_ratio={env.get('volume_ratio')}")
        print(f"  env_oi_change_pct={env.get('oi_change_pct')}")
        print(f"  env_adjustments={env.get('adjustments')}")

        # simulate conflict check
        side = trade["side"]
        expected_bias = "偏多" if side == "BUY" else "偏空"
        has_conflict = (env["direction_bias"] != "中性" and env["direction_bias"] != expected_bias)
        print(f"\n  Conflict check: side={side} expected_bias={expected_bias}")
        print(f"  env_bias={env['direction_bias']} has_conflict={has_conflict}")
        break
