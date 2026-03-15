"""
Binance WebSocket 数据采集器
支持 liquidation 流 (!forceOrder@arr) 和 aggTrade 流 (btcusdt@aggTrade)
自动重连、指数退避、JSONL存储、流量统计
"""

import argparse
import asyncio
import json
import logging
import platform
import signal
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional, Set

import websockets

# ==================== 配置 ====================
BINANCE_WS_BASE = "wss://fstream.binance.com/ws"
RECONNECT_DELAYS = [1, 2, 4, 8, 16, 30]  # 重连指数退避（秒）
STATS_INTERVAL = 60           # 统计间隔（秒）
DEPTH_WRITE_INTERVAL = 1.0    # 订单簿限速：每秒最多写1条（原始100ms → 1Hz）
DATA_ROOT = Path("data/raw")

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


class BinanceWSCollector:
    """Binance WebSocket 数据采集器"""

    def __init__(self, streams: Set[str]):
        """
        初始化采集器

        Args:
            streams: 要订阅的流集合 (e.g., {"liquidations", "trades"})
        """
        self.streams = streams
        self.websocket: Optional[Any] = None
        self.running = False

        # 消息计数
        self.msg_counts: Dict[str, int] = {s: 0 for s in streams}
        self.stats_start_time = time.time()
        self.last_stats_time = time.time()

        # 统计任务
        self.stats_task: asyncio.Task | None = None

        # depth 限速：记录上次写入时间（秒）
        self._last_depth_write: float = 0.0

        # 流名称映射
        self.stream_mapping = {
            "liquidations": "!forceOrder@arr",
            "trades": "btcusdt@aggTrade",
            "depth": "btcusdt@depth@100ms",   # 订单簿差量更新，100ms频率
        }

        # 创建数据目录
        self._setup_data_dirs()

        logger.info(f"初始化采集器, 订阅流: {streams}")

    def _setup_data_dirs(self):
        """创建数据存储目录"""
        # 创建基础目录
        DATA_ROOT.mkdir(parents=True, exist_ok=True)

        # 创建流类型目录
        for stream_type in ["liquidations", "trades", "depth"]:
            stream_dir = DATA_ROOT / stream_type
            stream_dir.mkdir(parents=True, exist_ok=True)

    def _get_stream_name(self, stream_key: str) -> str:
        """获取WebSocket流名称"""
        return self.stream_mapping.get(stream_key, stream_key)

    def _get_output_path(self, stream_key: str, msg_data: dict) -> Path:
        """
        获取输出文件路径（均使用UTC时间，避免跨日读写不一致）
        liquidations: data/raw/liquidations/YYYY-MM-DD.jsonl
        trades:       data/raw/trades/YYYY-MM-DD/HH.jsonl
        depth:        data/raw/depth/YYYY-MM-DD/HH.jsonl
        """
        now = datetime.now(timezone.utc)

        if stream_key == "liquidations":
            date_str = now.strftime("%Y-%m-%d")
            return DATA_ROOT / "liquidations" / f"{date_str}.jsonl"
        elif stream_key in ("trades", "depth"):
            date_str = now.strftime("%Y-%m-%d")
            hour_str = now.strftime("%H")
            return DATA_ROOT / stream_key / date_str / f"{hour_str}.jsonl"

        raise ValueError(f"未知流类型: {stream_key}")

    def _ensure_dir(self, file_path: Path) -> None:
        """确保目录存在"""
        file_path.parent.mkdir(parents=True, exist_ok=True)

    def _convert_decimal(self, data: dict) -> dict:
        """转换价格和数量为Decimal类型"""
        converted = data.copy()

        # liquidation: o.p (价格), o.q (数量)
        if "o" in converted:
            if "p" in converted["o"]:
                converted["o"]["p"] = str(Decimal(str(converted["o"]["p"])))
            if "q" in converted["o"]:
                converted["o"]["q"] = str(Decimal(str(converted["o"]["q"])))

        # aggTrade: p (价格), q (数量)
        if "p" in converted:
            converted["p"] = str(Decimal(str(converted["p"])))
        if "q" in converted:
            converted["q"] = str(Decimal(str(converted["q"])))

        return converted

    def _process_message(self, stream_key: str, msg: dict) -> None:
        """处理接收到的消息，写入JSONL文件"""
        try:
            # 订单簿限速：100ms原始频率 → 写入1Hz，节省磁盘
            if stream_key == "depth":
                now_t = time.time()
                if now_t - self._last_depth_write < DEPTH_WRITE_INTERVAL:
                    return
                self._last_depth_write = now_t

            # 服务器时间戳: liquidation用E, aggTrade/depth用T或E
            server_ts = msg.get("T") or msg.get("E")
            # 本地时间戳（毫秒）
            local_ts = int(time.time() * 1000)

            # 转换Decimal
            data = self._convert_decimal(msg)

            # 构建记录
            record = {
                "local_ts": local_ts,
                "server_ts": server_ts,
                "data": data
            }

            # 写入文件
            output_path = self._get_output_path(stream_key, data)
            self._ensure_dir(output_path)

            with open(output_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            self.msg_counts[stream_key] += 1

        except Exception as e:
            logger.error(f"处理消息失败: {e}", exc_info=True)

    async def _stats_loop(self) -> None:
        """统计任务：每60秒输出消息速率"""
        while self.running:
            await asyncio.sleep(STATS_INTERVAL)

            if not self.running:
                break

            now = time.time()
            elapsed = now - self.last_stats_time

            if elapsed > 0:
                for stream_key, count in self.msg_counts.items():
                    rate = count / elapsed * 60
                    logger.info(f"[Stats] {stream_key}: {rate:.1f} msgs/min (total: {count})")

                self.msg_counts = {s: 0 for s in self.streams}
                self.last_stats_time = now

    async def _connect_with_reconnect(self):
        """连接WebSocket，带指数退避重连"""
        delay_idx = 0
        last_error = None

        while delay_idx < len(RECONNECT_DELAYS):
            try:
                params = [self._get_stream_name(s) for s in self.streams]
                subscribe_msg = {
                    "method": "SUBSCRIBE",
                    "params": params,
                    "id": 1
                }

                logger.info(f"连接WebSocket: {BINANCE_WS_BASE}")
                ws = await websockets.connect(
                    BINANCE_WS_BASE,
                    ping_interval=20,
                    ping_timeout=10
                )

                await ws.send(json.dumps(subscribe_msg))
                logger.info(f"已订阅: {params}")

                return ws

            except Exception as e:
                last_error = e
                delay = RECONNECT_DELAYS[delay_idx]
                logger.warning(f"连接失败 ({delay_idx+1}/{len(RECONNECT_DELAYS)}): {e}, {delay}秒后重试...")
                await asyncio.sleep(delay)
                delay_idx += 1

        raise Exception(f"重连次数超限: {last_error}")

    async def _handle_message(self, message: str) -> None:
        """处理WebSocket消息"""
        try:
            msg = json.loads(message)

            # 忽略订阅确认/心跳
            if "result" in msg or msg.get("e") is None:
                return

            event_type = msg.get("e")

            if event_type == "forceOrder":
                if "liquidations" in self.streams:
                    self._process_message("liquidations", msg)
            elif event_type == "aggTrade":
                if "trades" in self.streams:
                    self._process_message("trades", msg)
            elif event_type == "depthUpdate":
                if "depth" in self.streams:
                    self._process_message("depth", msg)

        except json.JSONDecodeError as e:
            logger.warning(f"JSON解析失败: {e}")
        except Exception as e:
            logger.error(f"处理消息异常: {e}", exc_info=True)

    async def run(self) -> None:
        """运行采集器"""
        self.running = True
        self.stats_task = asyncio.create_task(self._stats_loop())

        while self.running:
            try:
                self.websocket = await self._connect_with_reconnect()

                async for message in self.websocket:
                    if not self.running:
                        break
                    await self._handle_message(message)

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"连接关闭: {e}, 尝试重连...")
            except Exception as e:
                logger.error(f"WebSocket异常: {e}, 尝试重连...")
                await asyncio.sleep(1)

        if self.websocket:
            await self.websocket.close()

    async def stop(self) -> None:
        """停止采集器"""
        logger.info("正在停止采集器...")
        self.running = False

        if self.stats_task and not self.stats_task.done():
            self.stats_task.cancel()
            try:
                await self.stats_task
            except asyncio.CancelledError:
                pass

        if self.websocket:
            await self.websocket.close()

        # 最终统计
        elapsed = time.time() - self.stats_start_time
        if elapsed > 0:
            for stream_key, count in self.msg_counts.items():
                rate = count / elapsed * 60
                logger.info(f"[Final] {stream_key}: {rate:.1f} msgs/min, total: {count}")

        logger.info("采集器已停止")


