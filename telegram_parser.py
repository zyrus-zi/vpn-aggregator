"""
Telegram VPN Config Collector
Парсит публичные Telegram-каналы и извлекает VPN конфиги
"""

import asyncio
import re
import base64
import logging
from typing import AsyncGenerator
from telethon import TelegramClient
from telethon.tl.types import Message

logger = logging.getLogger(__name__)

# Паттерны для поиска конфигов
CONFIG_PATTERNS = {
    "vmess":    re.compile(r'vmess://[A-Za-z0-9+/=]+'),
    "vless":    re.compile(r'vless://[^\s<>"\']+'),
    "trojan":   re.compile(r'trojan://[^\s<>"\']+'),
    "ss":       re.compile(r'ss://[A-Za-z0-9+/=:#@.\-_\[\]?&]+'),
    "ssr":      re.compile(r'ssr://[A-Za-z0-9+/=]+'),
    "hysteria2":re.compile(r'hy2://[^\s<>"\']+'),
    "hysteria": re.compile(r'hysteria://[^\s<>"\']+'),
    "tuic":     re.compile(r'tuic://[^\s<>"\']+'),
    "wg":       re.compile(r'wireguard://[^\s<>"\']+'),
}

# Паттерн для base64-подписок (список конфигов)
SUBSCRIPTION_PATTERN = re.compile(
    r'(?:https?://)[^\s<>"\']+(?:sub|subscribe|subscription|config|clash|v2ray)[^\s<>"\']*',
    re.IGNORECASE
)


class TelegramCollector:
    def __init__(self, api_id: int, api_hash: str, session_string: str = None):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_string = session_string
        self.client = None

    async def __aenter__(self):
        from telethon.sessions import StringSession
        self.client = TelegramClient(
            StringSession(self.session_string),
            self.api_id,
            self.api_hash
        )
        await self.client.connect()
        return self

    async def __aexit__(self, *args):
        if self.client:
            await self.client.disconnect()

    async def collect_from_channel(
        self,
        channel: str,
        limit: int = 100
    ) -> AsyncGenerator[dict, None]:
        """
        Собирает конфиги из одного канала
        Возвращает dict: { protocol, raw, source_channel, message_id, date }
        """
        try:
            async for message in self.client.iter_messages(channel, limit=limit):
                if not isinstance(message, Message) or not message.text:
                    continue

                text = message.text
                found = self._extract_configs(text)

                for protocol, raw in found:
                    yield {
                        "protocol": protocol,
                        "raw": raw,
                        "source_channel": channel,
                        "message_id": message.id,
                        "date": message.date.isoformat() if message.date else None,
                    }

        except Exception as e:
            logger.error(f"Error collecting from {channel}: {e}")

    def _extract_configs(self, text: str) -> list[tuple[str, str]]:
        """Извлекает все конфиги из текста"""
        results = []

        for protocol, pattern in CONFIG_PATTERNS.items():
            for match in pattern.finditer(text):
                raw = match.group(0).strip()
                if self._basic_validate(protocol, raw):
                    results.append((protocol, raw))

        # Попытка декодировать base64-блоки (бывают в сообщениях)
        b64_blocks = re.findall(r'(?<![A-Za-z0-9+/])([A-Za-z0-9+/]{60,}={0,2})(?![A-Za-z0-9+/=])', text)
        for block in b64_blocks:
            try:
                decoded = base64.b64decode(block + '==').decode('utf-8', errors='ignore')
                sub_results = self._extract_configs(decoded)
                results.extend(sub_results)
            except Exception:
                pass

        return results

    def _basic_validate(self, protocol: str, raw: str) -> bool:
        """Базовая синтаксическая валидация"""
        if len(raw) < 20:
            return False

        if protocol == "vmess":
            try:
                data = raw.replace("vmess://", "")
                base64.b64decode(data + '==')
                return True
            except Exception:
                return False

        if protocol in ("vless", "trojan", "hysteria2", "tuic"):
            return "@" in raw and ":" in raw

        if protocol == "ss":
            return len(raw) > 10

        return True

    async def collect_all(
        self,
        channels: list[str],
        limit_per_channel: int = 200
    ) -> list[dict]:
        """Собирает конфиги со всех каналов"""
        all_configs = []
        for channel in channels:
            logger.info(f"Collecting from {channel}...")
            async for config in self.collect_from_channel(channel, limit=limit_per_channel):
                all_configs.append(config)
            logger.info(f"  → {channel}: collected so far {len(all_configs)} total")

        return all_configs
