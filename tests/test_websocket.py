#!/usr/bin/env python3
"""
WebSocket模块测试
测试BinanceWSCollector类的功能
"""

import asyncio
import json
import pytest
import tempfile
from pathlib import Path
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.data.websocket import BinanceWSCollector, parse_streams


class TestBinanceWSCollector:
    """测试BinanceWSCollector类"""

    def test_init_with_valid_streams(self):
        """测试使用有效流初始化"""
        collector = BinanceWSCollector({"liquidations", "trades"})
        assert collector.streams == {"liquidations", "trades"}
        assert collector.msg_counts["liquidations"] == 0
        assert collector.msg_counts["trades"] == 0

    def test_get_stream_name(self):
        """测试获取WebSocket流名称"""
        collector = BinanceWSCollector({"liquidations", "trades"})

        # 测试映射的流
        assert collector._get_stream_name("liquidations") == "!forceOrder@arr"
        assert collector._get_stream_name("trades") == "btcusdt@aggTrade"

        # 测试未映射的流
        assert collector._get_stream_name("other") == "other"

    def test_get_output_path(self):
        """测试获取输出文件路径"""
        collector = BinanceWSCollector({"liquidations", "trades"})

        with patch('src.data.websocket.datetime') as mock_datetime:
            # 设置固定的时间
            mock_now = MagicMock()
            mock_now.strftime.side_effect = lambda fmt: {
                "%Y-%m-%d": "2026-03-07",
                "%H": "15"
            }[fmt]
            mock_datetime.now.return_value = mock_now

            # 测试清算数据路径
            liquidation_path = collector._get_output_path("liquidations", {})
            expected_liquidation_path = Path("data/raw/liquidations/2026-03-07.jsonl")
            assert liquidation_path == expected_liquidation_path

            # 测试交易数据路径
            trade_path = collector._get_output_path("trades", {})
            expected_trade_path = Path("data/raw/trades/2026-03-07/15.jsonl")
            assert trade_path == expected_trade_path

    def test_convert_decimal(self):
        """测试Decimal转换"""
        collector = BinanceWSCollector({"liquidations"})

        # 测试清算数据转换
        liquidation_data = {
            "o": {
                "p": "85000.50",
                "q": "2.345",
                "s": "BTCUSDT"
            }
        }
        converted = collector._convert_decimal(liquidation_data)

        # 验证Decimal转换为字符串
        assert converted["o"]["p"] == "85000.50"
        assert converted["o"]["q"] == "2.345"
        assert isinstance(converted["o"]["p"], str)
        assert isinstance(converted["o"]["q"], str)

        # 测试交易数据转换
        trade_data = {
            "p": "85100.75",
            "q": "1.234",
            "T": 1234567890123
        }
        converted_trade = collector._convert_decimal(trade_data)
        assert converted_trade["p"] == "85100.75"
        assert converted_trade["q"] == "1.234"
        assert isinstance(converted_trade["p"], str)
        assert isinstance(converted_trade["q"], str)

    def test_process_message_liquidation(self):
        """测试处理清算消息"""
        collector = BinanceWSCollector({"liquidations"})

        # 模拟清算消息
        liquidation_msg = {
            "e": "forceOrder",
            "E": 1234567890123,
            "o": {
                "s": "BTCUSDT",
                "S": "BUY",
                "o": "LIMIT",
                "f": "IOC",
                "q": "1.234",
                "p": "85000.00",
                "ap": "85010.50",
                "X": "FILLED",
                "l": "1.234",
                "z": "1.234",
                "T": 1234567890123
            }
        }

        with patch.object(collector, '_get_output_path') as mock_get_path, \
             patch.object(collector, '_ensure_dir'), \
             patch('src.data.websocket.time.time') as mock_time:

            # 设置时间
            mock_time.return_value = 1234567890.123
            mock_get_path.return_value = Path("test_liquidations.jsonl")

            # 模拟文件写入
            mock_file = MagicMock()
            mock_file.__enter__.return_value = mock_file

            with patch('builtins.open', return_value=mock_file):
                collector._process_message("liquidations", liquidation_msg)

                # 验证写入的内容
                mock_file.write.assert_called_once()
                write_call = mock_file.write.call_args[0][0]
                saved_data = json.loads(write_call.strip())

                assert "local_ts" in saved_data
                assert saved_data["server_ts"] == 1234567890123
                assert "data" in saved_data
                assert saved_data["data"]["e"] == "forceOrder"
                assert saved_data["data"]["o"]["p"] == "85000.00"  # Decimal转换为字符串

    def test_process_message_trade(self):
        """测试处理交易消息"""
        collector = BinanceWSCollector({"trades"})

        # 模拟交易消息
        trade_msg = {
            "e": "aggTrade",
            "E": 1234567890123,
            "a": 12345,
            "p": "85100.75",
            "q": "2.345",
            "f": 100,
            "l": 200,
            "T": 1234567890123,
            "m": True
        }

        with patch.object(collector, '_get_output_path') as mock_get_path, \
             patch.object(collector, '_ensure_dir'), \
             patch('src.data.websocket.time.time') as mock_time:

            # 设置时间
            mock_time.return_value = 1234567890.123
            mock_get_path.return_value = Path("test_trades.jsonl")

            # 模拟文件写入
            mock_file = MagicMock()
            mock_file.__enter__.return_value = mock_file

            with patch('builtins.open', return_value=mock_file):
                collector._process_message("trades", trade_msg)

                # 验证写入的内容
                mock_file.write.assert_called_once()
                write_call = mock_file.write.call_args[0][0]
                saved_data = json.loads(write_call.strip())

                assert "local_ts" in saved_data
                assert saved_data["server_ts"] == 1234567890123
                assert "data" in saved_data
                assert saved_data["data"]["e"] == "aggTrade"
                assert saved_data["data"]["p"] == "85100.75"  # Decimal转换为字符串

    @pytest.mark.asyncio
    async def test_connect_with_reconnect_success(self):
        """测试带重连的连接成功"""
        collector = BinanceWSCollector({"liquidations"})

        # 创建一个可以await的真实异步mock
        async def mock_connect(*args, **kwargs):
            class MockWebSocket:
                async def send(self, message):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

            return MockWebSocket()

        with patch('src.data.websocket.websockets.connect', side_effect=mock_connect):
            # 测试连接
            websocket = await collector._connect_with_reconnect()

            # 验证websockets.connect被调用
            from src.data.websocket import websockets
            assert websockets.connect.called
            websockets.connect.assert_called_once_with(
                "wss://fstream.binance.com/ws",
                ping_interval=20,
                ping_timeout=10
            )

            # 验证返回的websocket对象
            assert websocket is not None

    @pytest.mark.asyncio
    async def test_connect_with_reconnect_failure(self):
        """测试带重连的连接失败"""
        collector = BinanceWSCollector({"liquidations"})

        with patch('src.data.websocket.websockets.connect') as mock_connect, \
             patch('src.data.websocket.asyncio.sleep') as mock_sleep:

            # 模拟所有重连尝试都失败
            mock_connect.side_effect = Exception("连接失败")

            # 测试连接应抛出异常
            with pytest.raises(Exception) as exc_info:
                await collector._connect_with_reconnect()

            # 验证重连尝试次数
            assert mock_connect.call_count == len([1, 2, 4, 8, 16, 30])
            assert "重连次数超限" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_message_liquidation(self):
        """测试处理清算消息"""
        collector = BinanceWSCollector({"liquidations"})

        # 模拟清算消息
        liquidation_message = json.dumps({
            "e": "forceOrder",
            "E": 1234567890123,
            "o": {
                "s": "BTCUSDT",
                "S": "BUY",
                "o": "LIMIT",
                "f": "IOC",
                "q": "1.234",
                "p": "85000.00",
                "ap": "85010.50",
                "X": "FILLED",
                "l": "1.234",
                "z": "1.234",
                "T": 1234567890123
            }
        })

        with patch.object(collector, '_process_message') as mock_process:
            await collector._handle_message(liquidation_message)

            # 验证处理函数被调用
            mock_process.assert_called_once_with("liquidations", {
                "e": "forceOrder",
                "E": 1234567890123,
                "o": {
                    "s": "BTCUSDT",
                    "S": "BUY",
                    "o": "LIMIT",
                    "f": "IOC",
                    "q": "1.234",
                    "p": "85000.00",
                    "ap": "85010.50",
                    "X": "FILLED",
                    "l": "1.234",
                    "z": "1.234",
                    "T": 1234567890123
                }
            })

    @pytest.mark.asyncio
    async def test_handle_message_trade(self):
        """测试处理交易消息"""
        collector = BinanceWSCollector({"trades"})

        # 模拟交易消息
        trade_message = json.dumps({
            "e": "aggTrade",
            "E": 1234567890123,
            "a": 12345,
            "p": "85100.75",
            "q": "2.345",
            "f": 100,
            "l": 200,
            "T": 1234567890123,
            "m": True
        })

        with patch.object(collector, '_process_message') as mock_process:
            await collector._handle_message(trade_message)

            # 验证处理函数被调用
            mock_process.assert_called_once_with("trades", {
                "e": "aggTrade",
                "E": 1234567890123,
                "a": 12345,
                "p": "85100.75",
                "q": "2.345",
                "f": 100,
                "l": 200,
                "T": 1234567890123,
                "m": True
            })

    @pytest.mark.asyncio
    async def test_handle_message_ignore_non_event(self):
        """测试忽略非事件消息"""
        collector = BinanceWSCollector({"liquidations", "trades"})

        # 模拟订阅确认消息
        subscription_message = json.dumps({
            "result": None,
            "id": 1
        })

        with patch.object(collector, '_process_message') as mock_process:
            await collector._handle_message(subscription_message)

            # 验证处理函数未被调用
            mock_process.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_message_json_decode_error(self):
        """测试JSON解析错误处理"""
        collector = BinanceWSCollector({"liquidations"})

        # 模拟无效JSON
        invalid_message = "invalid json"

        with patch('src.data.websocket.logger.warning') as mock_warning:
            await collector._handle_message(invalid_message)

            # 验证警告日志被记录
            mock_warning.assert_called_once()
            assert "JSON解析失败" in mock_warning.call_args[0][0]

    @pytest.mark.asyncio
    async def test_ensure_dir(self):
        """测试目录创建"""
        collector = BinanceWSCollector({"liquidations"})

        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir) / "subdir" / "test.jsonl"

            # 调用_ensure_dir
            collector._ensure_dir(test_path)

            # 验证目录被创建
            assert test_path.parent.exists()
            assert test_path.parent.is_dir()

    @pytest.mark.asyncio
    async def test_stats_loop(self):
        """测试统计循环"""
        collector = BinanceWSCollector({"liquidations", "trades"})
        collector.running = True

        with patch('src.data.websocket.logger.info') as mock_info, \
             patch('src.data.websocket.asyncio.sleep') as mock_sleep:

            # 设置side_effect来中断循环
            mock_sleep.side_effect = [None, Exception("break")]

            try:
                await collector._stats_loop()
            except Exception as e:
                if str(e) != "break":
                    raise

            # 验证日志调用
            mock_info.assert_called()
            # 至少有一次统计输出
            assert any("[Stats]" in str(call) for call in mock_info.call_args_list)

    @pytest.mark.asyncio
    async def test_run_and_stop(self):
        """测试运行和停止采集器"""
        collector = BinanceWSCollector({"liquidations"})

        with patch.object(collector, '_connect_with_reconnect') as mock_connect, \
             patch.object(collector, '_stats_loop', return_value=asyncio.sleep(0)) as mock_stats_loop, \
             patch.object(collector, '_handle_message'):

            # 模拟WebSocket连接和消息流
            mock_websocket = AsyncMock()

            # 创建一个简单的异步迭代器
            async def message_generator():
                await asyncio.sleep(0.01)
                yield '{"result": null, "id": 1}'

            mock_websocket.__aiter__.return_value = message_generator()
            mock_connect.return_value = mock_websocket

            # 启动运行任务但立即停止
            run_task = asyncio.create_task(collector.run())
            await asyncio.sleep(0.01)

            # 停止采集器
            collector.running = False
            await collector.stop()

            # 取消运行任务
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

            # 验证连接关闭
            assert mock_websocket.close.called


