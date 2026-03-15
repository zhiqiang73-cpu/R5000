from importlib import import_module

__all__ = [
    "BacktestDataset",
    "EventDrivenBacktestEngine",
    "ExecutedTrade",
    "ImpactCandidate",
    "LiquidationTick",
    "TradeTick",
    "WalkForwardAnalyzer",
    "WalkForwardFold",
]

_NAME_TO_MODULE = {
    "BacktestDataset": ".engine",
    "EventDrivenBacktestEngine": ".engine",
    "ExecutedTrade": ".engine",
    "ImpactCandidate": ".engine",
    "LiquidationTick": ".engine",
    "TradeTick": ".engine",
    "WalkForwardAnalyzer": ".walk_forward",
    "WalkForwardFold": ".walk_forward",
}


def __getattr__(name: str):
    if name not in _NAME_TO_MODULE:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_NAME_TO_MODULE[name], __name__)
    return getattr(module, name)
