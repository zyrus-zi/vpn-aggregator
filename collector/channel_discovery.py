"""
Channel Discovery
Автоматически находит VPN-каналы двумя методами:
  1. searchGlobal — поиск по ключевым словам (как в плагине explore_search)
  2. Граф упоминаний — BFS по ссылкам между каналами

Алгоритм ранжирования портирован из explore_search.plugin (@mishabotov):
  score = совпадение_запроса + hits*6 + свежесть + участники
"""

import asyncio
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    FloodWaitError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.functions import messages as tl_messages
from telethon.functions import contacts as tl_contacts
from telethon.tl import types as tl_types

logger = logging.getLogger(__name__)

# ── Ключевые слова для поиска ──────────────────────────────────────────────────
SEARCH_QUERIES = [
    "vmess vless",
    "trojan shadowsocks",
    "v2ray config",
    "xray конфиг",
    "vless trojan free",
    "shadowsocks proxy",
    "hysteria2 tuic",
    "free vpn config",
]

# Слова, которые должны встречаться в названии/описании канала
CHANNEL_KEYWORDS = re.compile(
    r"v2ray|vmess|vless|trojan|shadowsocks|xray|proxy|vpn|tunnel|"
    r"конфи|прокси|впн|outline|clash|sing.?box|hysteria|tuic|ssr|wireguard",
    re.IGNORECASE,
)

# Паттерн для извлечения @username из текста сообщений
MENTION_PATTERN = re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,31})")

# Паттерн для конфигов — если канал их постит, он нам нужен
CONFIG_PATTERN = re.compile(
    r"(vmess|vless|trojan|ss|ssr|hy2|hysteria2|tuic|wireguard)://[^\s<>\"']{10,}"
)


@dataclass
class ChannelCandidate:
    username: str
    title: str = ""
    description: str = ""
    members: int = 0
    # Сколько раз встретился в результатах поиска/упоминаниях
    hits: int = 0
    # Дата последнего поста (unix timestamp)
    last_post: int = 0
    # Сколько конфигов найдено в последних сообщениях
    config_count: int = 0
    score: float = 0.0
    source: str = ""  # "search" | "mention" | "seed"


