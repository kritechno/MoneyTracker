import asyncio
import logging
import os
import re
from datetime import date, timedelta

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.constants import ChatAction
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

import bills
import currency
import expense_parser
import excel_store
import receipt_analyzer
import settings
from config import (
    ALLOWED_USER_IDS,
    CATEGORIES,
    CURRENCIES,
    CURRENCY_LABELS,
    TELEGRAM_BOT_TOKEN,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# httpx логирует полный URL getUpdates с токеном бота и спамит лог — глушим до WARNING.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("moneytracker")

_excel_lock = asyncio.Lock()


class _NoAmount(Exception):
    """Не удалось определить сумму траты — не записываем мусор."""

# Фразы-действия для реального обмена (двигают деньги в кошельке).
# Применяется, если в тексте есть две суммы: первая — что отдаю, вторая — что получаю.
_ACTION_RE = re.compile(r"(?i)(помен[яе]|обмен[яе]|размен[яе])")

# Подсказка, когда сообщение похоже на обмен, но распознана только одна сторона.
# Раньше такое уходило в обычную запись и Gemini писал «трату-призрак», из-за чего
# съезжал остаток. Теперь просим явный формат и ничего не записываем.
_EXCHANGE_HELP = (
    "💱 Похоже на обмен валюты, но я не разобрал обе суммы.\n"
    "Напиши обе стороны с валютами, например:\n"
    "• поменял 100 долларов на 48000 тенге\n"
    "• обменял 30 долларов 15000 тенге\n\n"
    "Если это обычная трата — напиши без слова «поменял/обменял», "
    "например «Шиномонтаж 5000»."
)


def _action_kind(pairs: list) -> str:
    """Решает, что делать с сообщением, в котором есть глагол обмена.
    pairs — распознанные (сумма, валюта) из currency.parse_amounts.
    ≥2 пары → настоящий обмен; ровно 1 → недописанный обмен (просим формат);
    0 → валюты нет вовсе, это обычная трата («поменял колесо 5000»)."""
    if len(pairs) >= 2:
        return "exchange"
    if len(pairs) == 1:
        return "ask"
    return "expense"


def _is_allowed(user_id: int) -> bool:
    """Пускаем владельца и список из .env, плюс тех, кому владелец выдал доступ
    командой /allow. Если ALLOWED_USER_IDS пуст и владельца ещё нет — первый
    написавший «занимает» бота (auto-claim) и становится владельцем."""
    if ALLOWED_USER_IDS:
        if user_id in ALLOWED_USER_IDS:
            return True
    else:
        owner = settings.get_owner_id()
        if owner is None:
            settings.set_owner_id(user_id)
            logger.info("Владелец бота назначен: %s", user_id)
            return True
        if user_id == owner:
            return True
    return user_id in settings.get_allowed_user_ids()


def _is_admin(user_id: int) -> bool:
    """Управлять доступом (/allow, /disallow, /allowed) может владелец, а в
    режиме ALLOWED_USER_IDS — любой из этого списка. Гостям, добавленным через
    /allow, управление недоступно."""
    if ALLOWED_USER_IDS:
        return user_id in ALLOWED_USER_IDS
    return user_id == settings.get_owner_id()


async def _auth_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запускается раньше всех (group=-1): чужих не пускает, обработку прерывает."""
    user = update.effective_user
    if user is None:
        return
    if await asyncio.to_thread(_is_allowed, user.id):
        return
    if update.callback_query:
        await update.callback_query.answer("⛔️ Это личный бот.", show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text(
            "⛔️ Это личный бот учёта трат.\n"
            f"Твой ID: {user.id} — покажи его владельцу, чтобы он открыл доступ."
        )
    raise ApplicationHandlerStop


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный обработчик ошибок: сетевые глушим, остальные логируем и, по
    возможности, сообщаем пользователю — чтобы действия не падали молча."""
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        logger.warning("Сетевая ошибка: %s", err)
        return
    logger.exception("Необработанная ошибка", exc_info=err)
    msg = getattr(update, "effective_message", None)
    if msg is not None:
        try:
            await msg.reply_text("⚠️ Что-то пошло не так. Попробуй ещё раз.")
        except Exception:
            pass

START_TEXT = (
    "👋 Я веду учёт трат по турам.\n\n"
    "Записать трату — просто напиши строкой:\n"
    "• Ресторан Нават 50000 тенге\n"
    "• Такси 1200\n"
    "• Заправка 30 долларов\n\n"
    "Фото чека приложи с подписью «Ресторан Нават 50000 тенге» — фото сохраню, "
    "трату запишу по подписи.\n\n"
    "После записи кнопками меняешь категорию, сумму, описание или удаляешь трату.\n\n"
    "Главное:\n"
    "/total — сводка по туру\n"
    "/wallet — остаток кошелька\n"
    "/add 5000 USD — пополнить кошелёк\n"
    "/profiles — выбрать или создать тур\n"
    "/excel — выгрузить файл тура\n"
    "/help — все команды"
)

HELP_TEXT = (
    "ℹ️ Как пользоваться\n\n"
    "Записать трату:\n"
    "• Ресторан Plov 4500 тенге\n"
    "• Такси 1200\n"
    "• Заправка 30 долларов\n\n"
    "Фото чека: добавь подпись с суммой. Без подписи фото только сохранится в чеки.\n"
    "Обмен валюты: «поменял 67 долларов 5360 сом».\n\n"
    "Команды:\n"
    "/total — сводка по туру за всё время\n"
    "/total месяц — период: сегодня, неделя, месяц, год или число дней\n"
    "/wallet — остаток кошелька\n"
    "/add 5000 USD — пополнить кошелёк\n"
    "/profiles — выбрать или создать тур\n"
    "/currency — валюта по умолчанию\n"
    "/excel — выгрузить файл тура\n"
    "/undo — удалить последнюю трату\n"
    "/clear — очистить чат\n\n"
    "Категории: Отель, Питание, Бензин, Прочее."
)


def _format_reply(entry: dict) -> str:
    cur = entry["currency"]
    cur_label = CURRENCY_LABELS.get(cur, cur)
    lines = [
        "✅ Записал трату:",
        f"📅 Дата: {entry['date']}",
        f"📝 Описание: {entry['description']}",
        f"🏷 Категория: {entry['category']}",
        f"💵 Сумма: {entry['amount']:,.2f} {cur} ({cur_label})",
    ]
    if cur != "USD":
        lines.append(f"💲 В долларах: ≈ {entry['amount_usd']:,.2f} USD")
    if entry.get("has_wallet"):
        lines.append(f"💼 Осталось: {entry['remaining']:,.2f} {cur} ({cur_label})")
        if entry["remaining"] < 0:
            lines.append(f"⚠️ Кошелёк в минусе по {cur} — пополни через /add.")
    if entry.get("profile"):
        lines.append(f"📂 Тур: {entry['profile']}")
    return "\n".join(lines)


def _expense_keyboard(expense_id: int, current_category: str) -> InlineKeyboardMarkup:
    cat_buttons = []
    for cat in CATEGORIES:
        label = f"✅ {cat}" if cat == current_category else cat
        cat_buttons.append(
            InlineKeyboardButton(label, callback_data=f"cat|{expense_id}|{cat}")
        )
    rows = [cat_buttons[i : i + 2] for i in range(0, len(cat_buttons), 2)]
    rows.append([
        InlineKeyboardButton("✏️ Сумма", callback_data=f"edit|{expense_id}|amt"),
        InlineKeyboardButton("✏️ Описание", callback_data=f"edit|{expense_id}|desc"),
    ])
    rows.append([InlineKeyboardButton("🗑 Удалить", callback_data=f"del|{expense_id}")])
    return InlineKeyboardMarkup(rows)


async def _enrich(entry: dict) -> dict:
    """Дополняет трату полями кошелька/профиля для _format_reply."""
    net = await asyncio.to_thread(excel_store.wallet_net)
    totals = await asyncio.to_thread(excel_store.compute_totals)
    cur = entry["currency"]
    entry["profile"] = await asyncio.to_thread(settings.get_active_profile)
    entry["has_wallet"] = any(net.values())
    entry["remaining"] = round(net.get(cur, 0.0) - totals["per_currency"].get(cur, 0.0), 2)
    return entry


async def _process(text):
    default_cur = await asyncio.to_thread(settings.get_default_currency)
    parsed = await asyncio.to_thread(expense_parser.parse_expense, text, default_cur)
    if parsed.parsed:
        data = parsed.entry
    elif parsed.no_amount:
        raise _NoAmount
    else:
        data = await asyncio.to_thread(receipt_analyzer.analyze, text, default_cur)
    if not data.get("amount") or data["amount"] <= 0:
        raise _NoAmount
    data["amount_usd"] = await asyncio.to_thread(
        currency.to_usd, data["amount"], data["currency"]
    )
    async with _excel_lock:
        expense_id = await asyncio.to_thread(excel_store.add_expense, data)
    data["id"] = expense_id
    await _enrich(data)
    return data, expense_id


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(START_TEXT)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT)