class TestParseStreams:
    """测试parse_streams函数"""

    def test_parse_valid_single_stream(self):
        """测试解析单个有效流"""
        streams = parse_streams("liquidations")
        assert streams == {"liquidations"}

        streams = parse_streams("trades")
        assert streams == {"trades"}

    def test_parse_valid_multiple_streams(self):
        """测试解析多个有效流"""
        streams = parse_streams("liquidations,trades")
        assert streams == {"liquidations", "trades"}

    def test_parse_with_spaces(self):
        """测试解析带空格的流"""
        streams = parse_streams("liquidations, trades")
        assert streams == {"liquidations", "trades"}

    def test_parse_mixed_case(self):
        """测试解析混合大小写的流"""
        streams = parse_streams("LIQUIDATIONS,Trades")
        assert streams == {"liquidations", "trades"}

    def test_parse_invalid_stream(self):
        """测试解析无效流"""
        with pytest.raises(ValueError) as exc_info:
            parse_streams("invalid")

        assert "无效的流" in str(exc_info.value)

    def test_parse_empty(self):
        """测试解析空字符串"""
        with pytest.raises(ValueError) as exc_info:
            parse_streams("")

        assert "invalid" in str(exc_info.value).lower() or "无效" in str(exc_info.value)

    def test_parse_partially_invalid(self):
        """测试解析部分无效流"""
        with pytest.raises(ValueError) as exc_info:
            parse_streams("liquidations,invalid,trades")

        assert "无效的流" in str(exc_info.value)


