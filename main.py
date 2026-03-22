"""
Main Pipeline — VPN Config Aggregator
Запускает: сбор → дедупликация → проверка → генерация подписок
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone

from collector.telegram_parser import TelegramCollector
from checker.config_checker import ConfigChecker
from generator.subscription import SubscriptionGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("pipeline")


# ─────────────────────────────────────────────────────────────
# Конфигурация (берётся из env или channels.json)
# ─────────────────────────────────────────────────────────────

def load_channels() -> list[str]:
    path = Path("channels.json")
    if path.exists():
        with open(path) as f:
            return json.load(f)
    # Дефолтный набор публичных каналов с конфигами
    return [
        "@v2ray_configs",
        "@VmessProtocol",
        "@freev2rayssr",
        "@v2rayngvpn",
        "@ConfigsHUB",
        "@PrivateVPNs",
        "@Outline_Vpn",
        "@FreeV2rays",
        "@PrivatProxy",
        "@DirectVPN",
        "@vpn_fail",
        "@V2rayCollectorDonate",
        "@networknim",
        "@mehrosaboran",
        "@iP_CF",
        "@proxystore11",
        "@Shadowsocks_IR",
    ]


async def run_pipeline():
    # ── Параметры ──────────────────────────────────────────────
    api_id       = int(os.environ["TG_API_ID"])
    api_hash     = os.environ["TG_API_HASH"]
    session      = os.environ.get("TG_SESSION", "")
    max_ping     = int(os.environ.get("MAX_PING_MS", "3000"))
    concurrency  = int(os.environ.get("CHECKER_CONCURRENCY", "40"))
    limit        = int(os.environ.get("MESSAGES_PER_CHANNEL", "200"))
    output_dir   = os.environ.get("OUTPUT_DIR", "./output")
    xray_path    = os.environ.get("XRAY_PATH", "./xray")

    channels = load_channels()
    logger.info(f"Channels: {len(channels)}, max_ping: {max_ping}ms, concurrency: {concurrency}")

    # ── 1. Сбор ────────────────────────────────────────────────
    logger.info("=== STEP 1: COLLECTING ===")
    raw_configs = []
    async with TelegramCollector(api_id, api_hash, session) as collector:
        raw_configs = await collector.collect_all(channels, limit_per_channel=limit)
    logger.info(f"Collected total: {len(raw_configs)} configs")

    # ── 2. Дедупликация ────────────────────────────────────────
    logger.info("=== STEP 2: DEDUPLICATION ===")
    seen = set()
    unique_configs = []
    for c in raw_configs:
        if c["raw"] not in seen:
            seen.add(c["raw"])
            unique_configs.append(c)
    logger.info(f"After dedup: {len(unique_configs)} unique configs")

    # ── 3. Проверка ────────────────────────────────────────────
    logger.info("=== STEP 3: CHECKING ===")
    checker = ConfigChecker(
        max_ping_ms=max_ping,
        concurrency=concurrency,
        xray_path=xray_path,
    )
    results = await checker.check_many(unique_configs)

    alive_configs = []
    for result in results:
        if result.is_alive:
            # Ищем исходный конфиг, добавляем ping
            orig = next((c for c in unique_configs if c["raw"] == result.raw), {})
            orig["ping_ms"] = result.ping_ms
            alive_configs.append(orig)

    # Сортировка по пингу
    alive_configs.sort(key=lambda x: x.get("ping_ms") or 9999)

    logger.info(f"Alive: {len(alive_configs)} / {len(unique_configs)}")

    # Сохраняем полный список живых конфигов
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    with open(f"{output_dir}/alive_configs.json", "w") as f:
        json.dump(alive_configs, f, ensure_ascii=False, indent=2, default=str)

    # ── 4. Генерация подписок ──────────────────────────────────
    logger.info("=== STEP 4: GENERATING SUBSCRIPTIONS ===")
    gen = SubscriptionGenerator(output_dir=output_dir)
    files = gen.generate_all(alive_configs)

    # ── Итог ───────────────────────────────────────────────────
    logger.info("=== DONE ===")
    logger.info(f"  Collected:   {len(raw_configs)}")
    logger.info(f"  Unique:      {len(unique_configs)}")
    logger.info(f"  Alive:       {len(alive_configs)}")
    logger.info(f"  Output dir:  {output_dir}")
    for fmt, path in files.items():
        logger.info(f"  [{fmt}] → {path}")


if __name__ == "__main__":
    asyncio.run(run_pipeline())
