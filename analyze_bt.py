"""Quick analysis of backtest results."""
import json, sys, io
from collections import Counter
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

path = sys.argv[1] if len(sys.argv) > 1 else "bt_result.json"
with open(path, encoding="utf-8") as f:
    text = f.read()
start = text.index("{")
data = json.loads(text[start:])

clf = Counter()
for c in data.get("classifications", []):
    clf[c["classification"]] += 1

print("=== Classification Distribution ===")
for k, v in clf.most_common():
    print(f"  {k}: {v}")
print(f"  Total: {sum(clf.values())}")
print()

m = data["metrics"]
print("=== Metrics ===")
print(f"  Impacts: {m['impact_count']}")
print(f"  Classifications: {m['classification_count']}")
print(f"  Trades: {m['total_trades']}")
print(f"  Wins/Losses: {m['wins']}/{m['losses']}")
print(f"  Win Rate: {m['win_rate']:.1%}")
print(f"  Net PnL: ${m['total_pnl']:.2f}")
print(f"  Avg RR: {m['avg_rr']:.2f}")
print(f"  Sharpe: {m['sharpe']:.3f}")
print(f"  Circuit Breakers: {m['circuit_breaker_count']}")
print()

print("=== Trade Details ===")
for i, t in enumerate(data["trades"]):
    strat = t["strategy"]
    side = t["side"]
    grade = t["grade"]
    pnl = t["pnl"]
    exit_reason = t["exit_reason"]
    conf = t["confidence"]
    fee = t["entry_fee"] + t["exit_fee"]
    print(f"  #{i+1} {strat} {side} grade={grade} conf={conf:.1f} exit={exit_reason} pnl=${pnl:.2f} fees=${fee:.2f}")
