"""Deep analysis: why do trend trades fail?"""
import json, sys, io
from collections import Counter
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

with open("bt_result.json", encoding="utf-8") as f:
    data = json.load(f)

print("=== Classification Detail ===")
for c in data.get("classifications", []):
    clf = c["classification"]
    liq_c = c.get("liq_count", 0)
    liq_v = float(c.get("liq_value", 0))
    liq_r = float(c.get("liq_ratio", 0))
    conf = float(c.get("confidence", 0))
    print(f"  {clf:6s} liq_count={liq_c:2d} liq_val=${liq_v:>10,.0f} liq_ratio={liq_r:.6f} conf={conf:.2f}")

print("\n=== 真突破 liq_value distribution ===")
breakouts = [c for c in data.get("classifications", []) if c["classification"] == "真突破"]
for b in breakouts:
    print(f"  liq_count={b['liq_count']:2d} liq_value=${float(b['liq_value']):>12,.0f} liq_ratio={float(b['liq_ratio']):.6f} conf={float(b['confidence']):.2f}")

print(f"\n  Avg liq_value: ${sum(float(b['liq_value']) for b in breakouts)/len(breakouts):,.0f}")
print(f"  Max liq_value: ${max(float(b['liq_value']) for b in breakouts):,.0f}")
print(f"  Min liq_value: ${min(float(b['liq_value']) for b in breakouts):,.0f}")

print("\n=== Trade Strategy Breakdown ===")
by_strat = {}
for t in data["trades"]:
    s = t["strategy"]
    by_strat.setdefault(s, []).append(t)
for s, trades in by_strat.items():
    wins = sum(1 for t in trades if t["pnl"] > 0)
    total_pnl = sum(t["pnl"] for t in trades)
    total_fees = sum(t["entry_fee"] + t["exit_fee"] for t in trades)
    total_gross = sum(t["gross_pnl"] for t in trades)
    print(f"  {s}: {len(trades)} trades, {wins} wins, gross=${total_gross:.2f}, fees=${total_fees:.2f}, net=${total_pnl:.2f}")

print("\n=== Trend Trades: Why They Fail ===")
for i, t in enumerate(data["trades"]):
    if "趋势" in t["strategy"]:
        hold = (t["exit_time"] - t["entry_time"]) / 1000
        risk = abs(t["entry_price"] - t["stop_loss"])
        potential = abs(t["take_profit"] - t["entry_price"])
        price_move = t["exit_price"] - t["entry_price"]
        if t["side"] == "SELL":
            price_move = -price_move
        pct_of_target = (price_move / potential * 100) if potential > 0 else 0
        print(f"  #{i+1} {t['side']} hold={hold:.0f}s exit={t['exit_reason']} move=${price_move:.1f} target=${potential:.1f} ({pct_of_target:.0f}% of target)")
