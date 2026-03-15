"""Run backtest and output clean JSON."""
import json, sys, io, logging
from decimal import Decimal
from src.backtest.engine import EventDrivenBacktestEngine

logging.basicConfig(level=logging.WARNING)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

engine = EventDrivenBacktestEngine(
    config={"initial_capital": Decimal("10000")},
    data_root="data/raw",
    symbol="BTCUSDT",
)
results = engine.run_backtest(start_date="2026-03-07", end_date="2026-03-15")
with open("bt_result.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=str)
print("Done. Results in bt_result.json")