async def excel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    path = await asyncio.to_thread(excel_store.active_path)
    profile = await asyncio.to_thread(settings.get_active_profile)
    if not os.path.exists(path):
        await update.message.reply_text("Тур пока пустой — нет ни одной траты.")
        return
    with open(path, "rb") as f:
        await update.message.reply_document(document=f, filename=f"{profile}.xlsx")


async def bills_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Только владельцу: присылает сохранённые фото-чеки (нет в /help и меню)."""
    user = update.effective_user
    if user is None or not await asyncio.to_thread(_is_admin, user.id):
        await update.message.reply_text("⛔️ Чеки доступны только владельцу бота.")
        return
    files = await asyncio.to_thread(bills.list_bills)
    if not files:
        await update.message.reply_text("📂 Чеков пока нет.")
        return
    recent = files[-50:]
    chat_id = update.message.chat_id
    await update.message.reply_text(
        f"📂 Сохранённых чеков: {len(files)}"
        + ("" if len(recent) == len(files) else f" (показываю последние {len(recent)})")
    )
    for i in range(0, len(recent), 10):
        chunk = recent[i : i + 10]
        media = []
        for path in chunk:
            with open(path, "rb") as f:
                media.append(InputMediaPhoto(f.read(), caption=os.path.basename(path)))
        try:
            await context.bot.send_media_group(chat_id, media)
        except Exception:
            logger.exception("Не смог отправить чеки")


def _currency_keyboard(current: str) -> InlineKeyboardMarkup:
    buttons = []
    current = (current or "").upper()
    for cur in CURRENCIES:
        label = CURRENCY_LABELS.get(cur, cur)
        mark = "✅ " if cur == current else ""
        buttons.append(
            InlineKeyboardButton(f"{mark}{label} ({cur})", callback_data=f"setcur|{cur}")
        )
    return InlineKeyboardMarkup([buttons[i : i + 3] for i in range(0, len(buttons), 3)])


def _currency_text(current: str, changed: bool = False) -> str:
    label = CURRENCY_LABELS.get(current, current)
    title = "✅ Валюта по умолчанию" if changed else "Валюта по умолчанию"
    return (
        f"{title}: {label} ({current}).\n"
        f"Если в трате нет валюты, запишу сумму как {current}.\n"
        "Например: «Такси 1200»."
    )


async def currency_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    current = await asyncio.to_thread(settings.get_default_currency)
    await update.message.reply_text(
        _currency_text(current) + "\n\nВыбери новую:",
        reply_markup=_currency_keyboard(current),
    )


def _profiles_keyboard(
    active: str, names: list[str], can_delete: bool = False
) -> InlineKeyboardMarkup:
    rows = []
    for i, name in enumerate(names):
        mark = "✅ " if name == active else ""
        rows.append([InlineKeyboardButton(f"{mark}{name}", callback_data=f"prof|{i}")])
    rows.append([InlineKeyboardButton("➕ Новый тур", callback_data="prof_new")])
    # Удаление — только владельцу и только если есть что оставить (нельзя удалить
    # последний тур).
    if can_delete and len(names) > 1:
        rows.append([InlineKeyboardButton("🗑 Удалить тур", callback_data="prof_delmenu")])
    return InlineKeyboardMarkup(rows)


def _profiles_delete_keyboard(active: str, names: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for i, name in enumerate(names):
        mark = "✅ " if name == active else ""
        rows.append([InlineKeyboardButton(f"🗑 {mark}{name}", callback_data=f"profdel|{i}")])
    rows.append([InlineKeyboardButton("← Назад", callback_data="prof_menu")])
    return InlineKeyboardMarkup(rows)


def _profile_confirm_delete_keyboard(index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Да, удалить", callback_data=f"profdelok|{index}")],
        [InlineKeyboardButton("← Отмена", callback_data="prof_delmenu")],
    ])


_PROFILES_INTRO = (
    "📂 Активный тур: {active}\n"
    "Новые траты попадут в этот тур. Выбери другой или создай новый:"
)


async def profiles_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    active = await asyncio.to_thread(settings.get_active_profile)
    names = await asyncio.to_thread(settings.list_profiles)
    user = update.effective_user
    can_delete = bool(user) and await asyncio.to_thread(_is_admin, user.id)
    await update.message.reply_text(
        _PROFILES_INTRO.format(active=active),
        reply_markup=_profiles_keyboard(active, names, can_delete),
    )


async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🧹 Очистить чат? Удалю до 200 последних сообщений (только в этом чате, "
        "данные в Excel не трогаю).",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Да, очистить", callback_data="clr|yes"),
            InlineKeyboardButton("Отмена", callback_data="clr|no"),
        ]]),
    )


async def _do_clear(context: ContextTypes.DEFAULT_TYPE, chat_id: int, last_id: int) -> None:
    for mid in range(last_id, max(0, last_id - 200), -1):
        try:
            await context.bot.delete_message(chat_id, mid)
        except Exception:
            pass


def _wallet_balances(net: dict, per_currency: dict) -> dict:
    """Остаток по валютам = кошелёк (поступления/обмены) − траты в этой валюте."""
    return {
        cur: round(net.get(cur, 0.0) - per_currency.get(cur, 0.0), 2)
        for cur in CURRENCIES
    }


def _format_wallet(net: dict, per_currency: dict) -> str:
    bal = _wallet_balances(net, per_currency)
    lines = ["💼 Кошелёк (остаток):"]
    total_usd = 0.0
    shown = False
    negative = False
    for cur in CURRENCIES:
        total_usd += currency.to_usd(bal[cur], cur)
        if bal[cur] or net.get(cur):
            label = CURRENCY_LABELS.get(cur, cur)
            warn = "  ⚠️" if bal[cur] < 0 else ""
            lines.append(f"  • {label} ({cur}): {bal[cur]:,.2f}{warn}")
            shown = True
            negative = negative or bal[cur] < 0
    if not shown:
        lines.append("  • кошелёк пуст")
    lines.append(f"  💲 Итого ≈ {total_usd:,.2f} USD")
    if negative:
        lines.append("⚠️ Есть минус по валюте. Пополнить: /add 5000 USD")
    return "\n".join(lines)


def _format_start_balance(start: dict) -> str:
    parts = []
    for cur in CURRENCIES:
        amt = start.get(cur, 0.0)
        if amt:
            label = CURRENCY_LABELS.get(cur, cur)
            parts.append(f"{amt:,.2f} {cur} ({label})")
    return ", ".join(parts) if parts else "не задан (0)"


def _wallet_keyboard(can_manage: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("✏️ Исправить начальный баланс", callback_data="setstart")]]
    if can_manage:
        rows.append([InlineKeyboardButton("🗑 Удалить движение", callback_data="wdlist")])
    return InlineKeyboardMarkup(rows)


def _group_movements(rows: list[dict]) -> list[dict]:
    """Группирует две ноги обмена/возврата (→ списание, ← зачисление) в одну запись.
    Ноги пишутся соседними строками, поэтому склеиваем по зеркальным стрелкам."""
    groups: list[dict] = []
    i = 0
    while i < len(rows):
        m = rows[i]
        note = m.get("note") or ""
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        paired = (
            m["kind"] in ("Обмен", "Возврат")
            and note.startswith("→")
            and nxt is not None
            and nxt["kind"] == m["kind"]
            and (nxt.get("note") or "").startswith("←")
        )
        if paired:
            groups.append({"id": m["id"], "kind": m["kind"], "legs": [m, nxt]})
            i += 2
        else:
            groups.append({"id": m["id"], "kind": m["kind"], "legs": [m]})
            i += 1
    return groups


def _movement_label(g: dict) -> str:
    """Короткая подпись операции кошелька для кнопки/списка."""
    if len(g["legs"]) == 2:
        out_leg, in_leg = g["legs"]
        return (
            f"{g['kind']}: {out_leg['amount']:,.0f} {out_leg['currency']} "
            f"→ +{in_leg['amount']:,.0f} {in_leg['currency']}"
        )
    m = g["legs"][0]
    amt = m["amount"] or 0
    sign = "+" if amt >= 0 else ""
    label = f"{g['kind']}: {sign}{amt:,.0f} {m['currency']}"
    if m["kind"] == "Старт" or not (m.get("note") or "").strip():
        return label
    return f"{label} · {m['note']}"


async def _show_movements(query) -> None:
    """Список последних движений кошелька с кнопками удаления (только владельцу)."""
    rows = await asyncio.to_thread(excel_store.list_movements)
    groups = _group_movements(rows)
    profile = await asyncio.to_thread(settings.get_active_profile)
    if not groups:
        await query.edit_message_text(f"📂 Тур: {profile}\n\n💼 В кошельке нет движений.")
        return
    recent = groups[-12:]
    lines = [
        f"🗑 Удаление движения кошелька · {profile}",
        "Выбери, что удалить. Обмен/возврат удалится обеими ногами.",
        "",
    ]
    buttons = []
    for g in recent:
        lines.append(f"• {_movement_label(g)}")
        if isinstance(g["id"], int):
            buttons.append(
                [InlineKeyboardButton(f"🗑 {_movement_label(g)}", callback_data=f"wdel|{g['id']}")]
            )
    buttons.append([InlineKeyboardButton("← Закрыть", callback_data="wdclose")])
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


def _fmt_amt(a: float) -> str:
    a = round(float(a), 2)
    return str(int(a)) if a == int(a) else f"{a:.2f}"


def _parse_period(args: list[str]) -> tuple[date | None, str]:
    """Разбирает аргумент /total в (нижняя граница даты, подпись периода)."""
    arg = (args[0].lower().strip() if args else "")
    today = date.today()
    if arg in ("", "все", "всё", "all"):
        return None, "за всё время"
    if arg in ("сегодня", "today", "день"):
        return today, "сегодня"
    if arg in ("неделя", "неделю", "week", "7"):
        return today - timedelta(days=6), "за неделю"
    if arg in ("месяц", "month", "30"):
        return today - timedelta(days=29), "за месяц"
    if arg in ("год", "year", "365"):
        return today - timedelta(days=364), "за год"
    if arg.isdigit():
        n = max(1, int(arg))
        return today - timedelta(days=n - 1), f"за {n} дн."
    return None, "за всё время"


def _format_total(t: dict, profile: str, period: str = "за всё время") -> str:
    if t["count"] == 0:
        return f"📊 Тур «{profile}» · {period}: трат нет."
    lines = [f"📊 Сводка · {profile} · {period} ({t['count']} трат):", "", "💱 По валютам:"]
    for cur in CURRENCIES:
        amount = t["per_currency"].get(cur, 0.0)
        if amount:
            label = CURRENCY_LABELS.get(cur, cur)
            lines.append(f"  • {label} ({cur}): {amount:,.2f}")
    lines += ["", f"💲 Всего в USD: ≈ {t['total_usd']:,.2f}", "", "🏷 По категориям (USD):"]
    for cat, amount in t["per_category"].items():
        if amount:
            lines.append(f"  • {cat}: ≈ {amount:,.2f}")
    return "\n".join(lines)


async def total(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    since, label = _parse_period(context.args)
    t = await asyncio.to_thread(excel_store.compute_totals, since)
    profile = await asyncio.to_thread(settings.get_active_profile)
    net = await asyncio.to_thread(excel_store.wallet_net)
    # Кошелёк — текущий остаток, всегда по всем тратам, не по периоду.
    t_all = t if since is None else await asyncio.to_thread(excel_store.compute_totals)
    text = _format_total(t, profile, label)
    if any(net.values()) or t_all["count"]:
        text += "\n\n" + _format_wallet(net, t_all["per_currency"])
    await update.message.reply_text(text)


async def wallet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await asyncio.to_thread(settings.get_active_profile)
    net = await asyncio.to_thread(excel_store.wallet_net)
    t = await asyncio.to_thread(excel_store.compute_totals)
    user = update.effective_user
    can_manage = bool(user) and await asyncio.to_thread(_is_admin, user.id)
    await update.message.reply_text(
        f"📂 Тур: {profile}\n\n"
        + _format_wallet(net, t["per_currency"])
        + "\n\n➕ Пополнить: /add 5000 USD\n"
        "💱 Обмен: «поменял 30 долларов на 15000 тенге»",
        reply_markup=_wallet_keyboard(can_manage),
    )


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    arg = " ".join(context.args) if context.args else ""
    pairs = await asyncio.to_thread(currency.parse_amounts, arg)
    if not pairs:
        await msg.reply_text(
            "Укажи сумму и валюту. Примеры:\n"
            "• /add 5000 USD\n"
            "• /add 100000 тенге\n"
            "• /add 1000 долларов 200000 тенге"
        )
        return
    async with _excel_lock:
        for amount, cur in pairs:
            net = await asyncio.to_thread(
                excel_store.add_movement, "Пополнение", cur, amount
            )
        t = await asyncio.to_thread(excel_store.compute_totals)
    added = ", ".join(f"{amt:,.2f} {cur}" for amt, cur in pairs)
    await msg.reply_text(
        f"✅ Пополнил кошелёк: {added}\n\n" + _format_wallet(net, t["per_currency"])
    )


def _rate_line(gave_amt, gave_cur, recv_amt, recv_cur) -> str:
    """Показывает курс в читаемую сторону (так, чтобы число было >= 1)."""
    if recv_amt >= gave_amt:
        rate = recv_amt / gave_amt if gave_amt else 0
        return f"1 {gave_cur} = {rate:,.2f} {recv_cur}"
    rate = gave_amt / recv_amt if recv_amt else 0
    return f"1 {recv_cur} = {rate:,.2f} {gave_cur}"


def _format_exchange(gave, received) -> str:
    gave_amt, gave_cur = gave
    recv_amt, recv_cur = received
    gave_label = CURRENCY_LABELS.get(gave_cur, gave_cur)
    recv_label = CURRENCY_LABELS.get(recv_cur, recv_cur)

    market_recv = currency.convert(gave_amt, gave_cur, recv_cur)
    diff = round(recv_amt - market_recv, 2)
    pct = (diff / market_recv * 100) if market_recv else 0.0

    lines = [
        "💱 Обмен:",
        f"Отдал:    {gave_amt:,.2f} {gave_cur} ({gave_label})",
        f"Получил:  {recv_amt:,.2f} {recv_cur} ({recv_label})",
        "",
        f"📈 Твой курс:     {_rate_line(gave_amt, gave_cur, recv_amt, recv_cur)}",
        f"📊 Рыночный курс: {_rate_line(gave_amt, gave_cur, market_recv, recv_cur)}",
        "",
    ]
    if diff > 0:
        lines.append(
            f"✅ Выгодно: получил на {diff:,.2f} {recv_cur} больше рынка (+{pct:.2f}%)"
        )
    elif diff < 0:
        lines.append(
            f"❌ Невыгодно: получил на {abs(diff):,.2f} {recv_cur} меньше рынка ({pct:.2f}%)"
        )
    else:
        lines.append("➖ Ровно по рыночному курсу.")
    return "\n".join(lines)


def _ex_cb(tag: str, gave, received) -> str:
    g_amt, g_cur = gave
    r_amt, r_cur = received
    return f"{tag}|{g_cur}|{_fmt_amt(g_amt)}|{r_cur}|{_fmt_amt(r_amt)}"


def _exchange_primary_kb(state: str, gave, received) -> InlineKeyboardMarkup:
    """Клавиатура под записанным обменом. state='applied' → кнопка отмены (через
    подтверждение); state='reversed' → кнопка вернуть обмен (через подтверждение)."""
    if state == "applied":
        btn = InlineKeyboardButton(
            "↩️ Отменить обмен", callback_data=_ex_cb("exunq", gave, received)
        )
    else:
        btn = InlineKeyboardButton(
            "💼 Вернуть обмен", callback_data=_ex_cb("exapq", gave, received)
        )
    return InlineKeyboardMarkup([[btn]])


def _exchange_confirm_kb(action: str, gave, received) -> InlineKeyboardMarkup:
    """Подтверждение движения денег: реальный обмен/возврат двигает кошелёк, поэтому
    не делаем его одним кликом (раньше случайный тап по старому сообщению откатывал
    реальный обмен)."""
    if action == "undo":
        yes = InlineKeyboardButton("✅ Да, отменить", callback_data=_ex_cb("exun", gave, received))
        no = InlineKeyboardButton("← Нет", callback_data=_ex_cb("exsa", gave, received))
    else:
        yes = InlineKeyboardButton("✅ Да, вернуть", callback_data=_ex_cb("exap", gave, received))
        no = InlineKeyboardButton("← Нет", callback_data=_ex_cb("exsr", gave, received))
    return InlineKeyboardMarkup([[yes, no]])


async def _exchange_reply(msg, pairs) -> None:
    """Реальный обмен валюты: первая сумма — что отдаю, вторая — что получаю.
    Списывает отданную, зачисляет полученную, показывает выгоду и кнопку отмены."""
    gave, received = pairs[0], pairs[1]
    g_amt, g_cur = gave
    r_amt, r_cur = received
    text = _format_exchange(gave, received)
    async with _excel_lock:
        net = await asyncio.to_thread(
            excel_store.add_exchange, g_cur, g_amt, r_cur, r_amt
        )
        t = await asyncio.to_thread(excel_store.compute_totals)
    text += "\n\n✅ Записал в кошелёк:\n" + _format_wallet(net, t["per_currency"])
    await msg.reply_text(text, reply_markup=_exchange_primary_kb("applied", gave, received))


async def _record_expense(msg, text, success_note="", no_amount_msg=None) -> None:
    """Записывает трату из обычного текста или подписи к фото."""
    await msg.chat.send_action(ChatAction.TYPING)
    try:
        entry, expense_id = await _process(text)
    except _NoAmount:
        await msg.reply_text(
            no_amount_msg
            or "Не вижу сумму. Напиши так: «Ресторан Plov 4500 тенге»."
        )
        return
    except receipt_analyzer.ModelOverloaded:
        await msg.reply_text(
            "⏳ Gemini сейчас перегружен. Повтори через минуту — трата не записана."
        )
        return
    except Exception:
        logger.exception("Ошибка обработки текста")
        await msg.reply_text(
            "⚠️ Не смог разобрать трату. Пример: «Ресторан Plov 4500 тенге»."
        )
        return
    reply = _format_reply(entry)
    if success_note:
        reply += "\n\n" + success_note
    await msg.reply_text(
        reply, reply_markup=_expense_keyboard(expense_id, entry["category"])
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сохраняет фото-чек; трату записывает только из подписи."""
    msg = update.message
    caption = (msg.caption or "").strip()
    try:
        photo = msg.photo[-1]
        tg_file = await photo.get_file()
        image_bytes = bytes(await tg_file.download_as_bytearray())
        await asyncio.to_thread(bills.save_bill, image_bytes, caption)
    except Exception:
        logger.exception("Ошибка сохранения чека")
        await msg.reply_text("⚠️ Не смог сохранить фото. Пришли его ещё раз.")
        return
    if caption:
        await _record_expense(
            msg,
            caption,
            success_note="📎 Фото сохранено в чеки.",
            no_amount_msg=(
                "📎 Фото сохранено. Чтобы записать трату, добавь сумму в подпись: "
                "«Ресторан Нават 50000 тенге»."
            ),
        )
    else:
        await msg.reply_text(
            "📎 Фото сохранено в чеки. Трата не записана: нет подписи с суммой.\n"
            "В следующий раз подпиши фото так: «Ресторан Нават 50000 тенге»."
        )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Импорт тура: пользователь шлёт .xlsx → имя файла становится названием тура.
    Новый тур создаётся, существующий заменяется (с бэкапом), тур делается активным."""
    msg = update.message
    await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
    try:
        tg_file = await msg.document.get_file()
        data = bytes(await tg_file.download_as_bytearray())
        async with _excel_lock:
            name, replaced = await asyncio.to_thread(
                excel_store.import_profile_from_upload, msg.document.file_name, data
            )
    except Exception:
        logger.exception("Ошибка импорта файла тура")
        await msg.reply_text(
            "⚠️ Не смог прочитать файл — он повреждён или это не .xlsx."
        )
        return
    verb = "обновил из файла" if replaced else "создал из файла"
    await msg.reply_text(
        f"✅ Тур «{name}» {verb} и сделал активным.\n"
        "Новые траты пойдут в него. Переключить тур: /profiles."
    )


async def _create_profile_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    name = (msg.text or "").strip()
    context.user_data["awaiting_profile_name"] = False
    if not name:
        await msg.reply_text("Название пустое. Создать тур заново: /profiles")
        return
    if await asyncio.to_thread(settings.profile_exists, name):
        await asyncio.to_thread(settings.set_active_profile, name)
        await msg.reply_text(f"📂 Тур «{name}» уже есть — сделал его активным.")
        return
    async with _excel_lock:
        await asyncio.to_thread(excel_store.create_profile, name)
        await asyncio.to_thread(settings.set_active_profile, name)
    context.user_data["awaiting_wallet_for"] = name
    await msg.reply_text(
        f"✅ Тур «{name}» создан (отдельный файл) и активирован.\n\n"
        "Начальный баланс кошелька можно указать одним сообщением:\n"
        "«1460000 тенге 7000 долларов»\n\n"
        "Чтобы пропустить, напиши «-»."
    )


async def _start_balance_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    name = context.user_data.pop("awaiting_wallet_for", None)
    if not name:
        return
    text = (msg.text or "").strip()
    skip = text.lower() in {"-", "0", "нет", "пропустить", "skip", "пусто", "later", "потом"}
    pairs = [] if skip else await asyncio.to_thread(currency.parse_amounts, text)
    if not pairs:
        await msg.reply_text(
            f"Ок, тур «{name}» без начального баланса. "
            "Пополнить позже: /add 5000 USD"
        )
        return
    async with _excel_lock:
        await asyncio.to_thread(settings.set_active_profile, name)
        net = await asyncio.to_thread(excel_store.set_start_balance, pairs)
        start = await asyncio.to_thread(excel_store.get_start_balance)
        t = await asyncio.to_thread(excel_store.compute_totals)
    await msg.reply_text(
        f"✅ Начальный баланс тура «{name}»: {_format_start_balance(start)}\n\n"
        + _format_wallet(net, t["per_currency"])
        + "\n\nОшибся в сумме? Поправить: /startbalance"
    )


async def _prompt_start_balance(msg, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает текущий начальный баланс активного профиля и ждёт новый."""
    profile = await asyncio.to_thread(settings.get_active_profile)
    start = await asyncio.to_thread(excel_store.get_start_balance)
    context.user_data["awaiting_edit"] = None
    context.user_data["awaiting_profile_name"] = False
    context.user_data.pop("awaiting_wallet_for", None)
    context.user_data["awaiting_start_balance"] = True
    await msg.reply_text(
        f"📂 Тур: {profile}\n"
        f"Текущий начальный баланс: {_format_start_balance(start)}\n\n"
        "Пришли правильный начальный баланс одним сообщением:\n"
        "«1460000 тенге 7000 долларов»\n\n"
        "«0» — обнулить, «-» — отмена. Пополнения и обмены не трону."
    )