def parse_streams(streams_str: str) -> Set[str]:
    """解析流参数字符串"""
    valid_streams = {"liquidations", "trades", "depth"}
    streams = {s.strip().lower() for s in streams_str.split(",")}

    invalid = streams - valid_streams
    if invalid:
        raise ValueError(f"无效的流: {invalid}, 有效值: {valid_streams}")

    if not streams:
        raise ValueError("必须指定至少一个流")

    return streams


def parse_duration(duration_str: str) -> int:
    """解析持续时间字符串，支持秒数或分钟数"""
    duration_str = duration_str.strip().lower()

    if duration_str.endswith('m'):
        # 分钟
        minutes = int(duration_str[:-1])
        return minutes * 60
    elif duration_str.endswith('h'):
        # 小时
        hours = int(duration_str[:-1])
        return hours * 3600
    else:
        # 秒数
        return int(duration_str)


async def run_collector_with_duration(collector, duration_seconds: int):
    """运行采集器指定时间"""
    # 启动采集器
    run_task = asyncio.create_task(collector.run())

    # 等待指定时间
    try:
        await asyncio.sleep(duration_seconds)
    finally:
        # 停止采集器
        await collector.stop()
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description="Binance WebSocket数据采集器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m src.data.websocket --streams liquidations
  python -m src.data.websocket --streams trades
  python -m src.data.websocket --streams depth
  python -m src.data.websocket --streams liquidations,trades,depth
  python -m src.data.websocket --streams liquidations --duration 300
  python -m src.data.websocket --streams liquidations --duration 5m
  python -m src.data.websocket --streams liquidations,trades,depth --duration 1h
        """
    )
    parser.add_argument(
        "--streams",
        type=str,
        required=True,
        help="要采集的流，逗号分隔 (liquidations,trades)"
    )
    parser.add_argument(
        "--duration",
        type=str,
        default=None,
        help="运行时间，支持秒数(300)、分钟(5m)、小时(1h)，默认无限运行"
    )

    args = parser.parse_args()

    try:
        streams = parse_streams(args.streams)
    except ValueError as e:
        parser.error(str(e))

    # 解析duration
    if args.duration:
        try:
            duration_seconds = parse_duration(args.duration)
            logger.info(f"运行时间: {args.duration} ({duration_seconds}秒)")
        except ValueError as e:
            parser.error(f"无效的duration格式: {args.duration}")
    else:
        duration_seconds = None
        logger.info("运行时间: 无限运行 (Ctrl+C停止)")

    collector = BinanceWSCollector(streams)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def shutdown():
        logger.info("正在停止采集器...")
        await collector.stop()
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def signal_handler(*_):
        loop.create_task(shutdown())

    # Windows 不支持 loop.add_signal_handler，退化为 signal.signal
    if platform.system() != "Windows":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)
    else:
        signal.signal(signal.SIGINT, signal_handler)

    try:
        if duration_seconds:
            loop.run_until_complete(run_collector_with_duration(collector, duration_seconds))
        else:
            loop.run_until_complete(collector.run())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"运行错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        loop.run_until_complete(asyncio.sleep(0.1))
        if not loop.is_closed():
            loop.close()


if __name__ == "__main__":
    main()
