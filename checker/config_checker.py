"""
VPN Config Checker
Проверяет работоспособность конфигов через xray-core/sing-box
Измеряет реальный пинг до внешнего ресурса
"""

import asyncio
import json
import os
import tempfile
import time
import logging
import base64
import urllib.parse
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# URL для проверки соединения
CHECK_URL = "http://cp.cloudflare.com/generate_204"
CHECK_TIMEOUT = 10  # секунд


@dataclass
class CheckResult:
    raw: str
    protocol: str
    is_alive: bool
    ping_ms: Optional[int] = None
    error: Optional[str] = None
    ip_country: Optional[str] = None


class ConfigChecker:
    def __init__(
        self,
        max_ping_ms: int = 3000,
        concurrency: int = 50,
        xray_path: str = "./xray",
        sing_box_path: str = "./sing-box",
    ):
        self.max_ping_ms = max_ping_ms
        self.concurrency = concurrency
        self.xray_path = xray_path
        self.sing_box_path = sing_box_path
        self._semaphore = asyncio.Semaphore(concurrency)

    async def check_many(self, configs: list[dict]) -> list[CheckResult]:
        """Проверяет список конфигов параллельно"""
        tasks = [self._check_one(c) for c in configs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        checked = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"Check exception: {r}")
            else:
                checked.append(r)

        alive = [r for r in checked if r.is_alive]
        logger.info(f"Checked {len(checked)}, alive: {len(alive)}")
        return checked

    async def _check_one(self, config: dict) -> CheckResult:
        async with self._semaphore:
            protocol = config["protocol"]
            raw = config["raw"]

            try:
                if protocol in ("vmess", "vless", "trojan", "ss", "hysteria2", "tuic"):
                    return await self._check_with_xray(protocol, raw)
                else:
                    # Fallback: TCP-соединение к хосту
                    return await self._check_tcp(protocol, raw)
            except Exception as e:
                return CheckResult(raw=raw, protocol=protocol, is_alive=False, error=str(e))

    # ─────────────────────────────────────────────────────────
    # Проверка через xray-core
    # ─────────────────────────────────────────────────────────

    async def _check_with_xray(self, protocol: str, raw: str) -> CheckResult:
        """
        Запускает временный xray-процесс на случайном порту,
        пробрасывает HTTP-прокси и делает запрос через него.
        """
        port = await self._free_port()
        config_path = None
        process = None

        try:
            xray_config = self._build_xray_config(protocol, raw, port)
            if xray_config is None:
                return CheckResult(raw=raw, protocol=protocol, is_alive=False, error="parse_failed")

            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False
            ) as f:
                json.dump(xray_config, f)
                config_path = f.name

            process = await asyncio.create_subprocess_exec(
                self.xray_path, "run", "-c", config_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

            # Даём xray время подняться
            await asyncio.sleep(1.5)

            start = time.monotonic()
            ok = await self._http_check_via_proxy(port)
            elapsed_ms = int((time.monotonic() - start) * 1000)

            if ok and elapsed_ms <= self.max_ping_ms:
                return CheckResult(raw=raw, protocol=protocol, is_alive=True, ping_ms=elapsed_ms)
            elif ok:
                return CheckResult(raw=raw, protocol=protocol, is_alive=False,
                                   ping_ms=elapsed_ms, error=f"ping {elapsed_ms}ms > limit {self.max_ping_ms}ms")
            else:
                return CheckResult(raw=raw, protocol=protocol, is_alive=False, error="no_response")

        finally:
            if process:
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=3)
                except Exception:
                    process.kill()
            if config_path and os.path.exists(config_path):
                os.unlink(config_path)

    def _build_xray_config(self, protocol: str, raw: str, local_port: int) -> Optional[dict]:
        """Строит конфиг xray из URI"""
        try:
            outbound = self._parse_uri_to_outbound(protocol, raw)
            if not outbound:
                return None

            return {
                "log": {"loglevel": "none"},
                "inbounds": [{
                    "type": "http",
                    "listen": "127.0.0.1",
                    "listen_port": local_port,
                    "tag": "http-in"
                }],
                "outbounds": [outbound, {"type": "freedom", "tag": "direct"}]
            }
        except Exception as e:
            logger.debug(f"Config build error: {e}")
            return None

    def _parse_uri_to_outbound(self, protocol: str, raw: str) -> Optional[dict]:
        """Парсит URI в формат outbound для xray/sing-box"""
        try:
            if protocol == "vmess":
                data = json.loads(base64.b64decode(raw[8:] + '==').decode())
                return {
                    "type": "vmess",
                    "tag": "proxy",
                    "server": data.get("add"),
                    "server_port": int(data.get("port", 443)),
                    "uuid": data.get("id"),
                    "security": data.get("scy", "auto"),
                    "transport": self._vmess_transport(data)
                }

            elif protocol == "vless":
                parsed = urllib.parse.urlparse(raw)
                params = dict(urllib.parse.parse_qsl(parsed.query))
                return {
                    "type": "vless",
                    "tag": "proxy",
                    "server": parsed.hostname,
                    "server_port": parsed.port or 443,
                    "uuid": parsed.username,
                    "flow": params.get("flow", ""),
                    "tls": {"enabled": params.get("security") in ("tls", "reality")}
                }

            elif protocol == "trojan":
                parsed = urllib.parse.urlparse(raw)
                return {
                    "type": "trojan",
                    "tag": "proxy",
                    "server": parsed.hostname,
                    "server_port": parsed.port or 443,
                    "password": parsed.username,
                    "tls": {"enabled": True}
                }

            elif protocol == "ss":
                return self._parse_ss(raw)

            elif protocol == "hysteria2":
                parsed = urllib.parse.urlparse(raw)
                return {
                    "type": "hysteria2",
                    "tag": "proxy",
                    "server": parsed.hostname,
                    "server_port": parsed.port or 443,
                    "password": parsed.username or parsed.password,
                    "tls": {"enabled": True, "insecure": True}
                }

        except Exception as e:
            logger.debug(f"URI parse error ({protocol}): {e}")
            return None

    def _parse_ss(self, raw: str) -> Optional[dict]:
        try:
            body = raw[5:]
            if "@" in body:
                method_pass, rest = body.rsplit("@", 1)
                try:
                    decoded = base64.b64decode(method_pass + '==').decode()
                    method, password = decoded.split(":", 1)
                except Exception:
                    method, password = method_pass.split(":", 1)
                host_port = rest.split("#")[0].split("?")[0]
                host, port = host_port.rsplit(":", 1)
            else:
                decoded = base64.b64decode(body + '==').decode()
                method_pass, host_port = decoded.split("@")
                method, password = method_pass.split(":", 1)
                host, port = host_port.rsplit(":", 1)

            return {
                "type": "shadowsocks",
                "tag": "proxy",
                "server": host,
                "server_port": int(port),
                "method": method,
                "password": password
            }
        except Exception:
            return None

    def _vmess_transport(self, data: dict) -> Optional[dict]:
        net = data.get("net", "tcp")
        if net == "ws":
            return {"type": "ws", "path": data.get("path", "/"), "headers": {"Host": data.get("host", "")}}
        if net == "grpc":
            return {"type": "grpc", "service_name": data.get("path", "")}
        if net == "h2":
            return {"type": "http", "host": [data.get("host", "")], "path": data.get("path", "/")}
        return None

    # ─────────────────────────────────────────────────────────
    # HTTP-проверка через локальный прокси
    # ─────────────────────────────────────────────────────────

    async def _http_check_via_proxy(self, port: int) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-s", "-o", "/dev/null",
                "--proxy", f"http://127.0.0.1:{port}",
                "--max-time", str(CHECK_TIMEOUT),
                "-w", "%{http_code}",
                CHECK_URL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=CHECK_TIMEOUT + 2)
            code = stdout.decode().strip()
            return code in ("204", "200")
        except Exception:
            return False

    # ─────────────────────────────────────────────────────────
    # TCP-проверка (fallback)
    # ─────────────────────────────────────────────────────────

    async def _check_tcp(self, protocol: str, raw: str) -> CheckResult:
        try:
            parsed = urllib.parse.urlparse(raw)
            host = parsed.hostname
            port = parsed.port or 443
            if not host or not port:
                return CheckResult(raw=raw, protocol=protocol, is_alive=False, error="no_host")

            start = time.monotonic()
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=5
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            writer.close()

            alive = elapsed_ms <= self.max_ping_ms
            return CheckResult(
                raw=raw, protocol=protocol,
                is_alive=alive, ping_ms=elapsed_ms,
                error=None if alive else f"ping {elapsed_ms}ms > limit"
            )
        except Exception as e:
            return CheckResult(raw=raw, protocol=protocol, is_alive=False, error=str(e))

    # ─────────────────────────────────────────────────────────
    # Утилиты
    # ─────────────────────────────────────────────────────────

    @staticmethod
    async def _free_port() -> int:
        import socket
        with socket.socket() as s:
            s.bind(("", 0))
            return s.getsockname()[1]