async def start_balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _prompt_start_balance(update.message, context)


async def _apply_start_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    context.user_data["awaiting_start_balance"] = False
    text = (msg.text or "").strip()
    low = text.lower()
    if low in {"-", "отмена", "нет", "cancel", "пропустить", "skip"}:
        await msg.reply_text("Ок, начальный баланс не меняю.")
        return
    if low in {"0", "ноль", "пусто"}:
        pairs = []
    else:
        pairs = await asyncio.to_thread(currency.parse_amounts, text)
        if not pairs:
            context.user_data["awaiting_start_balance"] = True  # дать повторить ввод
            await msg.reply_text(
                "Не понял сумму. Пример: «1460000 тенге 7000 долларов».\n"
                "«0» — обнулить, «-» — отмена."
            )
            return
    async with _excel_lock:
        net = await asyncio.to_thread(excel_store.set_start_balance, pairs)
        start = await asyncio.to_thread(excel_store.get_start_balance)
        t = await asyncio.to_thread(excel_store.compute_totals)
    profile = await asyncio.to_thread(settings.get_active_profile)
    await msg.reply_text(
        f"✅ Начальный баланс тура «{profile}»: {_format_start_balance(start)}\n\n"
        + _format_wallet(net, t["per_currency"])
    )


async def _apply_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: dict) -> None:
    msg = update.message
    context.user_data["awaiting_edit"] = None
    expense_id, field = edit["id"], edit["field"]
    text = (msg.text or "").strip()
    if field == "amt":
        parsed = await asyncio.to_thread(currency.parse_single_amount, text)
        if parsed is None or parsed[0] <= 0:
            await msg.reply_text("Нужно положительное число: «4500» или «30 долларов».")
            return
        amount, cur = parsed
        async with _excel_lock:
            entry = await asyncio.to_thread(
                excel_store.update_amount, expense_id, amount, cur
            )
    else:
        if not text:
            await msg.reply_text("Описание пустое. Пришли новый текст одним сообщением.")
            return
        async with _excel_lock:
            entry = await asyncio.to_thread(
                excel_store.update_description, expense_id, text
            )
    if entry is None:
        await msg.reply_text("Трата не найдена — возможно, удалена.")
        return
    await _enrich(entry)
    try:
        await context.bot.edit_message_text(
            _format_reply(entry),
            chat_id=edit["chat_id"],
            message_id=edit["message_id"],
            reply_markup=_expense_keyboard(expense_id, entry["category"]),
        )
    except Exception:
        pass
    await msg.reply_text("✅ Обновил трату.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    text = msg.text or ""
    edit = context.user_data.get("awaiting_edit")
    if edit:
        await _apply_edit(update, context, edit)
        return
    if context.user_data.get("awaiting_profile_name"):
        await _create_profile_flow(update, context)
        return
    if context.user_data.get("awaiting_wallet_for"):
        await _start_balance_flow(update, context)
        return
    if context.user_data.get("awaiting_start_balance"):
        await _apply_start_balance(update, context)
        return
    # Реальный обмен: глагол действия + минимум две суммы → двигаем кошелёк.
    if _ACTION_RE.search(text):
        pairs = await asyncio.to_thread(currency.parse_amounts, text)
        kind = _action_kind(pairs)
        if kind == "exchange":
            await _exchange_reply(msg, pairs)
            return
        if kind == "ask":
            # Недописанный обмен: не пишем трату-призрак, просим явный формат.
            await msg.reply_text(_EXCHANGE_HELP)
            return
        # kind == "expense": валюты не было — это обычная трата, пишем как обычно.
    await _record_expense(msg, msg.text)


async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _excel_lock:
        entry = await asyncio.to_thread(excel_store.last_entry)
        if entry is None:
            await update.message.reply_text("Нет трат для удаления.")
            return
        entry = await asyncio.to_thread(excel_store.delete_expense, entry["id"])
    if entry is None:
        await update.message.reply_text("Нет трат для удаления.")
        return
    await update.message.reply_text(
        f"🗑 Удалена последняя трата:\n"
        f"{entry['description']} — {entry['amount']:,.2f} {entry['currency']} "
        f"({entry['category']})"
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data or ""
    parts = data.split("|")

    if parts[0] == "setcur" and len(parts) == 2:
        cur = parts[1].upper()
        current = await asyncio.to_thread(settings.get_default_currency)
        if cur == current:
            await query.answer(f"Уже выбрано: {cur}")
            return
        ok = await asyncio.to_thread(settings.set_default_currency, cur)
        if not ok:
            await query.answer("Неизвестная валюта.")
            return
        saved = await asyncio.to_thread(settings.get_default_currency)
        text = _currency_text(saved, changed=True)
        await query.answer(f"Сохранено: {saved}")
        try:
            await query.edit_message_text(text, reply_markup=_currency_keyboard(saved))
        except BadRequest:
            await query.message.reply_text(text, reply_markup=_currency_keyboard(saved))
        return

    if parts[0] == "prof_new":
        context.user_data["awaiting_profile_name"] = True
        await query.answer()
        await query.message.reply_text(
            "📂 Пришли название нового тура одним сообщением "
            "(например «Италия 2026» или «Тур 2027»)."
        )
        return

    if parts[0] == "setstart":
        await query.answer()
        await _prompt_start_balance(query.message, context)
        return

    if parts[0] == "prof" and len(parts) == 2:
        names = await asyncio.to_thread(settings.list_profiles)
        try:
            name = names[int(parts[1])]
        except (ValueError, IndexError):
            await query.answer("Тур не найден.")
            return
        await asyncio.to_thread(settings.set_active_profile, name)
        can_delete = await asyncio.to_thread(_is_admin, query.from_user.id)
        await query.answer(f"Тур: {name}")
        await query.edit_message_text(
            _PROFILES_INTRO.format(active=name),
            reply_markup=_profiles_keyboard(name, names, can_delete),
        )
        return

    if parts[0] == "prof_menu":
        active = await asyncio.to_thread(settings.get_active_profile)
        names = await asyncio.to_thread(settings.list_profiles)
        can_delete = await asyncio.to_thread(_is_admin, query.from_user.id)
        await query.answer()
        await query.edit_message_text(
            _PROFILES_INTRO.format(active=active),
            reply_markup=_profiles_keyboard(active, names, can_delete),
        )
        return

    if parts[0] == "prof_delmenu":
        if not await asyncio.to_thread(_is_admin, query.from_user.id):
            await query.answer("⛔️ Удалять туры может только владелец.", show_alert=True)
            return
        names = await asyncio.to_thread(settings.list_profiles)
        if len(names) <= 1:
            await query.answer("Это единственный тур — удалить нельзя.", show_alert=True)
            return
        active = await asyncio.to_thread(settings.get_active_profile)
        await query.answer()
        await query.edit_message_text(
            "🗑 Какой тур удалить? Выбери из списка.\n"
            "Файл тура уйдёт в бэкап, из бота тур пропадёт.",
            reply_markup=_profiles_delete_keyboard(active, names),
        )
        return

    if parts[0] == "profdel" and len(parts) == 2:
        if not await asyncio.to_thread(_is_admin, query.from_user.id):
            await query.answer("⛔️ Удалять туры может только владелец.", show_alert=True)
            return
        names = await asyncio.to_thread(settings.list_profiles)
        try:
            name = names[int(parts[1])]
        except (ValueError, IndexError):
            await query.answer("Тур не найден.")
            return
        await query.answer()
        await query.edit_message_text(
            f"🗑 Удалить тур «{name}»?\n"
            "Траты и кошелёк этого тура пропадут из бота (файл сохраню в бэкап).",
            reply_markup=_profile_confirm_delete_keyboard(int(parts[1])),
        )
        return

    if parts[0] == "profdelok" and len(parts) == 2:
        if not await asyncio.to_thread(_is_admin, query.from_user.id):
            await query.answer("⛔️ Удалять туры может только владелец.", show_alert=True)
            return
        names = await asyncio.to_thread(settings.list_profiles)
        try:
            name = names[int(parts[1])]
        except (ValueError, IndexError):
            await query.answer("Тур не найден.")
            return
        async with _excel_lock:
            info = await asyncio.to_thread(excel_store.delete_profile, name)
        if info is None:
            await query.answer("Не удалось удалить (последний тур?).", show_alert=True)
            return
        active = await asyncio.to_thread(settings.get_active_profile)
        names = await asyncio.to_thread(settings.list_profiles)
        can_delete = await asyncio.to_thread(_is_admin, query.from_user.id)
        await query.answer(f"Тур «{name}» удалён.")
        await query.edit_message_text(
            f"🗑 Тур «{name}» удалён (файл в бэкапе).\n\n"
            + _PROFILES_INTRO.format(active=active),
            reply_markup=_profiles_keyboard(active, names, can_delete),
        )
        return

    if parts[0] in ("wdlist", "wdclose", "wdel", "wdelc"):
        if not await asyncio.to_thread(_is_admin, query.from_user.id):
            await query.answer("⛔️ Удалять движения может только владелец.", show_alert=True)
            return
        if parts[0] == "wdclose":
            await query.answer()
            profile = await asyncio.to_thread(settings.get_active_profile)
            net = await asyncio.to_thread(excel_store.wallet_net)
            t = await asyncio.to_thread(excel_store.compute_totals)
            await query.edit_message_text(
                f"📂 Тур: {profile}\n\n" + _format_wallet(net, t["per_currency"]),
                reply_markup=_wallet_keyboard(True),
            )
            return
        if parts[0] == "wdlist":
            await query.answer()
            await _show_movements(query)
            return
        try:
            movement_id = int(parts[1])
        except (IndexError, ValueError):
            await query.answer("Не понял движение.")
            return
        if parts[0] == "wdel":  # запрос подтверждения — деньги не трогаем
            rows = await asyncio.to_thread(excel_store.list_movements)
            groups = {g["id"]: g for g in _group_movements(rows)}
            g = groups.get(movement_id)
            if g is None:
                await query.answer("Движение не найдено (возможно, уже удалено).")
                await _show_movements(query)
                return
            await query.answer()
            await query.edit_message_text(
                f"🗑 Удалить движение?\n\n• {_movement_label(g)}\n\n"
                "Остаток кошелька пересчитается. Это не трата, а движение кошелька.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Да, удалить", callback_data=f"wdelc|{movement_id}"),
                    InlineKeyboardButton("← Нет", callback_data="wdlist"),
                ]]),
            )
            return
        # wdelc — подтверждённое удаление: двигаем кошелёк.
        async with _excel_lock:
            deleted = await asyncio.to_thread(excel_store.delete_movement, movement_id)
            net = await asyncio.to_thread(excel_store.wallet_net)
            t = await asyncio.to_thread(excel_store.compute_totals)
        if not deleted:
            await query.answer("Движение не найдено (возможно, уже удалено).")
            await _show_movements(query)
            return
        what = "; ".join(
            f"{d['kind']} {(d['amount'] or 0):,.0f} {d['currency']}" for d in deleted
        )
        await query.answer("Удалено.")
        await query.edit_message_text(
            f"🗑 Удалил из кошелька: {what}\n\n" + _format_wallet(net, t["per_currency"]),
            reply_markup=_wallet_keyboard(True),
        )
        return

    if parts[0] in ("exunq", "exapq", "exsa", "exsr", "exap", "exun") and len(parts) == 5:
        g_cur, r_cur = parts[1], parts[3]
        try:
            g_amt, r_amt = float(parts[2]), float(parts[4])
        except ValueError:
            await query.answer("Не понял суммы обмена.")
            return
        gave, received = (g_amt, g_cur), (r_amt, r_cur)

        # Запрос подтверждения и его отмена — только меняем клавиатуру, деньги не
        # трогаем. Реальный обмен/возврат двигает кошелёк только по «exap»/«exun».
        if parts[0] == "exunq":
            await query.answer()
            await query.edit_message_reply_markup(_exchange_confirm_kb("undo", gave, received))
            return
        if parts[0] == "exapq":
            await query.answer()
            await query.edit_message_reply_markup(_exchange_confirm_kb("redo", gave, received))
            return
        if parts[0] == "exsa":  # передумал отменять — вернуть кнопку «Отменить обмен»
            await query.answer("Оставил как есть.")
            await query.edit_message_reply_markup(_exchange_primary_kb("applied", gave, received))
            return
        if parts[0] == "exsr":  # передумал возвращать — оставить «возвращённое» состояние
            await query.answer("Оставил как есть.")
            await query.edit_message_reply_markup(_exchange_primary_kb("reversed", gave, received))
            return

        async with _excel_lock:
            if parts[0] == "exap":
                net = await asyncio.to_thread(
                    excel_store.add_exchange, g_cur, g_amt, r_cur, r_amt
                )
                note, state = "✅ Обмен снова записан в кошелёк.", "applied"
            else:  # exun
                net = await asyncio.to_thread(
                    excel_store.add_exchange, r_cur, r_amt, g_cur, g_amt, "Возврат"
                )
                note, state = "↩️ Обмен отменён, деньги вернул в кошелёк.", "reversed"
            t = await asyncio.to_thread(excel_store.compute_totals)
        text = (
            _format_exchange(gave, received)
            + f"\n\n{note}\n"
            + _format_wallet(net, t["per_currency"])
        )
        await query.answer(note)
        await query.edit_message_text(
            text, reply_markup=_exchange_primary_kb(state, gave, received)
        )
        return

    if parts[0] == "clr" and len(parts) == 2:
        if parts[1] == "yes":
            await query.answer("Очищаю…")
            await _do_clear(context, query.message.chat_id, query.message.message_id)
        else:
            await query.answer("Отменено")
            try:
                await query.edit_message_text("Очистка отменена.")
            except Exception:
                pass
        return

    if parts[0] == "del" and len(parts) == 2:
        try:
            expense_id = int(parts[1])
        except ValueError:
            await query.answer("Не понял трату.")
            return
        async with _excel_lock:
            entry = await asyncio.to_thread(excel_store.delete_expense, expense_id)
        if entry is None:
            await query.answer("Трата не найдена (возможно, уже удалена).")
            return
        await query.answer("Удалено")
        await query.edit_message_text(
            f"🗑 Удалено:\n{entry['description']} — {entry['amount']:,.2f} "
            f"{entry['currency']} ({entry['category']})"
        )
        return

    if parts[0] == "cat" and len(parts) == 3:
        try:
            expense_id = int(parts[1])
        except ValueError:
            await query.answer("Не понял трату.")
            return
        category = parts[2]
        current = await asyncio.to_thread(excel_store.get_expense, expense_id)
        if current is None:
            await query.answer("Трата не найдена (возможно, удалена).")
            return
        if current.get("category") == category:
            await query.answer("Уже выбрано")  # не дёргаем edit — был BadRequest
            return
        async with _excel_lock:
            entry = await asyncio.to_thread(
                excel_store.update_category, expense_id, category
            )
        if entry is None:
            await query.answer("Трата не найдена (возможно, удалена).")
            return
        await _enrich(entry)
        await query.answer(f"Категория: {category}")
        await query.edit_message_text(
            _format_reply(entry), reply_markup=_expense_keyboard(expense_id, category)
        )
        return

    if parts[0] == "edit" and len(parts) == 3:
        try:
            expense_id = int(parts[1])
        except ValueError:
            await query.answer("Не понял трату.")
            return
        field = parts[2]
        if field not in ("amt", "desc"):
            await query.answer()
            return
        current = await asyncio.to_thread(excel_store.get_expense, expense_id)
        if current is None:
            await query.answer("Трата не найдена (возможно, удалена).")
            return
        context.user_data["awaiting_edit"] = {
            "id": expense_id,
            "field": field,
            "chat_id": query.message.chat_id,
            "message_id": query.message.message_id,
        }
        await query.answer()
        await query.message.reply_text(
            "✏️ Пришли новую сумму одним сообщением: «4500» (та же валюта) "
            "или «30 долларов» (со сменой валюты)."
            if field == "amt"
            else "✏️ Пришли новое описание одним сообщением."
        )
        return

    await query.answer()


