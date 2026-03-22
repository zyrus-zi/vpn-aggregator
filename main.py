"""
Main Pipeline — VPN Config Aggregator
Запускает: discovery каналов → сбор → дедупликация → проверка → генерация подписок
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from collector.telegram_parser import TelegramCollector
from collector.channel_discovery import ChannelDiscovery
from checker.config_checker import ConfigChecker
from generator.subscription import SubscriptionGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline")


def load_channels() -> list[str]:
    path = Path("channels.json")
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return [
        "@v2ray_configs", "@VmessProtocol", "@freev2rayssr",
        "@v2rayngvpn", "@ConfigsHUB", "@FreeV2rays",
        "@Outline_Vpn", "@DirectVPN", "@vpn_fail",
        "@networknim", "@iP_CF", "@Shadowsocks_IR",
    ]


async def run_pipeline():
    api_id      = int(os.environ["TG_API_ID"])
    api_hash    = os.environ["TG_API_HASH"]
    session     = os.environ.get("TG_SESSION", "")
    max_ping    = int(os.environ.get("MAX_PING_MS", "3000"))
    concurrency = int(os.environ.get("CHECKER_CONCURRENCY", "40"))
    limit       = int(os.environ.get("MESSAGES_PER_CHANNEL", "200"))
    output_dir  = os.environ.get("OUTPUT_DIR", "./output")
    xray_path   = os.environ.get("XRAY_PATH", "./xray")
    run_discovery  = os.environ.get("RUN_DISCOVERY", "true").lower() == "true"
    max_discovered = int(os.environ.get("MAX_DISCOVERED_CHANNELS", "300"))
    bfs_depth      = int(os.environ.get("BFS_DEPTH", "3"))

    seed_channels = load_channels()
    logger.info(f"Seed channels: {len(seed_channels)}, max_ping={max_ping}ms")

    async with TelegramCollector(api_id, api_hash, session) as collector:

        # ── 1. Автоматический поиск каналов ───────────────────────────────
        if run_discovery:
            logger.info("=== STEP 1: CHANNEL DISCOVERY ===")
            discovery = ChannelDiscovery(
                client=collector.client,
                seed_channels=seed_channels,
                max_channels=max_discovered,
                bfs_depth=bfs_depth,
                min_score=20.0,
                messages_to_scan=30,
                flood_delay=1.5,
            )
            discovered = await discovery.discover()

            Path(output_dir).mkdir(parents=True, exist_ok=True)
            with open(f"{output_dir}/discovery_report.json", "w") as f:
                json.dump(discovery.get_candidates_report(), f, ensure_ascii=False, indent=2)

            all_channels = list(dict.fromkeys(seed_channels + discovered))
            logger.info(f"Discovery: {len(discovered)} new → total {len(all_channels)} channels")

            # Сохраняем обновлённый список для следующего запуска
            with open("channels.json", "w") as f:
                json.dump(all_channels, f, ensure_ascii=False, indent=2)
        else:
            all_channels = seed_channels
            logger.info(f"Discovery skipped, using {len(all_channels)} channels")

        # ── 2. Сбор конфигов ───────────────────────────────────────────────
        logger.info("=== STEP 2: COLLECTING ===")
        raw_configs = await collector.collect_all(all_channels, limit_per_channel=limit)
        logger.info(f"Collected: {len(raw_configs)}")

    # ── 3. Дедупликация ────────────────────────────────────────────────────
    logger.info("=== STEP 3: DEDUPLICATION ===")
    seen = set()
    unique_configs = []
    for c in raw_configs:
        if c["raw"] not in seen:
            seen.add(c["raw"])
            unique_configs.append(c)
    logger.info(f"Unique: {len(unique_configs)}")

    # ── 4. Проверка ────────────────────────────────────────────────────────
    logger.info("=== STEP 4: CHECKING ===")
    checker = ConfigChecker(max_ping_ms=max_ping, concurrency=concurrency, xray_path=xray_path)
    results = await checker.check_many(unique_configs)

    raw_to_orig = {c["raw"]: c for c in unique_configs}
    alive_configs = []
    for result in results:
        if result.is_alive:
            orig = raw_to_orig.get(result.raw, {}).copy()
            orig["ping_ms"] = result.ping_ms
            alive_configs.append(orig)

    alive_configs.sort(key=lambda x: x.get("ping_ms") or 9999)
    logger.info(f"Alive: {len(alive_configs)} / {len(unique_configs)}")

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    with open(f"{output_dir}/alive_configs.json", "w") as f:
        json.dump(alive_configs, f, ensure_ascii=False, indent=2, default=str)

    # ── 5. Генерация подписок ──────────────────────────────────────────────
    logger.info("=== STEP 5: GENERATING SUBSCRIPTIONS ===")
    gen = SubscriptionGenerator(output_dir=output_dir)
    files = gen.generate_all(alive_configs)

    logger.info("=== DONE ===")
    logger.info(f"  Channels: {len(all_channels)}  Collected: {len(raw_configs)}  "
                f"Unique: {len(unique_configs)}  Alive: {len(alive_configs)}")
    for fmt, path in files.items():
        logger.info(f"  [{fmt}] → {path}")


if __name__ == "__main__":
    asyncio.run(run_pipeline())
