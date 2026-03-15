#!/usr/bin/env python3
"""
手动测试WebSocket连接
"""

import asyncio
import json
import signal
import time
from pathlib import Path
from datetime import datetime
from decimal import Decimal
import websockets


async def test_liquidation_stream(duration_seconds: int = 30):
    """
    测试清算流连接
    """
    print(f"=== 测试清算流连接 ({duration_seconds}秒) ===")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"WebSocket URL: wss://fstream.binance.com/ws/!forceOrder@arr")
    print("-" * 60)

    # 创建数据目录
    data_dir = Path("data/raw/liquidations")
    data_dir.mkdir(parents=True, exist_ok=True)

    # 创建今天的文件
    filename = datetime.now().strftime("%Y-%m-%d.jsonl")
    filepath = data_dir / filename

    print(f"数据文件: {filepath}")

    message_count = 0
    start_time = time.time()

    try:
        # 连接WebSocket
        uri = "wss://fstream.binance.com/ws/!forceOrder@arr"
        print(f"正在连接到 {uri}...")

        async with websockets.connect(uri) as websocket:
            print("连接成功！等待消息...")
            print("按 Ctrl+C 停止")

            while time.time() - start_time < duration_seconds:
                try:
                    # 接收消息
                    message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    data = json.loads(message)

                    # 添加本地时间戳
                    local_ts = int(time.time() * 1000)
                    data["local_ts"] = local_ts

                    # 确保Decimal转换
                    if "o" in data:
                        if "p" in data["o"]:
                            data["o"]["p"] = str(Decimal(str(data["o"]["p"])))
                        if "q" in data["o"]:
                            data["o"]["q"] = str(Decimal(str(data["o"]["q"])))

                    # 写入文件
                    with open(filepath, "a", encoding="utf-8") as f:
                        f.write(json.dumps(data, ensure_ascii=False) + "\n")

                    message_count += 1

                    # 每收到5条消息打印一次
                    if message_count % 5 == 0:
                        print(f"收到 {message_count} 条消息...")

                        # 显示最新消息摘要
                        if "o" in data:
                            o = data["o"]
                            print(f"  最新: {o.get('s')} {o.get('S')} {o.get('q')} @ {o.get('p')}")

                except asyncio.TimeoutError:
                    continue  # 继续等待
                except json.JSONDecodeError as e:
                    print(f"JSON解析错误: {e}")
                    continue
                except Exception as e:
                    print(f"处理消息错误: {e}")
                    continue

    except websockets.exceptions.ConnectionClosed as e:
        print(f"连接关闭: {e}")
    except Exception as e:
        print(f"连接错误: {e}")
        return

    elapsed = time.time() - start_time
    print("-" * 60)
    print(f"测试完成!")
    print(f"持续时间: {elapsed:.1f}秒")
    print(f"消息总数: {message_count}")
    print(f"平均速率: {message_count/elapsed:.1f} 条/秒")

    # 显示文件统计
    if filepath.exists():
        with open(filepath, "r") as f:
            lines = f.readlines()
            print(f"文件行数: {len(lines)}")

            # 读取最后几条消息
            if lines:
                print("\n最后3条消息:")
                for line in lines[-3:]:
                    try:
                        msg = json.loads(line.strip())
                        o = msg.get("o", {})
                        print(f"  {o.get('s')} {o.get('S')} {o.get('q')} @ {o.get('p')}")
                    except:
                        print(f"  解析失败")


async def test_agg_trade_stream(duration_seconds: int = 15):
    """
    测试aggTrade流连接
    """
    print(f"\n=== 测试aggTrade流连接 ({duration_seconds}秒) ===")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"WebSocket URL: wss://fstream.binance.com/ws/btcusdt@aggTrade")
    print("-" * 60)

    # 创建数据目录
    data_dir = Path("data/raw/trades") / datetime.now().strftime("%Y-%m-%d")
    data_dir.mkdir(parents=True, exist_ok=True)

    # 创建小时文件
    hour_str = datetime.now().strftime("%H")
    filename = f"{hour_str}.jsonl"
    filepath = data_dir / filename

    print(f"数据文件: {filepath}")

    message_count = 0
    start_time = time.time()

    try:
        # 连接WebSocket
        uri = "wss://fstream.binance.com/ws/btcusdt@aggTrade"
        print(f"正在连接到 {uri}...")

        async with websockets.connect(uri) as websocket:
            print("连接成功！等待消息...")
            print("按 Ctrl+C 停止")

            while time.time() - start_time < duration_seconds:
                try:
                    # 接收消息
                    message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    data = json.loads(message)

                    # 添加本地时间戳
                    local_ts = int(time.time() * 1000)
                    data["local_ts"] = local_ts

                    # 确保Decimal转换
                    if "p" in data:
                        data["p"] = str(Decimal(str(data["p"])))
                    if "q" in data:
                        data["q"] = str(Decimal(str(data["q"])))

                    # 写入文件
                    with open(filepath, "a", encoding="utf-8") as f:
                        f.write(json.dumps(data, ensure_ascii=False) + "\n")

                    message_count += 1

                    # 每收到10条消息打印一次
                    if message_count % 10 == 0:
                        print(f"收到 {message_count} 条消息...")

                        # 显示最新消息摘要
                        print(f"  最新: {data.get('p')} x {data.get('q')} (maker: {data.get('m')})")

                except asyncio.TimeoutError:
                    continue  # 继续等待
                except json.JSONDecodeError as e:
                    print(f"JSON解析错误: {e}")
                    continue
                except Exception as e:
                    print(f"处理消息错误: {e}")
                    continue

    except websockets.exceptions.ConnectionClosed as e:
        print(f"连接关闭: {e}")
    except Exception as e:
        print(f"连接错误: {e}")
        return

    elapsed = time.time() - start_time
    print("-" * 60)
    print(f"测试完成!")
    print(f"持续时间: {elapsed:.1f}秒")
    print(f"消息总数: {message_count}")
    print(f"平均速率: {message_count/elapsed:.1f} 条/秒")


async def main():
    """
    主测试函数
    """
    print("="*60)
    print("Binance WebSocket 连接测试")
    print("="*60)
    print("\n注意: 这将连接到Binance WebSocket API并保存数据到本地文件")
    print("数据将保存在 data/raw/ 目录下")
    print("\n测试顺序:")
    print("1. 清算流 (!forceOrder@arr) - 30秒")
    print("2. aggTrade流 (btcusdt@aggTrade) - 15秒")
    print("-" * 60)

    try:
        # 测试清算流
        await test_liquidation_stream(30)

        # 测试aggTrade流
        await test_agg_trade_stream(15)

        print("\n" + "="*60)
        print("所有测试完成!")
        print("="*60)

        # 显示总结
        print("\n数据文件位置:")
        print(f"  清算数据: data/raw/liquidations/YYYY-MM-DD.jsonl")
        print(f"  交易数据: data/raw/trades/YYYY-MM-DD/HH.jsonl")

        # 验证文件
        data_dir = Path("data/raw")
        if data_dir.exists():
            print(f"\n数据目录内容:")
            for path in data_dir.rglob("*.jsonl"):
                rel_path = path.relative_to(data_dir)
                print(f"  {rel_path}")

    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
    except Exception as e:
        print(f"\n测试出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # 运行测试
    asyncio.run(main())