class ChannelDiscovery:
    def __init__(
        self,
        client: TelegramClient,
        seed_channels: list[str],
        max_channels: int = 300,
        bfs_depth: int = 3,
        min_score: float = 20.0,
        messages_to_scan: int = 30,
        flood_delay: float = 1.5,
    ):
        self.client = client
        self.seed_channels = [c.lstrip("@") for c in seed_channels]
        self.max_channels = max_channels
        self.bfs_depth = bfs_depth
        self.min_score = min_score
        self.messages_to_scan = messages_to_scan
        self.flood_delay = flood_delay

        self._visited: set[str] = set()
        self._candidates: dict[str, ChannelCandidate] = {}

    # ─────────────────────────────────────────────────────────────────────────
    # Публичный метод
    # ─────────────────────────────────────────────────────────────────────────

    async def discover(self) -> list[str]:
        """
        Возвращает список @username найденных VPN-каналов,
        отсортированных по убыванию score.
        """
        logger.info("=== Channel Discovery started ===")

        # Шаг 1: добавляем сиды
        for username in self.seed_channels:
            self._upsert(username, hits=5, source="seed")

        # Шаг 2: searchGlobal по ключевым словам
        await self._search_global_phase()

        # Шаг 3: BFS по графу упоминаний
        await self._bfs_mention_phase()

        # Шаг 4: финальная оценка — сканируем сообщения кандидатов
        await self._score_candidates()

        # Фильтрация и сортировка
        result = [
            f"@{c.username}"
            for c in sorted(self._candidates.values(), key=lambda x: -x.score)
            if c.score >= self.min_score and c.config_count > 0
        ]

        logger.info(f"Discovery complete: {len(result)} channels found")
        return result[: self.max_channels]

    # ─────────────────────────────────────────────────────────────────────────
    # Шаг 2: searchGlobal
    # Портировано из explore_search.plugin: _request_global_source()
    # ─────────────────────────────────────────────────────────────────────────

    async def _search_global_phase(self):
        logger.info(f"Global search phase: {len(SEARCH_QUERIES)} queries")

        for query in SEARCH_QUERIES:
            await self._throttle()
            try:
                await self._search_global_query(query)
            except FloodWaitError as e:
                logger.warning(f"FloodWait {e.seconds}s on query '{query}', sleeping...")
                await asyncio.sleep(e.seconds + 5)
            except Exception as e:
                logger.warning(f"searchGlobal error for '{query}': {e}")

    async def _search_global_query(self, query: str, pages: int = 5):
        """
        messages.searchGlobal с broadcasts_only=True (только каналы).
        Пагинация через offset_rate/offset_peer/offset_id — точно как в плагине.
        """
        offset_rate = 0
        offset_peer = tl_types.InputPeerEmpty()
        offset_id = 0
        hits_by_channel: dict[str, int] = defaultdict(int)
        last_date_by_channel: dict[str, int] = {}

        for page in range(pages):
            try:
                result = await self.client(
                    tl_messages.SearchGlobalRequest(
                        q=query,
                        filter=tl_types.InputMessagesFilterEmpty(),
                        min_date=0,
                        max_date=0,
                        offset_rate=offset_rate,
                        offset_peer=offset_peer,
                        offset_id=offset_id,
                        limit=36,  # _GLOBAL_REQUEST_LIMIT из плагина
                        # broadcasts_only — только каналы-броадкасты
                        broadcasts_only=True,
                        groups_only=False,
                    )
                )
            except Exception as e:
                logger.debug(f"searchGlobal page {page} error: {e}")
                break

            messages = getattr(result, "messages", [])
            chats = {c.id: c for c in getattr(result, "chats", [])}

            if not messages:
                break

            for msg in messages:
                peer = getattr(msg, "peer_id", None)
                channel_id = getattr(peer, "channel_id", None)
                if not channel_id:
                    continue

                chat = chats.get(channel_id)
                if not chat:
                    continue

                username = getattr(chat, "username", None)
                if not username:
                    continue

                username = username.lower()
                hits_by_channel[username] += 1
                msg_date = int(getattr(msg, "date", 0) or 0)
                last_date_by_channel[username] = max(
                    last_date_by_channel.get(username, 0), msg_date
                )

                title = getattr(chat, "title", "") or ""
                description = getattr(
                    getattr(chat, "about", None), "__str__", lambda: ""
                )()
                members = getattr(
                    getattr(chat, "participants_count", None), "__int__", lambda: 0
                )()

                self._upsert(
                    username,
                    title=title,
                    members=members,
                    source="search",
                )

            # Пагинация — берём offset из последнего сообщения (как в плагине)
            last = messages[-1]
            offset_rate = getattr(result, "next_rate", 0) or 0
            offset_id = getattr(last, "id", 0) or 0
            peer = getattr(last, "peer_id", None)
            if peer:
                channel_id = getattr(peer, "channel_id", None)
                if channel_id:
                    offset_peer = tl_types.InputPeerChannel(
                        channel_id=channel_id, access_hash=0
                    )

            if not offset_rate:
                break

            await self._throttle()

        # Применяем накопленные hits и last_date
        for username, hits in hits_by_channel.items():
            c = self._candidates.get(username)
            if c:
                c.hits += hits
                c.last_post = max(c.last_post, last_date_by_channel.get(username, 0))

        logger.info(
            f"  query='{query}': found {len(hits_by_channel)} channels"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Шаг 3: BFS по упоминаниям
    # ─────────────────────────────────────────────────────────────────────────

    async def _bfs_mention_phase(self):
        logger.info(f"BFS mention phase: depth={self.bfs_depth}")

        queue = list(self._candidates.keys())
        current_depth = 0

        while queue and current_depth < self.bfs_depth:
            next_queue = []
            logger.info(
                f"  BFS depth {current_depth}: processing {len(queue)} channels"
            )

            for username in queue:
                if username in self._visited:
                    continue
                self._visited.add(username)

                if len(self._candidates) >= self.max_channels * 2:
                    break

                mentions = await self._extract_mentions(username)
                for mentioned in mentions:
                    if mentioned not in self._candidates:
                        self._upsert(mentioned, source="mention")
                        next_queue.append(mentioned)
                    else:
                        self._candidates[mentioned].hits += 1

                await self._throttle()

            queue = next_queue
            current_depth += 1

    async def _extract_mentions(self, username: str) -> list[str]:
        """Извлекает @упоминания каналов из последних сообщений"""
        found = []
        try:
            entity = await self.client.get_entity(f"@{username}")
            async for msg in self.client.iter_messages(entity, limit=50):
                text = getattr(msg, "text", "") or ""
                for m in MENTION_PATTERN.finditer(text):
                    mentioned = m.group(1).lower()
                    if mentioned != username:
                        found.append(mentioned)

                # Также смотрим в описании (forwarded channel info)
                fwd = getattr(msg, "fwd_from", None)
                if fwd:
                    fwd_name = getattr(
                        getattr(fwd, "from_name", None), "__str__", lambda: ""
                    )()

        except (ChannelPrivateError, UsernameInvalidError, UsernameNotOccupiedError):
            pass
        except FloodWaitError as e:
            logger.warning(f"FloodWait {e.seconds}s on @{username}, sleeping...")
            await asyncio.sleep(e.seconds + 3)
        except Exception as e:
            logger.debug(f"_extract_mentions @{username}: {e}")

        return list(set(found))

    # ─────────────────────────────────────────────────────────────────────────
    # Шаг 4: Финальная оценка кандидатов
    # Портировано из _build_global_ranked_results() плагина
    # ─────────────────────────────────────────────────────────────────────────

    async def _score_candidates(self):
        logger.info(f"Scoring {len(self._candidates)} candidates...")
        now = int(time.time())

        for username, candidate in list(self._candidates.items()):
            await self._throttle(0.3)

            try:
                entity = await self.client.get_entity(f"@{username}")
            except Exception:
                candidate.score = 0
                continue

            # Обновляем метаданные из entity
            candidate.title = getattr(entity, "title", "") or candidate.title
            candidate.members = (
                getattr(entity, "participants_count", 0) or candidate.members
            )
            about = getattr(entity, "about", "") or ""

            # Сканируем последние сообщения на наличие конфигов
            config_count = 0
            try:
                async for msg in self.client.iter_messages(
                    entity, limit=self.messages_to_scan
                ):
                    text = getattr(msg, "text", "") or ""
                    configs = CONFIG_PATTERN.findall(text)
                    config_count += len(configs)

                    # Обновляем last_post
                    msg_date = int(getattr(msg, "date", 0) or 0)
                    candidate.last_post = max(candidate.last_post, msg_date)
            except Exception:
                pass

            candidate.config_count = config_count

            # ── Алгоритм scoring из плагина ───────────────────────────────
            score = 0.0

            # 1. Совпадение ключевых слов в названии/описании
            text_to_check = f"{candidate.title} {about}"
            if CHANNEL_KEYWORDS.search(text_to_check):
                score += 30

            # 2. Hits из searchGlobal (плагин: score += 12 + min(34, hits*6))
            score += 12 + min(34, candidate.hits * 6)

            # 3. Свежесть: если постили за последние 7 дней
            days_ago = (now - candidate.last_post) / 86400
            if days_ago < 1:
                score += 20
            elif days_ago < 7:
                score += 10
            elif days_ago < 30:
                score += 3

            # 4. Число конфигов в последних N сообщениях
            score += min(50, config_count * 5)

            # 5. Популярность канала (участники)
            if candidate.members > 50_000:
                score += 15
            elif candidate.members > 10_000:
                score += 10
            elif candidate.members > 1_000:
                score += 5

            # 6. Источник
            if candidate.source == "seed":
                score += 10

            candidate.score = score
            logger.debug(
                f"  @{username}: score={score:.0f} configs={config_count} "
                f"hits={candidate.hits} members={candidate.members}"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Утилиты
    # ─────────────────────────────────────────────────────────────────────────

    def _upsert(
        self,
        username: str,
        title: str = "",
        members: int = 0,
        hits: int = 1,
        source: str = "",
    ):
        username = username.lower().lstrip("@")
        if not username or len(username) < 4:
            return
        if username in self._candidates:
            c = self._candidates[username]
            c.hits += hits
            if title:
                c.title = title
            if members:
                c.members = members
        else:
            self._candidates[username] = ChannelCandidate(
                username=username,
                title=title,
                members=members,
                hits=hits,
                source=source,
            )

    async def _throttle(self, delay: float = None):
        """Задержка между запросами — защита от FloodWait"""
        await asyncio.sleep(delay if delay is not None else self.flood_delay)

    def get_candidates_report(self) -> list[dict]:
        """Возвращает детальный отчёт по всем кандидатам для отладки"""
        return [
            {
                "username": f"@{c.username}",
                "title": c.title,
                "members": c.members,
                "hits": c.hits,
                "config_count": c.config_count,
                "score": round(c.score, 1),
                "source": c.source,
            }
            for c in sorted(self._candidates.values(), key=lambda x: -x.score)
        ]