def _parse_user_ids(args) -> list[int]:
    """Достаёт положительные Telegram-id из аргументов команды (через пробел)."""
    ids: list[int] = []
    for tok in args or []:
        tok = tok.strip().lstrip("@")
        if tok.isdigit():
            uid = int(tok)
            if uid not in ids:
                ids.append(uid)
    return ids


async def allow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Владелец открывает доступ другим пользователям по Telegram-id."""
    msg = update.message
    user = update.effective_user
    if user is None or not await asyncio.to_thread(_is_admin, user.id):
        await msg.reply_text("⛔️ Управлять доступом может только владелец бота.")
        return
    ids = _parse_user_ids(context.args)
    if not ids:
        await msg.reply_text(
            "Кому открыть доступ? Пришли Telegram-id:\n"
            "• /allow 123456789\n"
            "• /allow 123456789 987654321\n\n"
            "Свой id новый пользователь увидит, просто написав боту."
        )
        return
    added, already = [], []
    for uid in ids:
        if await asyncio.to_thread(settings.add_allowed_user_id, uid):
            added.append(uid)
        else:
            already.append(uid)
    lines = []
    if added:
        lines.append("✅ Доступ открыт: " + ", ".join(map(str, added)))
    if already:
        lines.append("ℹ️ Уже был доступ: " + ", ".join(map(str, already)))
    await msg.reply_text("\n".join(lines))


async def disallow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Владелец закрывает доступ. Себя и список из .env снять нельзя."""
    msg = update.message
    user = update.effective_user
    if user is None or not await asyncio.to_thread(_is_admin, user.id):
        await msg.reply_text("⛔️ Управлять доступом может только владелец бота.")
        return
    ids = _parse_user_ids(context.args)
    if not ids:
        await msg.reply_text("Кого убрать? Пример: /disallow 123456789")
        return
    owner = await asyncio.to_thread(settings.get_owner_id)
    removed, missing, protected = [], [], []
    for uid in ids:
        if uid in ALLOWED_USER_IDS or uid == owner:
            protected.append(uid)
        elif await asyncio.to_thread(settings.remove_allowed_user_id, uid):
            removed.append(uid)
        else:
            missing.append(uid)
    lines = []
    if removed:
        lines.append("✅ Доступ закрыт: " + ", ".join(map(str, removed)))
    if missing:
        lines.append("ℹ️ Не было в списке: " + ", ".join(map(str, missing)))
    if protected:
        lines.append(
            "⛔️ Нельзя убрать здесь (владелец или из .env): "
            + ", ".join(map(str, protected))
        )
    await msg.reply_text("\n".join(lines))


