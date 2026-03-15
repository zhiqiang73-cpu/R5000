"""
第1层环境过滤的测试套件
测试时间上下文同步、资金费率小时计算、环境评估逻辑
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock, Mock
from decimal import Decimal
import sys
import os

# 添加src目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from layers.environment import EnvironmentEvaluator, EnvironmentResult
from layers.time_context import TimeContext, SameHourExpectations, TimeContextBuilder


# 全局 autouse fixture：所有测试自动 mock get_current_oi，避免真实 API 调用
@pytest.fixture(autouse=True)
def mock_current_oi():
    """自动 mock get_current_oi，返回 500M USDT（与测试用 oi_mean 一致）"""
    with patch("layers.environment.get_current_oi") as m:
        m.return_value = Decimal("500000000")
        yield m


class TestTimeContext:
    """测试时间上下文同步"""

    def test_utc_shanghai_sync(self):
        """测试UTC与上海时间的同步转换"""
        # 创建测试时间
        test_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        with patch('layers.time_context.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            builder = TimeContextBuilder()
            ctx = builder.build()

            # 检查UTC时间
            expected_utc_ms = int(test_time.timestamp() * 1000)
            assert ctx.utc_now_ms == expected_utc_ms, f"UTC时间戳错误: {ctx.utc_now_ms} != {expected_utc_ms}"

            # 检查上海时间 (UTC+8)
            expected_shanghai_str = "2025-01-01 20:00"  # UTC+8
            assert ctx.shanghai_now_str == expected_shanghai_str, \
                f"上海时间错误: {ctx.shanghai_now_str} != {expected_shanghai_str}"

            # 检查星期几 (2025-01-01是星期三)
            assert ctx.weekday == 2, f"星期几错误: {ctx.weekday} != 2 (星期三)"

    @pytest.mark.parametrize("current_hour,current_minute,expected_hours", [
        (0, 0, 8),    # 00:00 -> 下一结算08:00 = 8小时
        (7, 0, 1),    # 07:00 -> 下一结算08:00 = 1小时
        (7, 30, 0.5), # 07:30 -> 下一结算08:00 = 0.5小时
        (8, 0, 8),    # 08:00 -> 当天16:00 = 8小时
        (15, 0, 1),   # 15:00 -> 下一结算16:00 = 1小时
        (15, 30, 0.5),# 15:30 -> 下一结算16:00 = 0.5小时
        (16, 0, 8),   # 16:00 -> 第二天00:00 = 8小时
        (23, 0, 1),   # 23:00 -> 第二天00:00 = 1小时
        (23, 30, 0.5),# 23:30 -> 第二天00:00 = 0.5小时
    ])
    def test_funding_hours_calculation(self, current_hour, current_minute, expected_hours):
        """测试资金费率结算时间计算"""
        test_time = datetime(2025, 1, 1, current_hour, current_minute, 0, tzinfo=timezone.utc)

        with patch('layers.time_context.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            builder = TimeContextBuilder()
            ctx = builder.build()

            # 验证结算时间计算
            assert abs(ctx.hours_to_funding - expected_hours) < 0.01, \
                f"当前时间{current_hour}:{current_minute:02d}，期望{expected_hours}小时，实际{ctx.hours_to_funding:.2f}小时"

            # 验证约束条件
            if ctx.hours_to_funding < 2:
                assert "settle-2h: rel0.6" in ctx.constraints, "结算前2小时应有相应约束"
            elif ctx.hours_to_funding < 4:
                assert "settle-4h: rel0.8" in ctx.constraints, "结算前4小时应有相应约束"
            else:
                assert "settle-normal: rel1.0" in ctx.constraints, "正常结算时间应有相应约束"

    def test_time_constraints_generation(self):
        """测试时间约束条件生成"""
        # 测试周末约束
        weekend_time = datetime(2025, 1, 4, 12, 0, 0, tzinfo=timezone.utc)  # 周六

        with patch('layers.time_context.datetime') as mock_datetime:
            mock_datetime.now.return_value = weekend_time
            mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            builder = TimeContextBuilder()
            ctx = builder.build()

            assert "weekend: vol-reduced" in ctx.constraints, "周末应有流动性降低约束"

        # 测试周一早晨约束
        monday_morning = datetime(2025, 1, 6, 1, 0, 0, tzinfo=timezone.utc)  # 周一早晨

        with patch('layers.time_context.datetime') as mock_datetime:
            mock_datetime.now.return_value = monday_morning
            mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            builder = TimeContextBuilder()
            ctx = builder.build()

            if ctx.hours_to_funding > 6:
                assert "monday-morning: rel0.7" in ctx.constraints, "周一早晨应有可靠性降低约束"


class TestFundingRateExtremes:
    """测试资金费率极端度判断"""

    def test_fr_extreme_bullish(self):
        """测试资金费率极高 -> 偏空偏向"""
        # 创建模拟的时间上下文
        mock_time_ctx = TimeContext(
            utc_now_ms=1234567890000,
            shanghai_now_str="2025-01-01 12:00",
            hours_to_funding=5.0,
            funding_times=[],
            same_hour_expectations=SameHourExpectations(
                fr_mean=Decimal("0.0001"),
                fr_std=Decimal("0.00005"),
                vol_mean=Decimal("15000"),
                vol_median=Decimal("12000"),
                oi_mean=Decimal("500000000"),
                liq_mean=Decimal("500000")
            ),
            deviation_duration_min=0,
            constraints=[],
            weekday=0
        )

        # 模拟极高的资金费率 (zscore > +2.0)
        with patch('layers.environment.get_current_fr') as mock_fr:
            mock_fr.return_value = Decimal("0.0010")  # 0.10% (zscore = 18)

            # 模拟其他数据
            with patch('layers.environment.get_oi_change_1h') as mock_oi:
                mock_oi.return_value = Decimal("1000000")

                with patch('layers.environment.get_recent_volume_1h') as mock_vol:
                    mock_vol.return_value = Decimal("20000")

                    with patch('layers.environment.get_recent_liquidations_30m') as mock_liq:
                        mock_liq.return_value = Decimal("500000")

                        # 创建评估器
                        evaluator = EnvironmentEvaluator(time_ctx=mock_time_ctx)
                        result = evaluator.evaluate()

                        # 验证结果
                        assert result.direction_bias == '偏空', \
                            f"极高资金费率应产生偏空偏向，实际为{result.direction_bias}"
                        assert result.liquidation_side == '多头', \
                            f"极高资金费率清算方应为多头，实际为{result.liquidation_side}"
                        assert result.status in ['可交易', '休眠'], \
                            f"状态应为可交易或休眠，实际为{result.status}"

    def test_fr_extreme_bearish(self):
        """测试资金费率极低 -> 偏多偏向"""
        # 创建模拟的时间上下文
        mock_time_ctx = TimeContext(
            utc_now_ms=1234567890000,
            shanghai_now_str="2025-01-01 12:00",
            hours_to_funding=5.0,
            funding_times=[],
            same_hour_expectations=SameHourExpectations(
                fr_mean=Decimal("0.0001"),
                fr_std=Decimal("0.00005"),
                vol_mean=Decimal("15000"),
                vol_median=Decimal("12000"),
                oi_mean=Decimal("500000000"),
                liq_mean=Decimal("500000")
            ),
            deviation_duration_min=0,
            constraints=[],
            weekday=0
        )

        # 模拟极低的资金费率 (zscore < -2.0)
        with patch('layers.environment.get_current_fr') as mock_fr:
            mock_fr.return_value = Decimal("-0.0010")  # -0.10% (zscore = -22)

            # 模拟其他数据
            with patch('layers.environment.get_oi_change_1h') as mock_oi:
                mock_oi.return_value = Decimal("1000000")

                with patch('layers.environment.get_recent_volume_1h') as mock_vol:
                    mock_vol.return_value = Decimal("20000")

                    with patch('layers.environment.get_recent_liquidations_30m') as mock_liq:
                        mock_liq.return_value = Decimal("500000")

                        evaluator = EnvironmentEvaluator(time_ctx=mock_time_ctx)
                        result = evaluator.evaluate()

                        assert result.direction_bias == '偏多', \
                            f"极低资金费率应产生偏多偏向，实际为{result.direction_bias}"
                        assert result.liquidation_side == '空头', \
                            f"极低资金费率清算方应为空头，实际为{result.liquidation_side}"

    def test_fr_neutral(self):
        """测试资金费率正常 -> 中性偏向"""
        # 创建模拟的时间上下文
        mock_time_ctx = TimeContext(
            utc_now_ms=1234567890000,
            shanghai_now_str="2025-01-01 12:00",
            hours_to_funding=5.0,
            funding_times=[],
            same_hour_expectations=SameHourExpectations(
                fr_mean=Decimal("0.0001"),
                fr_std=Decimal("0.00005"),
                vol_mean=Decimal("15000"),
                vol_median=Decimal("12000"),
                oi_mean=Decimal("500000000"),
                liq_mean=Decimal("500000")
            ),
            deviation_duration_min=0,
            constraints=[],
            weekday=0
        )

        # 模拟正常的资金费率
        with patch('layers.environment.get_current_fr') as mock_fr:
            mock_fr.return_value = Decimal("0.00015")  # 0.015% (zscore = 1)

            # 模拟其他数据
            with patch('layers.environment.get_oi_change_1h') as mock_oi:
                mock_oi.return_value = Decimal("1000000")

                with patch('layers.environment.get_recent_volume_1h') as mock_vol:
                    mock_vol.return_value = Decimal("20000")

                    with patch('layers.environment.get_recent_liquidations_30m') as mock_liq:
                        mock_liq.return_value = Decimal("500000")

                        evaluator = EnvironmentEvaluator(time_ctx=mock_time_ctx)
                        result = evaluator.evaluate()

                        assert result.direction_bias == '中性', \
                            f"正常资金费率应产生中性偏向，实际为{result.direction_bias}"
                        assert result.liquidation_side is None, \
                            f"正常资金费率清算方应为None，实际为{result.liquidation_side}"


class TestEnvironmentSuitability:
    """测试环境适宜性计算"""

    def test_low_volume_dormant(self):
        """测试低成交量 -> 休眠状态"""
        # 创建模拟的时间上下文
        mock_time_ctx = TimeContext(
            utc_now_ms=1234567890000,
            shanghai_now_str="2025-01-01 12:00",
            hours_to_funding=5.0,
            funding_times=[],
            same_hour_expectations=SameHourExpectations(
                fr_mean=Decimal("0.0001"),
                fr_std=Decimal("0.00005"),
                vol_mean=Decimal("20000"),  # 高均值，使得当前成交量较低
                vol_median=Decimal("16000"),
                oi_mean=Decimal("500000000"),
                liq_mean=Decimal("500000")
            ),
            deviation_duration_min=0,
            constraints=[],
            weekday=0
        )

        # 模拟低成交量
        with patch('layers.environment.get_recent_volume_1h') as mock_vol:
            mock_vol.return_value = Decimal("5000")  # 低成交量

            # 模拟其他正常数据
            with patch('layers.environment.get_current_fr') as mock_fr:
                mock_fr.return_value = Decimal("0.0001")

                with patch('layers.environment.get_oi_change_1h') as mock_oi:
                    mock_oi.return_value = Decimal("1000000")

                    with patch('layers.environment.get_recent_liquidations_30m') as mock_liq:
                        mock_liq.return_value = Decimal("500000")

                        evaluator = EnvironmentEvaluator(time_ctx=mock_time_ctx)
                        result = evaluator.evaluate()

                        # 成交量比例 = 5000 / 20000 = 0.25 < 0.3，应休眠（流动性真空）
                        assert result.status == '休眠', \
                            f"低成交量应进入休眠状态，实际为{result.status}"
                        assert "volume" in result.reason.lower() or "流动性" in result.reason, \
                            f"休眠原因应包含成交量信息，实际为{result.reason}"
                        assert result.suitability < Decimal("0.5"), \
                            f"休眠状态适宜性分数应较低，实际为{result.suitability}"

    def test_vol_gray_zone_reduced_position(self):
        """测试成交量灰色区间(0.3-0.5) -> 可交易但仓位减半"""
        mock_time_ctx = TimeContext(
            utc_now_ms=1234567890000,
            shanghai_now_str="2025-01-01 12:00",
            hours_to_funding=5.0,
            funding_times=[],
            same_hour_expectations=SameHourExpectations(
                fr_mean=Decimal("0.0001"),
                fr_std=Decimal("0.00005"),
                vol_mean=Decimal("20000"),
                vol_median=Decimal("16000"),
                oi_mean=Decimal("500000000"),
                liq_mean=Decimal("500000")
            ),
            deviation_duration_min=0,
            constraints=[],
            weekday=0
        )

        # vol=8000, mean=20000 → ratio=0.40 (0.3 ≤ 0.40 < 0.5，灰色区间)
        with patch('layers.environment.get_recent_volume_1h') as mock_vol:
            mock_vol.return_value = Decimal("8000")

            with patch('layers.environment.get_current_fr') as mock_fr:
                mock_fr.return_value = Decimal("0.0001")

                with patch('layers.environment.get_oi_change_1h') as mock_oi:
                    mock_oi.return_value = Decimal("10000000")

                    with patch('layers.environment.get_recent_liquidations_30m') as mock_liq:
                        mock_liq.return_value = Decimal("300000")

                        evaluator = EnvironmentEvaluator(time_ctx=mock_time_ctx)
                        result = evaluator.evaluate()

                        # 灰色区间应可交易（不休眠）
                        assert result.status == '可交易', \
                            f"成交量灰色区间应可交易，实际为{result.status}"
                        # 灰色区间应有仓位减半标记
                        assert result.adjustments.get("position_multiplier") == 0.5, \
                            f"灰色区间应有position_multiplier=0.5，实际={result.adjustments}"

    def test_high_activity_high_stress_tradable(self):
        """测试高活跃度+高压力 -> 可交易状态"""
        # 创建模拟的时间上下文
        mock_time_ctx = TimeContext(
            utc_now_ms=1234567890000,
            shanghai_now_str="2025-01-01 12:00",
            hours_to_funding=5.0,
            funding_times=[],
            same_hour_expectations=SameHourExpectations(
                fr_mean=Decimal("0.0001"),
                fr_std=Decimal("0.00005"),
                vol_mean=Decimal("15000"),
                vol_median=Decimal("12000"),
                oi_mean=Decimal("500000000"),
                liq_mean=Decimal("500000")
            ),
            deviation_duration_min=0,
            constraints=[],
            weekday=0
        )

        # 模拟高成交量
        with patch('layers.environment.get_recent_volume_1h') as mock_vol:
            mock_vol.return_value = Decimal("30000")  # 高成交量

            # 模拟高OI变化 (>3%)
            with patch('layers.environment.get_oi_change_1h') as mock_oi:
                # OI变化 = 20000000 / 500000000 = 0.04 = 4% > 3%
                mock_oi.return_value = Decimal("20000000")

                # 模拟高清算量
                with patch('layers.environment.get_recent_liquidations_30m') as mock_liq:
                    # 清算量 > 1000000 (高阈值)
                    mock_liq.return_value = Decimal("1500000")

                    # 模拟正常资金费率
                    with patch('layers.environment.get_current_fr') as mock_fr:
                        mock_fr.return_value = Decimal("0.0001")

                        evaluator = EnvironmentEvaluator(time_ctx=mock_time_ctx)
                        result = evaluator.evaluate()

                        assert result.status == '可交易', \
                            f"高活跃度高压力应可交易，实际为{result.status}"
                        assert result.suitability > Decimal("0.5"), \
                            f"可交易状态适宜性分数应较高，实际为{result.suitability}"

                        # 检查调整参数
                        assert 'stop_multiplier' in result.adjustments, \
                            "应包含stop_multiplier调整参数"

    def test_low_activity_low_stress_dormant(self):
        """测试低活跃度+低压力 -> 休眠状态"""
        # 创建模拟的时间上下文
        mock_time_ctx = TimeContext(
            utc_now_ms=1234567890000,
            shanghai_now_str="2025-01-01 12:00",
            hours_to_funding=5.0,
            funding_times=[],
            same_hour_expectations=SameHourExpectations(
                fr_mean=Decimal("0.0001"),
                fr_std=Decimal("0.00005"),
                vol_mean=Decimal("15000"),
                vol_median=Decimal("12000"),
                oi_mean=Decimal("500000000"),
                liq_mean=Decimal("500000")
            ),
            deviation_duration_min=0,
            constraints=[],
            weekday=0
        )

        # 模拟正常成交量
        with patch('layers.environment.get_recent_volume_1h') as mock_vol:
            mock_vol.return_value = Decimal("16000")

            # 模拟低OI变化 (<1%)
            with patch('layers.environment.get_oi_change_1h') as mock_oi:
                # OI变化 = 4000000 / 500000000 = 0.008 = 0.8% < 1%
                mock_oi.return_value = Decimal("4000000")

                # 模拟低清算量
                with patch('layers.environment.get_recent_liquidations_30m') as mock_liq:
                    # 清算量 < 100000 (低阈值)
                    mock_liq.return_value = Decimal("50000")

                    # 模拟正常资金费率
                    with patch('layers.environment.get_current_fr') as mock_fr:
                        mock_fr.return_value = Decimal("0.0001")

                        evaluator = EnvironmentEvaluator(time_ctx=mock_time_ctx)
                        result = evaluator.evaluate()

                        assert result.status == '休眠', \
                            f"低活跃度低压力应休眠，实际为{result.status}"
                        assert "沉寂" in result.reason or "quiet" in result.reason.lower(), \
                            f"休眠原因应包含沉寂信息，实际为{result.reason}"

    def test_normal_environment_tradable(self):
        """测试正常环境 -> 可交易状态"""
        # 创建模拟的时间上下文
        mock_time_ctx = TimeContext(
            utc_now_ms=1234567890000,
            shanghai_now_str="2025-01-01 12:00",
            hours_to_funding=5.0,
            funding_times=[],
            same_hour_expectations=SameHourExpectations(
                fr_mean=Decimal("0.0001"),
                fr_std=Decimal("0.00005"),
                vol_mean=Decimal("15000"),
                vol_median=Decimal("12000"),
                oi_mean=Decimal("500000000"),
                liq_mean=Decimal("500000")
            ),
            deviation_duration_min=0,
            constraints=[],
            weekday=0
        )

        # 模拟正常成交量
        with patch('layers.environment.get_recent_volume_1h') as mock_vol:
            mock_vol.return_value = Decimal("16000")

            # 模拟中等OI变化 (1-3%)
            with patch('layers.environment.get_oi_change_1h') as mock_oi:
                # OI变化 = 10000000 / 500000000 = 0.02 = 2%
                mock_oi.return_value = Decimal("10000000")

                # 模拟中等清算量
                with patch('layers.environment.get_recent_liquidations_30m') as mock_liq:
                    mock_liq.return_value = Decimal("300000")

                    # 模拟正常资金费率
                    with patch('layers.environment.get_current_fr') as mock_fr:
                        mock_fr.return_value = Decimal("0.0001")

                        evaluator = EnvironmentEvaluator(time_ctx=mock_time_ctx)
                        result = evaluator.evaluate()

                        assert result.status == '可交易', \
                            f"正常环境应可交易，实际为{result.status}"
                        assert result.reason == '正常环境', \
                            f"正常环境原因，实际为{result.reason}"
                        assert Decimal("0.4") <= result.suitability <= Decimal("0.7"), \
                            f"正常环境适宜性分数应在0.4-0.7之间，实际为{result.suitability}"


class TestWeightedSuitabilityCalculation:
    """测试加权适宜性计算"""

    def test_suitability_calculation_logic(self):
        """测试适宜性计算逻辑"""
        test_scenarios = []

        # 场景1: 高成交量 + 高清算压力 + 高OI变化
        test_scenarios.append({
            'volume': Decimal("30000"),
            'volume_mean': Decimal("15000"),
            'oi_change': Decimal("20000000"),
            'liquidation': Decimal("1500000"),
            'expected_score': '高',
            'description': '高成交量高压力高活跃度'
        })

        # 场景2: 低成交量 + 低清算压力 + 低OI变化
        test_scenarios.append({
            'volume': Decimal("5000"),
            'volume_mean': Decimal("20000"),
            'oi_change': Decimal("4000000"),
            'liquidation': Decimal("50000"),
            'expected_score': '低',
            'description': '低成交量低压力低活跃度'
        })

        # 场景3: 中等成交量 + 中等清算压力 + 中等OI变化
        test_scenarios.append({
            'volume': Decimal("16000"),
            'volume_mean': Decimal("15000"),
            'oi_change': Decimal("10000000"),
            'liquidation': Decimal("300000"),
            'expected_score': '中',
            'description': '中等成交量中等压力中等活跃度'
        })

        for scenario in test_scenarios:
            # 创建模拟的时间上下文
            mock_time_ctx = TimeContext(
                utc_now_ms=1234567890000,
                shanghai_now_str="2025-01-01 12:00",
                hours_to_funding=5.0,
                funding_times=[],
                same_hour_expectations=SameHourExpectations(
                    fr_mean=Decimal("0.0001"),
                    fr_std=Decimal("0.00005"),
                    vol_mean=scenario['volume_mean'],
                    vol_median=scenario['volume_mean'] * Decimal("0.8"),
                    oi_mean=Decimal("500000000"),
                    liq_mean=Decimal("500000")
                ),
                deviation_duration_min=0,
                constraints=[],
                weekday=0
            )

            # 模拟数据
            with patch('layers.environment.get_recent_volume_1h') as mock_vol:
                mock_vol.return_value = scenario['volume']

                with patch('layers.environment.get_oi_change_1h') as mock_oi:
                    mock_oi.return_value = scenario['oi_change']

                    with patch('layers.environment.get_recent_liquidations_30m') as mock_liq:
                        mock_liq.return_value = scenario['liquidation']

                        with patch('layers.environment.get_current_fr') as mock_fr:
                            mock_fr.return_value = Decimal("0.0001")

                            evaluator = EnvironmentEvaluator(time_ctx=mock_time_ctx)
                            result = evaluator.evaluate()

                            # 验证适宜性分数范围
                            if scenario['expected_score'] == '高':
                                assert result.suitability > Decimal("0.7"), \
                                    f"场景'{scenario['description']}': 高适宜性分数应>0.7，实际为{result.suitability}"
                            elif scenario['expected_score'] == '中':
                                assert Decimal("0.4") <= result.suitability <= Decimal("0.7"), \
                                    f"场景'{scenario['description']}': 中等适宜性分数应在0.4-0.7之间，实际为{result.suitability}"
                            else:  # 低
                                assert result.suitability < Decimal("0.4"), \
                                    f"场景'{scenario['description']}': 低适宜性分数应<0.4，实际为{result.suitability}"


class TestEnvironmentAccuracy:
    """测试环境评估准确性"""

    def test_accuracy_on_multiple_scenarios(self):
        """测试在多个不同场景下的准确性"""
        scenarios = []

        # 场景1: 低成交量休眠
        scenarios.append({
            'volume': Decimal("5000"),
            'volume_mean': Decimal("20000"),
            'oi_change': Decimal("10000000"),
            'liquidation': Decimal("300000"),
            'expected_status': '休眠',
            'expected_reason_contains': 'volume',
            'description': '低成交量休眠'
        })

        # 场景2: 高活跃度高压力可交易
        scenarios.append({
            'volume': Decimal("30000"),
            'volume_mean': Decimal("15000"),
            'oi_change': Decimal("20000000"),
            'liquidation': Decimal("1500000"),
            'expected_status': '可交易',
            'expected_reason_contains': None,
            'description': '高活跃度高压力可交易'
        })

        # 场景3: 低活跃度低压力休眠
        scenarios.append({
            'volume': Decimal("16000"),
            'volume_mean': Decimal("15000"),
            'oi_change': Decimal("4000000"),
            'liquidation': Decimal("50000"),
            'expected_status': '休眠',
            'expected_reason_contains': '沉寂',
            'description': '低活跃度低压力休眠'
        })

        # 场景4: 极高资金费率偏空
        scenarios.append({
            'volume': Decimal("16000"),
            'volume_mean': Decimal("15000"),
            'oi_change': Decimal("10000000"),
            'liquidation': Decimal("300000"),
            'fr_value': Decimal("0.0010"),
            'expected_bias': '偏空',
            'expected_liquidation_side': '多头',
            'description': '极高资金费率偏空'
        })

        # 场景5: 极低资金费率偏多
        scenarios.append({
            'volume': Decimal("16000"),
            'volume_mean': Decimal("15000"),
            'oi_change': Decimal("10000000"),
            'liquidation': Decimal("300000"),
            'fr_value': Decimal("-0.0010"),
            'expected_bias': '偏多',
            'expected_liquidation_side': '空头',
            'description': '极低资金费率偏多'
        })

        correct_predictions = 0
        total_scenarios = len(scenarios)

        for i, scenario in enumerate(scenarios):
            # 创建模拟的时间上下文
            mock_time_ctx = TimeContext(
                utc_now_ms=1234567890000,
                shanghai_now_str="2025-01-01 12:00",
                hours_to_funding=5.0,
                funding_times=[],
                same_hour_expectations=SameHourExpectations(
                    fr_mean=Decimal("0.0001"),
                    fr_std=Decimal("0.00005"),
                    vol_mean=scenario['volume_mean'],
                    vol_median=scenario['volume_mean'] * Decimal("0.8"),
                    oi_mean=Decimal("500000000"),
                    liq_mean=Decimal("500000")
                ),
                deviation_duration_min=0,
                constraints=[],
                weekday=0
            )

            # 模拟数据
            with patch('layers.environment.get_recent_volume_1h') as mock_vol:
                mock_vol.return_value = scenario['volume']

                with patch('layers.environment.get_oi_change_1h') as mock_oi:
                    mock_oi.return_value = scenario['oi_change']

                    with patch('layers.environment.get_recent_liquidations_30m') as mock_liq:
                        mock_liq.return_value = scenario['liquidation']

                        with patch('layers.environment.get_current_fr') as mock_fr:
                            fr_value = scenario.get('fr_value', Decimal("0.0001"))
                            mock_fr.return_value = fr_value

                            evaluator = EnvironmentEvaluator(time_ctx=mock_time_ctx)
                            result = evaluator.evaluate()

                            # 检查预测是否正确
                            correct = True

                            # 检查状态
                            expected_status = scenario.get('expected_status')
                            if expected_status and result.status != expected_status:
                                print(f"场景{i+1} '{scenario['description']}': 状态预测错误。期望:{expected_status}, 实际:{result.status}")
                                correct = False

                            # 检查原因包含特定字符串 (处理中英文)
                            expected_reason_contains = scenario.get('expected_reason_contains')
                            if expected_reason_contains:
                                # 检查中文关键词
                                reason_lower = result.reason.lower()
                                if expected_reason_contains == 'volume':
                                    # 检查是否包含成交量相关的关键词
                                    if not any(keyword in reason_lower for keyword in ['成交量', 'volume', '流动']):
                                        print(f"场景{i+1} '{scenario['description']}': 原因不包含成交量关键词。实际:{result.reason}")
                                        correct = False
                                elif expected_reason_contains == '沉寂':
                                    if not any(keyword in reason_lower for keyword in ['沉寂', 'quiet']):
                                        print(f"场景{i+1} '{scenario['description']}': 原因不包含沉寂关键词。实际:{result.reason}")
                                        correct = False

                            # 检查偏向
                            expected_bias = scenario.get('expected_bias')
                            if expected_bias and result.direction_bias != expected_bias:
                                print(f"场景{i+1} '{scenario['description']}': 偏向预测错误。期望:{expected_bias}, 实际:{result.direction_bias}")
                                correct = False

                            # 检查清算方
                            expected_liquidation_side = scenario.get('expected_liquidation_side')
                            if expected_liquidation_side and result.liquidation_side != expected_liquidation_side:
                                print(f"场景{i+1} '{scenario['description']}': 清算方预测错误。期望:{expected_liquidation_side}, 实际:{result.liquidation_side}")
                                correct = False

                            if correct:
                                correct_predictions += 1

        # 计算准确率
        accuracy = correct_predictions / total_scenarios

        print(f"\n环境评估准确性测试结果:")
        print(f"总场景数: {total_scenarios}")
        print(f"正确预测: {correct_predictions}")
        print(f"准确率: {accuracy*100:.1f}%")

        # 验证准确率 >= 80%
        assert accuracy >= 0.8, f"环境评估准确率应大于等于80%，实际为{accuracy*100:.1f}%"


class TestDormantPeriodValidation:
    """测试休眠时段验证"""

    def test_dormant_period_forced_trading_loss(self):
        """测试在休眠时段强行交易的亏损率 > 55%"""
        # 这个测试需要模拟交易和回测数据
        # 由于这是一个复杂的集成测试，我们在这里只测试逻辑

        # 创建低成交量环境（休眠状态）
        mock_time_ctx = TimeContext(
            utc_now_ms=1234567890000,
            shanghai_now_str="2025-01-01 12:00",
            hours_to_funding=5.0,
            funding_times=[],
            same_hour_expectations=SameHourExpectations(
                fr_mean=Decimal("0.0001"),
                fr_std=Decimal("0.00005"),
                vol_mean=Decimal("20000"),
                vol_median=Decimal("16000"),
                oi_mean=Decimal("500000000"),
                liq_mean=Decimal("500000")
            ),
            deviation_duration_min=0,
            constraints=[],
            weekday=0
        )

        # 模拟低成交量
        with patch('layers.environment.get_recent_volume_1h') as mock_vol:
            mock_vol.return_value = Decimal("5000")  # 成交量比例 = 0.25 < 0.3

            # 模拟其他正常数据
            with patch('layers.environment.get_current_fr') as mock_fr:
                mock_fr.return_value = Decimal("0.0001")

                with patch('layers.environment.get_oi_change_1h') as mock_oi:
                    mock_oi.return_value = Decimal("10000000")

                    with patch('layers.environment.get_recent_liquidations_30m') as mock_liq:
                        mock_liq.return_value = Decimal("300000")

                        evaluator = EnvironmentEvaluator(time_ctx=mock_time_ctx)
                        result = evaluator.evaluate()

                        # 验证进入休眠状态
                        assert result.status == '休眠', \
                            f"低成交量应进入休眠状态，实际为{result.status}"

                        # 验证适宜性分数低
                        assert result.suitability < Decimal("0.5"), \
                            f"休眠状态适宜性分数应较低，实际为{result.suitability}"

                        print(f"\n休眠时段测试:")
                        print(f"状态: {result.status}")
                        print(f"原因: {result.reason}")
                        print(f"适宜性分数: {result.suitability}")
                        print("注意: 实际亏损率>55%测试需要交易回测数据")

    def test_direction_bias_vs_liquidation_consistency(self):
        """测试方向偏向与后续清算方向一致率 > 55%"""
        # 这个测试需要历史数据验证
        # 我们在这里测试资金费率极端时的偏向逻辑

        test_cases = [
            {
                'fr_value': Decimal("0.0010"),  # 极高资金费率
                'expected_bias': '偏空',
                'expected_liq_side': '多头',
                'description': '极高资金费率'
            },
            {
                'fr_value': Decimal("-0.0010"),  # 极低资金费率
                'expected_bias': '偏多',
                'expected_liq_side': '空头',
                'description': '极低资金费率'
            },
            {
                'fr_value': Decimal("0.0001"),  # 正常资金费率
                'expected_bias': '中性',
                'expected_liq_side': None,
                'description': '正常资金费率'
            }
        ]

        correct_predictions = 0

        for case in test_cases:
            mock_time_ctx = TimeContext(
                utc_now_ms=1234567890000,
                shanghai_now_str="2025-01-01 12:00",
                hours_to_funding=5.0,
                funding_times=[],
                same_hour_expectations=SameHourExpectations(
                    fr_mean=Decimal("0.0001"),
                    fr_std=Decimal("0.00005"),
                    vol_mean=Decimal("15000"),
                    vol_median=Decimal("12000"),
                    oi_mean=Decimal("500000000"),
                    liq_mean=Decimal("500000")
                ),
                deviation_duration_min=0,
                constraints=[],
                weekday=0
            )

            # 模拟数据
            with patch('layers.environment.get_recent_volume_1h') as mock_vol:
                mock_vol.return_value = Decimal("16000")

                with patch('layers.environment.get_oi_change_1h') as mock_oi:
                    mock_oi.return_value = Decimal("10000000")

                    with patch('layers.environment.get_recent_liquidations_30m') as mock_liq:
                        mock_liq.return_value = Decimal("300000")

                        with patch('layers.environment.get_current_fr') as mock_fr:
                            mock_fr.return_value = case['fr_value']

                            evaluator = EnvironmentEvaluator(time_ctx=mock_time_ctx)
                            result = evaluator.evaluate()

                            # 验证预测
                            if (result.direction_bias == case['expected_bias'] and
                                result.liquidation_side == case['expected_liq_side']):
                                correct_predictions += 1
                            else:
                                print(f"案例'{case['description']}'预测错误:")
                                print(f"  期望偏向: {case['expected_bias']}, 实际: {result.direction_bias}")
                                print(f"  期望清算方: {case['expected_liq_side']}, 实际: {result.liquidation_side}")

        consistency_rate = correct_predictions / len(test_cases)
        print(f"\n方向偏向与清算方向一致性测试:")
        print(f"总案例数: {len(test_cases)}")
        print(f"正确预测: {correct_predictions}")
        print(f"一致率: {consistency_rate*100:.1f}%")

        # 在当前简单测试中应达到100%
        assert consistency_rate == 1.0, f"简单逻辑测试一致率应为100%，实际为{consistency_rate*100:.1f}%"
        print("注意: 实际>55%一致率测试需要历史清算数据验证")


if __name__ == '__main__':
    """运行测试"""
    print("运行第1层环境过滤测试套件...")

    # 运行所有测试
    import unittest

    # 创建测试套件
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestTimeContext))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestFundingRateExtremes))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestEnvironmentSuitability))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestWeightedSuitabilityCalculation))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestEnvironmentAccuracy))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestDormantPeriodValidation))

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 输出总结
    print(f"\n测试总结:")
    print(f"运行测试数: {result.testsRun}")
    print(f"失败数: {len(result.failures)}")
    print(f"错误数: {len(result.errors)}")

    if result.wasSuccessful():
        print("所有测试通过!")
    else:
        print("有测试失败!")
        import sys
        sys.exit(1)