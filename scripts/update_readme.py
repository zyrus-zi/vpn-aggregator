"""
Обновляет README.md актуальной статистикой из meta.json
"""

import json
from pathlib import Path
from datetime import datetime

META_PATH = Path("output/meta.json")
README_PATH = Path("README.md")

STATS_START = "<!-- STATS_START -->"
STATS_END   = "<!-- STATS_END -->"


def build_stats_block(meta: dict) -> str:
    total   = meta.get("total", 0)
    updated = meta.get("updated_at", "")
    by_proto = meta.get("by_protocol", {})
    ping    = meta.get("ping", {})

    proto_rows = "\n".join(
        f"| `{p}` | {n} |"
        for p, n in sorted(by_proto.items(), key=lambda x: -x[1])
    )

    return f"""{STATS_START}
## 📊 Current Stats

> Last updated: **{updated}**

| Metric | Value |
|--------|-------|
| ✅ Alive configs | **{total}** |
| ⚡ Min ping | {ping.get('min', '—')} ms |
| 📈 Avg ping | {ping.get('avg', '—')} ms |
| 🐢 Max ping | {ping.get('max', '—')} ms |

### By Protocol

| Protocol | Count |
|----------|-------|
{proto_rows}
{STATS_END}"""


def update_readme():
    if not META_PATH.exists():
        print("meta.json not found, skipping README update")
        return

    with open(META_PATH) as f:
        meta = json.load(f)

    stats_block = build_stats_block(meta)

    if not README_PATH.exists():
        README_PATH.write_text(stats_block + "\n")
        return

    content = README_PATH.read_text()

    if STATS_START in content and STATS_END in content:
        before = content[:content.index(STATS_START)]
        after  = content[content.index(STATS_END) + len(STATS_END):]
        new_content = before + stats_block + after
    else:
        new_content = content + "\n\n" + stats_block + "\n"

    README_PATH.write_text(new_content)
    print(f"README updated: {meta.get('total')} configs")


if __name__ == "__main__":
    update_readme()
