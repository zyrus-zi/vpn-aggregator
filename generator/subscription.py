"""
Subscription Generator
Генерирует подписки в форматах: base64 (universal), Clash YAML, sing-box JSON
"""

import base64
import json
import re
import urllib.parse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SubscriptionGenerator:
    def __init__(self, output_dir: str = "./output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(self, alive_configs: list[dict]) -> dict[str, str]:
        """
        Генерирует все форматы подписок.
        Возвращает dict: { format_name -> filepath }
        """
        raws = [c["raw"] for c in alive_configs]
        files = {}

        # 1. Universal base64 (V2RayNG, Hiddify, Streisand, etc.)
        files["base64"] = self._write_base64(raws)

        # 2. Clash / Mihomo YAML
        files["clash"] = self._write_clash(alive_configs)

        # 3. sing-box JSON
        files["singbox"] = self._write_singbox(alive_configs)

        # 4. Статистика (для README и бейджей)
        files["meta"] = self._write_meta(alive_configs)

        logger.info(f"Generated subscriptions: {list(files.keys())}")
        return files

    # ─────────────────────────────────────────────────────────
    # Universal Base64
    # ─────────────────────────────────────────────────────────

    def _write_base64(self, raws: list[str]) -> str:
        content = "\n".join(raws)
        encoded = base64.b64encode(content.encode()).decode()

        path = self.output_dir / "sub.txt"
        path.write_text(encoded, encoding="utf-8")
        logger.info(f"base64 sub: {len(raws)} configs → {path}")
        return str(path)

    # ─────────────────────────────────────────────────────────
    # Clash / Mihomo YAML
    # ─────────────────────────────────────────────────────────

    def _write_clash(self, configs: list[dict]) -> str:
        proxies = []
        for c in configs:
            proxy = self._config_to_clash_proxy(c)
            if proxy:
                proxies.append(proxy)

        if not proxies:
            return ""

        # Дедупликация имён
        names = []
        seen = {}
        for p in proxies:
            name = p["name"]
            seen[name] = seen.get(name, 0) + 1
            if seen[name] > 1:
                p["name"] = f"{name} #{seen[name]}"
            names.append(p["name"])

        yaml_lines = [
            "# Auto-generated VPN subscription",
            f"# Updated: {datetime.now(timezone.utc).isoformat()}",
            f"# Total: {len(proxies)} proxies",
            "",
            "mixed-port: 7890",
            "allow-lan: false",
            "mode: rule",
            "log-level: warning",
            "",
            "proxies:",
        ]

        for p in proxies:
            yaml_lines.append(self._proxy_to_yaml_block(p))

        yaml_lines += [
            "",
            "proxy-groups:",
            "  - name: AUTO",
            "    type: url-test",
            "    url: http://cp.cloudflare.com/generate_204",
            "    interval: 300",
            "    proxies:",
        ]
        for name in names:
            yaml_lines.append(f"      - {name}")

        yaml_lines += [
            "",
            "  - name: MANUAL",
            "    type: select",
            "    proxies:",
            "      - AUTO",
        ]
        for name in names:
            yaml_lines.append(f"      - {name}")

        yaml_lines += [
            "",
            "rules:",
            "  - MATCH,AUTO",
        ]

        content = "\n".join(yaml_lines) + "\n"
        path = self.output_dir / "clash.yaml"
        path.write_text(content, encoding="utf-8")
        logger.info(f"Clash sub: {len(proxies)} proxies → {path}")
        return str(path)

    def _config_to_clash_proxy(self, c: dict) -> Optional[dict]:
        protocol = c["protocol"]
        raw = c["raw"]
        ping = c.get("ping_ms", 9999)

        try:
            if protocol == "ss":
                return self._parse_ss_for_clash(raw, ping)
            elif protocol == "vmess":
                return self._parse_vmess_for_clash(raw, ping)
            elif protocol == "vless":
                return self._parse_vless_for_clash(raw, ping)
            elif protocol == "trojan":
                return self._parse_trojan_for_clash(raw, ping)
            elif protocol == "hysteria2":
                return self._parse_hy2_for_clash(raw, ping)
        except Exception as e:
            logger.debug(f"Clash parse error ({protocol}): {e}")
        return None

    def _parse_ss_for_clash(self, raw: str, ping: int) -> dict:
        body = raw[5:].split("#")[0].split("?")[0]
        if "@" in body:
            userinfo, hostport = body.rsplit("@", 1)
            try:
                userinfo = base64.b64decode(userinfo + '==').decode()
            except Exception:
                pass
            method, password = userinfo.split(":", 1)
        else:
            decoded = base64.b64decode(body + '==').decode()
            method_pass, hostport = decoded.split("@")
            method, password = method_pass.split(":", 1)

        host, port = hostport.rsplit(":", 1)
        return {
            "name": f"SS {host}:{port} [{ping}ms]",
            "type": "ss",
            "server": host,
            "port": int(port),
            "cipher": method,
            "password": password,
        }

    def _parse_vmess_for_clash(self, raw: str, ping: int) -> dict:
        data = json.loads(base64.b64decode(raw[8:] + '==').decode())
        proxy = {
            "name": f"VMess {data.get('add')}:{data.get('port')} [{ping}ms]",
            "type": "vmess",
            "server": data.get("add"),
            "port": int(data.get("port", 443)),
            "uuid": data.get("id"),
            "alterId": int(data.get("aid", 0)),
            "cipher": data.get("scy", "auto"),
            "tls": data.get("tls") == "tls",
        }
        net = data.get("net", "tcp")
        if net == "ws":
            proxy["network"] = "ws"
            proxy["ws-opts"] = {"path": data.get("path", "/"), "headers": {"Host": data.get("host", "")}}
        return proxy

    def _parse_vless_for_clash(self, raw: str, ping: int) -> dict:
        parsed = urllib.parse.urlparse(raw)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        proxy = {
            "name": f"VLESS {parsed.hostname}:{parsed.port} [{ping}ms]",
            "type": "vless",
            "server": parsed.hostname,
            "port": parsed.port or 443,
            "uuid": parsed.username,
            "tls": params.get("security") in ("tls", "reality"),
            "udp": True,
        }
        if params.get("type") == "ws":
            proxy["network"] = "ws"
        return proxy

    def _parse_trojan_for_clash(self, raw: str, ping: int) -> dict:
        parsed = urllib.parse.urlparse(raw)
        return {
            "name": f"Trojan {parsed.hostname}:{parsed.port} [{ping}ms]",
            "type": "trojan",
            "server": parsed.hostname,
            "port": parsed.port or 443,
            "password": parsed.username,
            "udp": True,
        }

    def _parse_hy2_for_clash(self, raw: str, ping: int) -> dict:
        parsed = urllib.parse.urlparse(raw)
        return {
            "name": f"Hy2 {parsed.hostname}:{parsed.port} [{ping}ms]",
            "type": "hysteria2",
            "server": parsed.hostname,
            "port": parsed.port or 443,
            "password": parsed.username or parsed.password,
            "skip-cert-verify": True,
        }

    def _proxy_to_yaml_block(self, p: dict) -> str:
        lines = ["  - "]
        first = True
        for k, v in p.items():
            if isinstance(v, dict):
                if first:
                    lines[0] += f"{k}:"
                    first = False
                else:
                    lines.append(f"    {k}:")
                for sk, sv in v.items():
                    lines.append(f"      {sk}: {json.dumps(sv)}")
            else:
                val = json.dumps(v) if isinstance(v, (bool, int)) else f'"{v}"'
                if first:
                    lines[0] += f"{k}: {val}"
                    first = False
                else:
                    lines.append(f"    {k}: {val}")
        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────
    # sing-box JSON
    # ─────────────────────────────────────────────────────────

    def _write_singbox(self, configs: list[dict]) -> str:
        outbounds = []
        tags = []

        for c in configs:
            ob = self._config_to_singbox_outbound(c)
            if ob:
                # Уникальный тег
                base_tag = ob["tag"]
                existing_tags = [o["tag"] for o in outbounds]
                if base_tag in existing_tags:
                    ob["tag"] = f"{base_tag}-{len(outbounds)}"
                outbounds.append(ob)
                tags.append(ob["tag"])

        config = {
            "log": {"level": "warn"},
            "dns": {
                "servers": [
                    {"tag": "remote", "address": "tls://1.1.1.1"},
                    {"tag": "local", "address": "223.5.5.5", "detour": "direct"}
                ]
            },
            "inbounds": [
                {"type": "mixed", "listen": "127.0.0.1", "listen_port": 2080, "tag": "mixed-in"}
            ],
            "outbounds": [
                {
                    "type": "urltest",
                    "tag": "auto",
                    "outbounds": tags,
                    "url": "http://cp.cloudflare.com/generate_204",
                    "interval": "5m"
                },
                *outbounds,
                {"type": "direct", "tag": "direct"},
                {"type": "block", "tag": "block"}
            ],
            "route": {
                "auto_detect_interface": True,
                "rules": [{"outbound": "direct", "geoip": ["private"]}],
                "final": "auto"
            }
        }

        path = self.output_dir / "singbox.json"
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"sing-box config: {len(outbounds)} outbounds → {path}")
        return str(path)

    def _config_to_singbox_outbound(self, c: dict) -> Optional[dict]:
        protocol = c["protocol"]
        raw = c["raw"]
        ping = c.get("ping_ms", 9999)

        try:
            if protocol == "ss":
                body = raw[5:].split("#")[0].split("?")[0]
                if "@" in body:
                    userinfo, hostport = body.rsplit("@", 1)
                    try:
                        userinfo = base64.b64decode(userinfo + '==').decode()
                    except Exception:
                        pass
                    method, password = userinfo.split(":", 1)
                else:
                    decoded = base64.b64decode(body + '==').decode()
                    method_pass, hostport = decoded.split("@")
                    method, password = method_pass.split(":", 1)
                host, port = hostport.rsplit(":", 1)
                return {
                    "type": "shadowsocks", "tag": f"ss-{host}",
                    "server": host, "server_port": int(port),
                    "method": method, "password": password
                }

            elif protocol == "vmess":
                data = json.loads(base64.b64decode(raw[8:] + '==').decode())
                return {
                    "type": "vmess", "tag": f"vmess-{data.get('add')}",
                    "server": data.get("add"), "server_port": int(data.get("port", 443)),
                    "uuid": data.get("id"), "security": data.get("scy", "auto"),
                    "tls": {"enabled": data.get("tls") == "tls"}
                }

            elif protocol == "trojan":
                parsed = urllib.parse.urlparse(raw)
                return {
                    "type": "trojan", "tag": f"trojan-{parsed.hostname}",
                    "server": parsed.hostname, "server_port": parsed.port or 443,
                    "password": parsed.username,
                    "tls": {"enabled": True, "insecure": True}
                }

            elif protocol == "hysteria2":
                parsed = urllib.parse.urlparse(raw)
                return {
                    "type": "hysteria2", "tag": f"hy2-{parsed.hostname}",
                    "server": parsed.hostname, "server_port": parsed.port or 443,
                    "password": parsed.username or parsed.password,
                    "tls": {"enabled": True, "insecure": True}
                }

        except Exception as e:
            logger.debug(f"sing-box parse error ({protocol}): {e}")
        return None

    # ─────────────────────────────────────────────────────────
    # Метаданные / статистика
    # ─────────────────────────────────────────────────────────

    def _write_meta(self, configs: list[dict]) -> str:
        from collections import Counter

        proto_count = Counter(c["protocol"] for c in configs)
        pings = [c["ping_ms"] for c in configs if c.get("ping_ms")]

        meta = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "total": len(configs),
            "by_protocol": dict(proto_count),
            "ping": {
                "min": min(pings) if pings else None,
                "max": max(pings) if pings else None,
                "avg": int(sum(pings) / len(pings)) if pings else None,
            }
        }

        path = self.output_dir / "meta.json"
        path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return str(path)
