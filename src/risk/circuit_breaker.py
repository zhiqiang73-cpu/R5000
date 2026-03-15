# -*- coding: utf-8 -*-
"""熔断规则管理器"""

from decimal import Decimal
from typing import List, Optional, Tuple, Any

from src.risk.position import downgrade_grade


class RiskManager:
    """
    风险熔断管理器。

    使用方式：
        rm = RiskManager(Decimal("100000"))
        rm.daily_loss = Decimal("3500")
        rm.recent_trades = [trade1, trade2, ...]
        action, reason = rm.check_circuit_breakers()
    """

    def __init__(self, total_capital: Decimal):
        self.total_capital = total_capital
        # 可直接赋值，供测试/回测注入状态
        self.daily_loss: Decimal = Decimal("0")   # 当日累计亏损 (USDT，正数)
        self.recent_trades: List[Any] = []         # 对象需有 .pnl 属性 (USDT)

    def check_circuit_breakers(self) -> Tuple[Optional[str], Optional[str]]:
        """
        按优先级检查四条熔断规则。

        Returns:
            (动作说明, 原因代码) 或 (None, None) 表示无熔断
        """
        # 1. 当日累计亏损 > 3%
        if self.daily_loss > self.total_capital * Decimal("0.03"):
            return "停止当日交易", "daily_loss_limit"

        if not self.recent_trades:
            return None, None

        # 2. 最新单笔亏损 > 2%
        last = self.recent_trades[-1]
        if last.pnl < -self.total_capital * Decimal("0.02"):
            return "暂停30分钟 + 人工确认", "large_single_loss"

        # 3. 连续3笔止损（当日不再交易，暂停至次日0点）
        if len(self.recent_trades) >= 3:
            if all(t.pnl < 0 for t in self.recent_trades[-3:]):
                return "暂停至次日0点", "consecutive_losses"

        # 4. 最近10笔胜率 < 30%
        if len(self.recent_trades) >= 10:
            wins = sum(1 for t in self.recent_trades[-10:] if t.pnl > 0)
            if wins < 3:
                return "暂停1小时", "low_win_rate"

        return None, None

    def check_before_entry(
        self,
        signal: dict,
        env: Any,
    ) -> Tuple[bool, Optional[str]]:
        """
        入场前检查：方向与环境偏向冲突时降级。

        signal 字典会被原地修改（grade 降级）。
        Returns:
            (可以入场, 拒绝原因或None)
        """
        side = signal.get("side", "")
        bias = getattr(env, "direction_bias", "中性")

        conflict = (side == "BUY" and bias == "偏空") or \
                   (side == "SELL" and bias == "偏多")

        if conflict:
            signal["grade"] = downgrade_grade(signal["grade"])

        return True, None
