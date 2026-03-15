from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.backtest.engine import BacktestDataset, EventDrivenBacktestEngine

logger = logging.getLogger(__name__)


def _merge(base: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(base)
    if overrides:
        merged.update(overrides)
    return merged


def _as_decimal(value: Any) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    def to_dict(self) -> dict[str, int]:
        return {
            "fold_id": self.fold_id,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
        }


class WalkForwardAnalyzer:
    def __init__(self, base_config: dict[str, Any] | None = None, data_root: str = "data/raw", symbol: str = "BTCUSDT") -> None:
        self.base_config = dict(base_config or {})
        self.data_root = data_root
        self.symbol = symbol
        self.engine = EventDrivenBacktestEngine(config=self.base_config, data_root=data_root, symbol=symbol)
        self.dataset = BacktestDataset(symbol=symbol)

    def load_data(self, start_date: str, end_date: str) -> BacktestDataset:
        self.dataset = self.engine.load_data(start_date, end_date)
        return self.dataset

    def build_folds(self, dataset: BacktestDataset, folds: int = 4) -> list[WalkForwardFold]:
        if dataset.is_empty() or folds <= 0 or dataset.end_time <= dataset.start_time:
            return []
        total_span = dataset.end_time - dataset.start_time
        step = max(total_span // (folds + 2), 1)
        train_end = dataset.start_time + step * 2
        results: list[WalkForwardFold] = []

        for fold_id in range(1, folds + 1):
            test_start = train_end
            test_end = dataset.end_time if fold_id == folds else min(dataset.end_time, test_start + step)
            if test_start >= dataset.end_time or test_end <= test_start:
                break
            results.append(
                WalkForwardFold(
                    fold_id=fold_id,
                    train_start=dataset.start_time,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                )
            )
            train_end = test_end

        return results

    def run(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        dataset: BacktestDataset | None = None,
        folds: int = 4,
        candidate_configs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if dataset is None:
            if start_date and end_date:
                dataset = self.load_data(start_date, end_date)
            else:
                dataset = self.dataset

        fold_defs = self.build_folds(dataset, folds=folds)
        if not fold_defs:
            return {"walk_forward_folds": [], "summary": {"fold_count": 0, "profitable_folds": 0, "all_folds_profitable": False}}

        candidates = candidate_configs or self.default_candidate_configs()
        fold_results: list[dict[str, Any]] = []

        for fold in fold_defs:
            train_dataset = dataset.slice(fold.train_start, fold.train_end)
            test_dataset = dataset.slice(fold.test_start, fold.test_end)
            best_config, train_result = self._select_best_config(train_dataset, candidates)
            test_engine = EventDrivenBacktestEngine(config=best_config, data_root=self.data_root, symbol=self.symbol)
            test_result = test_engine.run_backtest(dataset=test_dataset)
            stability = self._parameter_stability(train_dataset, best_config)
            fold_results.append(
                {
                    **fold.to_dict(),
                    "selected_config": self._serialize_config(best_config),
                    "train_metrics": train_result["metrics"],
                    "test_metrics": test_result["metrics"],
                    "parameter_stability": stability,
                }
            )

        return {
            "walk_forward_folds": fold_results,
            "summary": self._summarize(fold_results),
        }

    def default_candidate_configs(self) -> list[dict[str, Any]]:
        return [
            {},
            {
                "min_price_change": Decimal("0.0012"),
                "volume_surge_threshold": Decimal("1.8"),
                "liq_ratio_threshold": Decimal("0.12"),
            },
            {
                "min_price_change": Decimal("0.0018"),
                "volume_surge_threshold": Decimal("2.2"),
                "liq_ratio_threshold": Decimal("0.18"),
            },
        ]

    def _select_best_config(self, train_dataset: BacktestDataset, candidates: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        best_config = _merge(self.base_config, candidates[0])
        best_result = EventDrivenBacktestEngine(config=best_config, data_root=self.data_root, symbol=self.symbol).run_backtest(dataset=train_dataset)
        best_score = self._score(best_result["metrics"])

        for candidate in candidates[1:]:
            config = _merge(self.base_config, candidate)
            engine = EventDrivenBacktestEngine(config=config, data_root=self.data_root, symbol=self.symbol)
            result = engine.run_backtest(dataset=train_dataset)
            score = self._score(result["metrics"])
            if score > best_score:
                best_config = config
                best_result = result
                best_score = score

        return best_config, best_result

    def _parameter_stability(self, dataset: BacktestDataset, best_config: dict[str, Any]) -> dict[str, float]:
        base_engine = EventDrivenBacktestEngine(config=best_config, data_root=self.data_root, symbol=self.symbol)
        base_result = base_engine.run_backtest(dataset=dataset)
        base_sharpe = float(base_result["metrics"].get("sharpe", 0.0))
        perturbations = [
            {"min_price_change": _as_decimal(best_config.get("min_price_change", Decimal("0.0015"))) * Decimal("0.8")},
            {"volume_surge_threshold": _as_decimal(best_config.get("volume_surge_threshold", Decimal("2.0"))) * Decimal("1.2")},
            {"liq_ratio_threshold": _as_decimal(best_config.get("liq_ratio_threshold", Decimal("0.15"))) * Decimal("0.8")},
        ]
        max_change = 0.0
        for perturbation in perturbations:
            config = _merge(best_config, perturbation)
            result = EventDrivenBacktestEngine(config=config, data_root=self.data_root, symbol=self.symbol).run_backtest(dataset=dataset)
            sharpe = float(result["metrics"].get("sharpe", 0.0))
            max_change = max(max_change, abs(sharpe - base_sharpe))
        return {"base_sharpe": base_sharpe, "max_sharpe_change": max_change}

    def _score(self, metrics: dict[str, Any]) -> tuple:
        expectancy = float(metrics.get("expectancy", 0.0))
        sharpe = float(metrics.get("sharpe", 0.0))
        total_return = float(metrics.get("total_return_pct", 0.0))
        max_drawdown = float(metrics.get("max_drawdown", 0.0))
        total_trades = int(metrics.get("total_trades", 0))
        return (expectancy > 0, sharpe, total_return, -max_drawdown, total_trades)

    def _serialize_config(self, config: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in config.items():
            result[key] = float(value) if isinstance(value, Decimal) else value
        return result

    def _summarize(self, folds: list[dict[str, Any]]) -> dict[str, Any]:
        profitable = [fold for fold in folds if float(fold["test_metrics"].get("expectancy", 0.0)) > 0]
        avg_sharpe = sum(float(fold["test_metrics"].get("sharpe", 0.0)) for fold in folds) / len(folds)
        avg_expectancy = sum(float(fold["test_metrics"].get("expectancy", 0.0)) for fold in folds) / len(folds)
        max_drawdown = max(float(fold["test_metrics"].get("max_drawdown", 0.0)) for fold in folds)
        max_sharpe_change = max(float(fold["parameter_stability"].get("max_sharpe_change", 0.0)) for fold in folds)
        return {
            "fold_count": len(folds),
            "profitable_folds": len(profitable),
            "all_folds_profitable": len(profitable) == len(folds),
            "avg_sharpe": avg_sharpe,
            "avg_expectancy": avg_expectancy,
            "max_drawdown": max_drawdown,
            "max_sharpe_change": max_sharpe_change,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run walk-forward validation for the three-layer strategy.")
    parser.add_argument("--start", required=True, help="Start date in YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date in YYYY-MM-DD")
    parser.add_argument("--folds", type=int, default=4, help="Number of walk-forward folds")
    parser.add_argument("--data-root", default="data/raw", help="Raw data root directory")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading symbol")
    parser.add_argument("--json", action="store_true", help="Print raw JSON output")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    analyzer = WalkForwardAnalyzer(data_root=args.data_root, symbol=args.symbol)
    results = analyzer.run(start_date=args.start, end_date=args.end, folds=args.folds)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(results["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
