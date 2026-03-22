# 🛡️ VPN Config Aggregator

Автоматически собирает VPN-конфиги из публичных Telegram-каналов,
проверяет их работоспособность и публикует готовые подписки.

Обновляется каждые **6 часов** через GitHub Actions.

---

## 📡 Подписки

| Формат | Ссылка | Клиенты |
|--------|--------|---------|
| **Universal (base64)** | `https://<your-user>.github.io/<repo>/sub.txt` | V2RayNG, Hiddify, Streisand, Shadowrocket |
| **Clash / Mihomo** | `https://<your-user>.github.io/<repo>/clash.yaml` | Clash, Mihomo, ClashX, Clash Verge |
| **sing-box** | `https://<your-user>.github.io/<repo>/singbox.json` | sing-box, Hiddify (new), NekoBox |

> Замени `<your-user>` и `<repo>` на своё имя пользователя и название репозитория.

---

## ⚙️ Настройка (Fork & Run)

### 1. Получи Telegram API credentials

Зайди на [my.telegram.org](https://my.telegram.org) → API development tools.  
Создай приложение — получи `api_id` и `api_hash`.

### 2. Сгенерируй сессию

```bash
git clone https://github.com/<your-user>/<repo>
cd <repo>
pip install telethon
python scripts/gen_session.py
```

Скопируй выведенную строку сессии.

### 3. Добавь GitHub Secrets

`Settings → Secrets and variables → Actions → New repository secret`

| Secret | Значение |
|--------|---------|
| `TG_API_ID` | Числовой ID из my.telegram.org |
| `TG_API_HASH` | Hash из my.telegram.org |
| `TG_SESSION` | Строка из шага 2 |

### 4. Включи GitHub Pages

`Settings → Pages → Source: GitHub Actions`

### 5. Запусти вручную

`Actions → VPN Config Aggregator → Run workflow`

---

## 🔧 Параметры

Переменные окружения (или workflow inputs при ручном запуске):

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `MAX_PING_MS` | `3000` | Максимальный пинг (мс). Конфиги выше — отбрасываются |
| `MESSAGES_PER_CHANNEL` | `200` | Сколько последних сообщений парсить в каждом канале |
| `CHECKER_CONCURRENCY` | `40` | Параллельность при проверке |

Список каналов — в файле `channels.json`.

---

## 📁 Структура проекта

```
.
├── main.py                        # Основной пайплайн
├── channels.json                  # Список TG-каналов
├── requirements.txt
├── collector/
│   └── telegram_parser.py         # Парсинг Telegram
├── checker/
│   └── config_checker.py          # Проверка через xray-core
├── generator/
│   └── subscription.py            # Генерация подписок
├── scripts/
│   ├── gen_session.py             # Генератор сессии TG
│   └── update_readme.py           # Обновление README со статистикой
└── .github/
    └── workflows/
        └── aggregate.yml          # GitHub Actions
```

---

<!-- STATS_START -->
## 📊 Current Stats

> Last updated: —

Статистика появится после первого запуска.
<!-- STATS_END -->

---

## ⚠️ Дисклеймер

Проект предназначен для личного использования.
Конфиги берутся из публично доступных источников.
Автор не несёт ответственности за содержимое конфигов.
Используйте на свой страх и риск.