class TestIntegration:
    """集成测试"""

    def test_data_directory_structure(self):
        """测试数据目录结构"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 修改全局数据根目录
            import src.data.websocket as ws_module
            original_data_root = ws_module.DATA_ROOT
            ws_module.DATA_ROOT = Path(tmpdir) / "data" / "raw"

            try:
                # 创建收集器，这会自动创建目录
                collector = BinanceWSCollector({"liquidations", "trades"})

                # 验证目录结构
                assert (ws_module.DATA_ROOT / "liquidations").exists()
                assert (ws_module.DATA_ROOT / "liquidations").is_dir()
                assert (ws_module.DATA_ROOT / "trades").exists()
                assert (ws_module.DATA_ROOT / "trades").is_dir()
            finally:
                ws_module.DATA_ROOT = original_data_root

    def test_jsonl_format(self):
        """测试JSONL格式"""
        with tempfile.TemporaryDirectory() as tmpdir:
            import src.data.websocket as ws_module
            original_data_root = ws_module.DATA_ROOT
            ws_module.DATA_ROOT = Path(tmpdir) / "data" / "raw"

            try:
                collector = BinanceWSCollector({"liquidations"})

                # 模拟清算消息
                liquidation_msg = {
                    "e": "forceOrder",
                    "E": 1234567890123,
                    "o": {
                        "s": "BTCUSDT",
                        "S": "BUY",
                        "p": "85000.00",
                        "q": "1.234"
                    }
                }

                with patch('src.data.websocket.time.time', return_value=1234567890.123):
                    collector._process_message("liquidations", liquidation_msg)

                # 读取写入的文件（文件名由 datetime.now() 决定，取实际写入的文件）
                liq_dir = ws_module.DATA_ROOT / "liquidations"
                written_files = list(liq_dir.glob("*.jsonl"))
                assert len(written_files) == 1, f"期望1个文件，实际: {written_files}"
                file_path = written_files[0]

                with open(file_path, "r") as f:
                    lines = f.readlines()
                    assert len(lines) == 1

                    # 验证JSONL格式
                    data = json.loads(lines[0])
                    assert "local_ts" in data
                    assert "server_ts" in data
                    assert "data" in data
                    assert data["data"]["e"] == "forceOrder"
                    assert data["data"]["o"]["p"] == "85000.00"

            finally:
                ws_module.DATA_ROOT = original_data_root


if __name__ == "__main__":
    # 运行测试
    import sys
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))