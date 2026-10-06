# MoneyTracker

A Telegram bot for tracking trip expenses across several currencies. I built it for the guides of my tour company, who log spending in tenge, som, dollars and euros while on the road.

- Write an expense as plain text, or send a receipt photo with a caption. A local parser handles simple entries; Gemini is the fallback for messy ones.
- Six currencies with live conversion to USD, plus a wallet that tracks top-ups and real cash exchanges.
- One Excel file per tour, each with its own balance, exportable and importable from the chat.
- Several users with per-user routing, an owner role and an allow-list.
- Covered by a unit test suite.

Stack: Python, python-telegram-bot, Gemini API, openpyxl. No expense data is stored in this repository.

The full documentation below is in Russian.

---

## MoneyTracker — Telegram-бот учёта трат

Личный Telegram-бот для учёта расходов в поездках. Пользователь пишет трату текстом
или отправляет фото чека с подписью. Бот записывает расход в Excel, считает итоги по
валютам/категориям и ведёт кошелёк. Учёт — по турам: каждый тур (профиль) хранится в
отдельном Excel-файле со своим балансом.

Фото чеков **не распознаются**. Они сохраняются в папку чеков, а трата создаётся только
из текста сообщения или подписи к фото.

## Возможности
- ✍️ Текстовая запись: `Ресторан Plov 4500 тенге`, `Такси 1200`, `Заправка 30 долларов`.
- 📎 Фото чека с подписью: фото сохраняется, расход берётся из подписи.
- 🧠 Локальный парсер быстро разбирает простые траты; Gemini используется как fallback для сложного текста.
- 🍽 Рестораны записываются как `Ресторан «Название»` без перечисления блюд.
- ✏️ Под каждой тратой — кнопки категории, правки суммы/описания и удаления.
- 💱 Валюты: KZT, KGS, USD, EUR, UZS, TJS. Конвертация в USD по живому курсу.
- 💼 Кошелёк: пополнения (`/add`) и реальные обмены (`поменял 67 долларов 5360 сом`).
- ✏️ Правка баланса — только владельцу (кнопки под `/wallet`):
  - «⚖️ Задать остаток» (`/setbalance`) — выставить текущий остаток валюты ровно («164000 тенге»); пишет прозрачную «Коррекцию».
  - «✏️ Исправить начальный баланс» (`/startbalance`) — переписать стартовый баланс тура.
- 🗑 Владелец может удалить ошибочное движение кошелька (обмен/возврат/пополнение) кнопкой под `/wallet` — обмен снимается обеими ногами, без выгрузки/импорта.
- 📂 Туры — каждый в своём Excel-файле (имя файла = имя тура), со своими тратами и кошельком.
- 📊 `/total` — сводка по туру; период: `/total месяц`, `/total неделя`, `/total 7`.
- 📤 `/excel` — выгрузка файла активного тура. Прислать `.xlsx` боту — импорт тура по имени файла.

## Команды
- `/start`, `/help` — инструкция.
- `/total [период]` — сводка; периоды: `сегодня`, `неделя`, `месяц`, `год`, `N` дней.
- `/wallet` — остаток по валютам.
- `/add 5000 USD` — пополнить кошелёк.
- `/startbalance` — исправить начальный баланс тура (или кнопка под `/wallet`).
- `/profiles` — выбрать или создать тур.
- `/currency` — выбрать валюту по умолчанию.
- `/excel` — получить таблицу.
- `/undo` или `/delete` — удалить последнюю трату.
- `/clear` — очистить чат с ботом.

## Доступ
Бот отвечает только владельцу. Если `ALLOWED_USER_IDS` задан в `.env`, доступ идёт по
этому списку. Если список пустой, первый написавший после запуска становится владельцем
(auto-claim, id хранится в `settings.json`).

Владелец может выдавать доступ другим прямо в чате, без передеплоя:

- `/allow <id>` — открыть доступ (можно несколько id через пробел).
- `/disallow <id>` — закрыть доступ.
- `/allowed` — кто сейчас имеет доступ.

Свой Telegram-id новый пользователь увидит, просто написав боту — бот подскажет его в
ответе «это личный бот». Выданные id хранятся в `settings.json` (ключ `allowed_user_ids`).
Id из `.env` и самого владельца через `/disallow` снять нельзя.

Пример:

```env
ALLOWED_USER_IDS=123456789
```

## Установка

```bash
cd MoneyTracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Настройка
Открой `.env` (пример — `.env.example`) и впиши ключи:

```env
TELEGRAM_BOT_TOKEN=...
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
EXCEL_PATH=expenses.xlsx
# ALLOWED_USER_IDS=123456789
```

## Запуск (локально, для разработки)

```bash
source .venv/bin/activate
python bot.py
```

После запуска напиши боту `/start`.

> Один Telegram-токен обслуживает только один процесс. Не запускай локально с **боевым**
> токеном, пока бот работает на Railway, — Telegram вернёт ошибку 409. Для локальной
> разработки используй отдельный тестовый токен.

## Хостинг и деплой (Railway)

Боевой бот работает на **Railway** (always-on, 24/7), не на локальном Маке.

- **Деплой = `git push` в `main`.** Railway подключён к GitHub-репозиторию и авто-деплоит
  каждый push, пересобирая образ через Nixpacks по `Procfile` (`worker: python bot.py`).
- **Данные — на Railway Volume в `/data`** (env: `EXCEL_PATH=/data/expenses.xlsx`,
  `SETTINGS_PATH=/data/settings.json`). Volume обязателен, иначе таблица и настройки
  стираются при редеплое. Локальные `expenses.xlsx`/`settings.json` в репозитории —
  устаревший снимок, на боевого бота не влияют.
- Логи — в дашборде Railway (Deploy logs).

Старый локальный автозапуск через macOS LaunchAgent (`./install_autostart.sh`,
`com.moneytracker.bot.plist`) больше не используется — оставлен как легаси.

## Структура
- `bot.py` — Telegram-хендлеры, команды, кнопки.
- `expense_parser.py` — быстрый локальный парсер простых трат.
- `receipt_analyzer.py` — Gemini fallback для сложного текста.
- `excel_store.py` — файл-на-тур: запись в Excel, итоги, кошелёк, стабильные ID трат.
- `currency.py` — курсы валют, конвертация, парсинг сумм.
- `settings.py` — валюта по умолчанию, туры (файл на тур), владелец, доступ.
- `bills.py` — сохранение и выдача фото чеков.
- `config.py` — конфигурация из `.env`.

Файл тура создаётся автоматически при первой трате (или при создании тура). Файлы лежат
в `DATA_DIR` (рядом с `EXCEL_PATH`). Подробный контекст для разработки — в `AGENT.md`.
