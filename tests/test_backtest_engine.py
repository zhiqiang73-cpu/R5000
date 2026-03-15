import json
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.backtest.engine import BacktestDataset, EventDrivenBacktestEngine, LiquidationTick, TradeTick
from src.backtest.walk_forward import WalkForwardAnalyzer

BASE_TS = 1_700_000_000_000


def make_trade(offset_seconds: int, price: str, quantity: str = "0.01", maker: bool = False) -> TradeTick:
    return TradeTick(
        timestamp=BASE_TS + offset_seconds * 1000,
        price=Decimal(price),
        quantity=Decimal(quantity),
        is_buyer_maker=maker,
        symbol="BTCUSDT",
    )


def build_mean_reversion_dataset(event_count: int = 1) -> BacktestDataset:
    trades = []
    liquidations = []
    cursor = 0
    baseline_prices = ["100.00", "100.01", "99.99", "100.00"]

    for _ in range(event_count):
        for i in range(720):
            trades.append(make_trade(cursor, baseline_prices[i % len(baseline_prices)], "0.01", maker=(i % 2 == 0)))
            cursor += 5

        for price in ["100.00", "99.92", "99.84", "99.76", "99.68", "99.60"]:
            trades.append(make_trade(cursor, price, "1.00", maker=False))
            cursor += 5

        for _ in range(9):
            trades.append(make_trade(cursor, "99.83", "0.05", maker=False))
            cursor += 5

        for price in ["99.86", "99.90", "99.94", "99.98", "100.04", "100.10"]:
            trades.append(make_trade(cursor, price, "0.05", maker=False))
            cursor += 5

        for i in range(120):
            trades.append(make_trade(cursor, baseline_prices[i % len(baseline_prices)], "0.02", maker=(i % 2 == 1)))
            cursor += 5

    return BacktestDataset(trades=tuple(trades), liquidations=tuple(liquidations), symbol="BTCUSDT")


class BacktestEngineTests(unittest.TestCase):
    def test_load_data_supports_top_level_jsonl(self) -> None:
        root = Path("tests/.tmp_backtest_loader")
        try:
            trade_dir = root / "trades" / "2026-03-07"
            trade_dir.mkdir(parents=True)
            liq_dir = root / "liquidations"
            liq_dir.mkdir(parents=True)

            trade_record = {
                "e": "aggTrade",
                "E": 1772893681835,
                "s": "BTCUSDT",
                "p": "67767.10",
                "q": "0.003",
                "T": 1772893681681,
                "m": True,
            }
            liq_record = {
                "e": "forceOrder",
                "E": 1772893645088,
                "o": {
                    "s": "BTCUSDT",
                    "S": "SELL",
                    "q": "2.0",
                    "p": "67700.00",
                    "T": 1772893645087,
                },
            }
            (trade_dir / "22.jsonl").write_text(json.dumps(trade_record, ensure_ascii=False) + "\n", encoding="utf-8")
            (liq_dir / "2026-03-07.jsonl").write_text(json.dumps(liq_record, ensure_ascii=False) + "\n", encoding="utf-8")

            engine = EventDrivenBacktestEngine(data_root=root)
            dataset = engine.load_data("2026-03-07", "2026-03-07")
            self.assertEqual(len(dataset.trades), 1)
            self.assertEqual(len(dataset.liquidations), 1)
            self.assertEqual(dataset.trades[0].symbol, "BTCUSDT")
            self.assertEqual(dataset.liquidations[0].side, "SELL")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_backtest_runs_end_to_end_on_synthetic_dataset(self) -> None:
        dataset = build_mean_reversion_dataset(event_count=1)
        engine = EventDrivenBacktestEngine(config={"impact_cooldown_ms": 600_000})
        result = engine.run_backtest(dataset=dataset)

        self.assertGreaterEqual(result["metrics"]["impact_count"], 1)
        self.assertGreaterEqual(result["metrics"]["total_trades"], 1)
        self.assertGreater(result["metrics"]["total_pnl"], 0)
        self.assertEqual(result["classifications"][0]["classification"], "\u8fc7\u5ea6\u53cd\u5e94")

    def test_walk_forward_returns_fold_metrics(self) -> None:
        dataset = build_mean_reversion_dataset(event_count=4)
        analyzer = WalkForwardAnalyzer(base_config={"impact_cooldown_ms": 600_000})
        result = analyzer.run(dataset=dataset, folds=2, candidate_configs=[{}])

        self.assertEqual(len(result["walk_forward_folds"]), 2)
        self.assertIn("summary", result)
        for fold in result["walk_forward_folds"]:
            self.assertIn("test_metrics", fold)
            self.assertIn("parameter_stability", fold)


if __name__ == "__main__":
    unittest.main()
