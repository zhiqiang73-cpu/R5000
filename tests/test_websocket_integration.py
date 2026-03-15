#!/usr/bin/env python3
"""
WebSocket集成测试
测试WebSocket模块的实际运行
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.data.websocket import BinanceWSCollector, parse_streams


async def test_websocket_data_collection():
    """测试WebSocket数据收集"""
    print("=== WebSocket数据收集测试 ===")

    # 使用临时目录
    with tempfile.TemporaryDirectory() as tmpdir:
        import src.data.websocket as ws_module
        original_data_root = ws_module.DATA_ROOT
        ws_module.DATA_ROOT = Path(tmpdir) / "data" / "raw"

        try:
            # 创建收集器（只测试一个流以减少复杂度）
            collector = BinanceWSCollector({"liquidations"})

            # 模拟WebSocket消息
            mock_messages = [
                # 清算消息
                json.dumps({
                    "e": "forceOrder",
                    "E": 1234567890123,
                    "o": {
                        "s": "BTCUSDT",
                        "S": "BUY",
                        "o": "LIMIT",
                        "f": "IOC",
                        "q": "2.500",
                        "p": "85000.00",
                        "ap": "85010.50",
                        "X": "FILLED",
                        "l": "2.500",
                        "z": "2.500",
                        "T": 1234567890123
                    }
                }),
                # 另一个清算消息
                json.dumps({
                    "e": "forceOrder",
                    "E": 1234567890124,
                    "o": {
                        "s": "BTCUSDT",
                        "S": "SELL",
                        "o": "LIMIT",
                        "f": "IOC",
                        "q": "1.200",
                        "p": "85100.00",
                        "ap": "85090.50",
                        "X": "FILLED",
                        "l": "1.200",
                        "z": "1.200",
                        "T": 1234567890124
                    }
                }),
                # 订阅确认消息（应该被忽略）
                json.dumps({
                    "result": None,
                    "id": 1
                })
            ]

            # 模拟WebSocket连接
            with patch('src.data.websocket.websockets.connect') as mock_connect:
                # 创建模拟的WebSocket对象
                mock_websocket = AsyncMock()
                mock_connect.return_value = mock_websocket

                # 模拟__aiter__返回消息
                async def message_generator():
                    for msg in mock_messages:
                        yield msg
                        await asyncio.sleep(0.01)  # 小延迟

                mock_websocket.__aiter__.return_value = message_generator()

                # 运行收集器一小段时间
                collector.running = True

                # 创建统计任务
                stats_task = asyncio.create_task(collector._stats_loop())

                # 创建主循环任务
                main_task = asyncio.create_task(collector.run())

                # 等待一小段时间让消息处理
                await asyncio.sleep(0.1)

                # 停止收集器
                collector.running = False
                stats_task.cancel()
                main_task.cancel()

                # 等待任务完成
                try:
                    await asyncio.gather(stats_task, main_task, return_exceptions=True)
                except asyncio.CancelledError:
                    pass

            # 验证数据文件
            liquidation_dir = ws_module.DATA_ROOT / "liquidations"
            assert liquidation_dir.exists()

            files = list(liquidation_dir.glob("*.jsonl"))
            assert len(files) == 1, f"Expected 1 JSONL file, found {len(files)}"

            # 读取并验证文件内容
            with open(files[0], "r") as f:
                lines = f.readlines()
                assert len(lines) == 2, f"Expected 2 messages, found {len(lines)}"

                # 验证第一条消息
                data1 = json.loads(lines[0])
                assert data1["data"]["e"] == "forceOrder"
                assert data1["data"]["o"]["s"] == "BTCUSDT"
                assert data1["data"]["o"]["S"] == "BUY"
                assert data1["data"]["o"]["q"] == "2.500"  # Decimal转换为字符串
                assert data1["data"]["o"]["p"] == "85000.00"

                # 验证第二条消息
                data2 = json.loads(lines[1])
                assert data2["data"]["o"]["S"] == "SELL"
                assert data2["data"]["o"]["q"] == "1.200"
                assert data2["data"]["o"]["p"] == "85100.00"

            print("✓ 数据收集测试通过")
            print(f"  文件: {files[0]}")
            print(f"  消息数: {len(lines)}")

        finally:
            ws_module.DATA_ROOT = original_data_root


def test_file_storage_structure():
    """测试文件存储结构"""
    print("=== 文件存储结构测试 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        import src.data.websocket as ws_module
        original_data_root = ws_module.DATA_ROOT
        ws_module.DATA_ROOT = Path(tmpdir) / "data" / "raw"

        try:
            # 创建收集器
            collector = BinanceWSCollector({"liquidations", "trades"})

            # 模拟时间以固定文件名
            with patch('src.data.websocket.datetime') as mock_datetime:
                mock_now = type('MockDateTime', (), {})()
                mock_now.strftime = lambda fmt: {
                    "%Y-%m-%d": "2026-03-07",
                    "%H": "14"
                }[fmt]
                mock_datetime.now.return_value = mock_now

                # 测试清算文件路径
                liquidation_path = collector._get_output_path("liquidations", {})
                expected_liquidation_path = ws_module.DATA_ROOT / "liquidations" / "2026-03-07.jsonl"
                assert liquidation_path == expected_liquidation_path

                # 测试交易文件路径
                trade_path = collector._get_output_path("trades", {})
                expected_trade_path = ws_module.DATA_ROOT / "trades" / "2026-03-07" / "14.jsonl"
                assert trade_path == expected_trade_path

            print("✓ 文件存储结构测试通过")
            print(f"  清算文件路径: {expected_liquidation_path}")
            print(f"  交易文件路径: {expected_trade_path}")

        finally:
            ws_module.DATA_ROOT = original_data_root


def test_stream_parsing():
    """测试流参数解析"""
    print("=== 流参数解析测试 ===")

    test_cases = [
        ("liquidations", {"liquidations"}),
        ("trades", {"trades"}),
        ("liquidations,trades", {"liquidations", "trades"}),
        ("LIQUIDATIONS,TRADES", {"liquidations", "trades"}),
        ("liquidations, trades", {"liquidations", "trades"}),
    ]

    for input_str, expected in test_cases:
        result = parse_streams(input_str)
        assert result == expected, f"解析失败: {input_str} -> {result}"
        print(f"  ✓ {input_str} -> {result}")

    # 测试无效输入
    try:
        parse_streams("invalid")
        assert False, "应该抛出ValueError"
    except ValueError as e:
        print(f"  ✓ 检测到无效流: {e}")

    try:
        parse_streams("")
        assert False, "应该抛出ValueError"
    except ValueError as e:
        print(f"  ✓ 检测到空流: {e}")


def test_decimal_conversion():
    """测试Decimal转换"""
    print("=== Decimal转换测试 ===")

    collector = BinanceWSCollector({"liquidations"})

    test_data = {
        "o": {
            "p": "85000.50",  # 价格
            "q": "2.345",     # 数量
            "s": "BTCUSDT"
        }
    }

    converted = collector._convert_decimal(test_data)

    # 验证转换
    assert converted["o"]["p"] == "85000.50"
    assert converted["o"]["q"] == "2.345"
    assert isinstance(converted["o"]["p"], str)
    assert isinstance(converted["o"]["q"], str)

    print("✓ Decimal转换测试通过")
    print(f"  价格: {converted['o']['p']} (类型: {type(converted['o']['p']).__name__})")
    print(f"  数量: {converted['o']['q']} (类型: {type(converted['o']['q']).__name__})")


async def run_all_tests():
    """运行所有测试"""
    print("\n" + "="*60)
    print("WebSocket模块集成测试")
    print("="*60 + "\n")

    try:
        # 运行同步测试
        test_file_storage_structure()
        test_stream_parsing()
        test_decimal_conversion()

        # 运行异步测试
        await test_websocket_data_collection()

        print("\n" + "="*60)
        print("所有测试通过！")
        print("="*60)

    except Exception as e:
        print(f"\n测试失败: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    # 运行集成测试
    asyncio.run(run_all_tests())