async def allowed_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает, у кого сейчас есть доступ к боту."""
    msg = update.message
    user = update.effective_user
    if user is None or not await asyncio.to_thread(_is_admin, user.id):
        await msg.reply_text("⛔️ Список доступа видит только владелец бота.")
        return
    owner = await asyncio.to_thread(settings.get_owner_id)
    granted = sorted(await asyncio.to_thread(settings.get_allowed_user_ids))
    lines = ["👥 Доступ к боту:"]
    if ALLOWED_USER_IDS:
        lines.append("• из .env: " + ", ".join(map(str, sorted(ALLOWED_USER_IDS))))
    if owner is not None:
        lines.append(f"• владелец: {owner}")
    if granted:
        lines.append("• выдан через /allow: " + ", ".join(map(str, granted)))
    lines.append("")
    lines.append("Добавить: /allow <id> · убрать: /disallow <id>")
    await msg.reply_text("\n".join(lines))


_BOT_COMMANDS = [
    BotCommand("start", "Старт и инструкция"),
    BotCommand("help", "Все команды"),
    BotCommand("total", "Сводка по тратам"),
    BotCommand("wallet", "Кошелёк: остаток по валютам"),
    BotCommand("add", "Пополнить кошелёк (/add 5000 USD)"),
    BotCommand("currency", "Валюта по умолчанию"),
    BotCommand("profiles", "Туры: у каждого свой файл"),
    BotCommand("excel", "Выгрузить файл тура"),
    BotCommand("undo", "Удалить последнюю трату"),
    BotCommand("clear", "Очистить чат"),
]


async def _post_init(app: Application) -> None:
    await asyncio.to_thread(settings.ensure_defaults)
    await asyncio.to_thread(excel_store.migrate_to_files)
    await asyncio.to_thread(excel_store.ensure_ids_setup)
    await asyncio.to_thread(excel_store.ensure_wallet_setup)
    await asyncio.to_thread(excel_store.ensure_wallet_ids_setup)
    await app.bot.set_my_commands(_BOT_COMMANDS)
    logger.info("Меню команд зарегистрировано.")


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(_post_init).build()
    # group=-1: проверка владельца до всех остальных хендлеров.
    app.add_handler(TypeHandler(Update, _auth_gate), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("total", total))
    app.add_handler(CommandHandler("excel", excel_cmd))
    app.add_handler(CommandHandler("bills", bills_cmd))  # секретная, нет в /help и меню
    app.add_handler(CommandHandler("undo", undo))
    app.add_handler(CommandHandler("delete", undo))
    app.add_handler(CommandHandler("currency", currency_cmd))
    app.add_handler(CommandHandler("profiles", profiles_cmd))
    app.add_handler(CommandHandler("wallet", wallet_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("startbalance", start_balance_cmd))
    app.add_handler(CommandHandler("allow", allow_cmd))
    app.add_handler(CommandHandler("disallow", disallow_cmd))
    app.add_handler(CommandHandler("allowed", allowed_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.FileExtension("xlsx"), handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(on_error)
    logger.info("Бот запущен. Ожидаю сообщения...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
