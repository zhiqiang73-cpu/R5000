from __future__ import annotations

from decimal import Decimal
from math import sqrt
from statistics import mean, pstdev
from typing import Dict, Iterable, Sequence


def _decimal_sum(values: Iterable[Decimal]) -> Decimal:
    total = Decimal('0')
    for value in values:
        total += value
    return total


def _max_drawdown(equity_curve: Sequence[Decimal]) -> Decimal:
    if not equity_curve:
        return Decimal('0')
    peak = equity_curve[0]
    max_dd = Decimal('0')
    for equity in equity_curve:
        if equity > peak:
            peak = equity
        if peak > 0:
            drawdown = (peak - equity) / peak
            if drawdown > max_dd:
                max_dd = drawdown
    return max_dd


def _sharpe_from_returns(returns: Sequence[float]) -> float:
    if len(returns) < 2:
        return 0.0
    std = pstdev(returns)
    if std == 0:
        return 0.0
    return mean(returns) / std * sqrt(len(returns))


def build_metrics(
    initial_capital: Decimal,
    ending_capital: Decimal,
    trades: Sequence[object],
    daily_pnl: Dict[str, Decimal],
) -> Dict[str, float | int]:
    total_trades = len(trades)
    total_pnl = _decimal_sum(getattr(trade, 'pnl', Decimal('0')) for trade in trades)
    wins = [trade for trade in trades if getattr(trade, 'pnl', Decimal('0')) > 0]
    losses = [trade for trade in trades if getattr(trade, 'pnl', Decimal('0')) < 0]

    gross_profit = _decimal_sum(trade.pnl for trade in wins)
    gross_loss = abs(_decimal_sum(trade.pnl for trade in losses))
    win_rate = (len(wins) / total_trades) if total_trades else 0.0
    expectancy = (total_pnl / total_trades) if total_trades else Decimal('0')
    profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else 0.0

    equity_curve = [initial_capital]
    equity = initial_capital
    for trade in trades:
        equity += trade.pnl
        equity_curve.append(equity)

    max_drawdown = _max_drawdown(equity_curve)

    daily_returns = []
    for pnl in daily_pnl.values():
        if initial_capital > 0:
            daily_returns.append(float(pnl / initial_capital))

    avg_rr = 0.0
    if total_trades:
        avg_rr = float(sum(float(trade.planned_rr) for trade in trades) / total_trades)

    return {
        'total_trades': total_trades,
        'wins': len(wins),
        'losses': len(losses),
        'ending_capital': float(ending_capital),
        'total_pnl': float(total_pnl),
        'total_return_pct': float(((ending_capital - initial_capital) / initial_capital) if initial_capital > 0 else Decimal('0')),
        'win_rate': win_rate,
        'expectancy': float(expectancy),
        'profit_factor': profit_factor,
        'avg_rr': avg_rr,
        'sharpe': _sharpe_from_returns(daily_returns),
        'max_drawdown': float(max_drawdown),
    }
