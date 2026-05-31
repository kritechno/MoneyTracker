import csv
import io
import os
import re
import tempfile
from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import currency
import settings
from config import CATEGORIES, CURRENCIES, CURRENCY_LABELS, EXCEL_PATH

_HEADERS = ["Дата", "Описание", "Сумма", "Валюта", "Категория", "Сумма USD", "ID"]
_DATA_WIDTHS = [12, 34, 12, 10, 14, 12, 8]
_ID_COL = 7  # технический столбец стабильного идентификатора (скрыт)
_WALLET_HEADERS = ["Дата", "Тип", "Валюта", "Сумма", "Примечание"]
_WALLET_WIDTHS = [12, 16, 10, 14, 30]
_HEADER_FILL = PatternFill("solid", fgColor="4472C4")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_TITLE_FONT = Font(bold=True, size=12)
_INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def _active_sheets() -> tuple[str, str]:
    return settings.get_active_sheets()


def _atomic_save(wb: Workbook) -> None:
    """Сохраняет книгу через временный файл + os.replace, чтобы аварийное
    завершение (сон Мака, перезапуск) не оставило битый .xlsx."""
    folder = os.path.dirname(os.path.abspath(EXCEL_PATH))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".expenses_", suffix=".xlsx")
    os.close(fd)
    try:
        wb.save(tmp)
        os.replace(tmp, EXCEL_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _init_wallet_sheet(ws) -> None:
    for col, header in enumerate(_WALLET_HEADERS, start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
    for i, w in enumerate(_WALLET_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def _append_movement(ws, kind: str, currency: str, amount: float, note: str = "") -> None:
    next_row = ws.max_row + 1 if ws.max_row >= 1 else 2
    if next_row < 2:
        next_row = 2
    d = ws.cell(row=next_row, column=1, value=date.today())
    d.number_format = "yyyy-mm-dd"
    ws.cell(row=next_row, column=2, value=kind)
    ws.cell(row=next_row, column=3, value=currency)
    c = ws.cell(row=next_row, column=4, value=round(float(amount), 2))
    c.number_format = "#,##0.00"
    ws.cell(row=next_row, column=5, value=note)


def _wallet_net_from_ws(ws) -> dict:
    net = {cur: 0.0 for cur in CURRENCIES}
    for row in ws.iter_rows(min_row=2, values_only=True):
        cur, amt = row[2], row[3]
        if cur in net and isinstance(amt, (int, float)):
            net[cur] += amt
    return {cur: round(v, 2) for cur, v in net.items()}


def _gen_wallet_name(name: str, existing: set[str]) -> str:
    if name == settings.DEFAULT_PROFILE and "Кошелёк" not in existing:
        return "Кошелёк"
    return _safe_sheet_name(name, existing, prefix="Кошелёк ")


def _init_data_sheet(ws) -> None:
    for col, header in enumerate(_HEADERS, start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
    for i, w in enumerate(_DATA_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.column_dimensions[get_column_letter(_ID_COL)].hidden = True
    ws.freeze_panes = "A2"


def _next_id(ws) -> int:
    """Следующий свободный идентификатор траты на листе (max существующих + 1)."""
    mx = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        v = row[_ID_COL - 1] if len(row) >= _ID_COL else None
        if isinstance(v, int) and v > mx:
            mx = v
    return mx + 1


def _find_row_by_id(ws, expense_id: int) -> int | None:
    """Физический номер строки по стабильному id, либо None."""
    for row in range(2, ws.max_row + 1):
        if ws.cell(row=row, column=_ID_COL).value == expense_id:
            return row
    return None


def _ensure_ids(ws) -> bool:
    """Миграция: гарантирует заголовок ID и присваивает id строкам без него."""
    changed = False
    if ws.cell(row=1, column=_ID_COL).value != "ID":
        cell = ws.cell(row=1, column=_ID_COL, value="ID")
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(_ID_COL)].hidden = True
        changed = True
    nxt = _next_id(ws)
    for row in range(2, ws.max_row + 1):
        has_data = ws.cell(row=row, column=3).value is not None
        cur_id = ws.cell(row=row, column=_ID_COL).value
        if has_data and not isinstance(cur_id, int):
            ws.cell(row=row, column=_ID_COL, value=nxt)
            nxt += 1
            changed = True
    return changed


def _new_workbook() -> Workbook:
    data_sheet, summary_sheet = _active_sheets()
    wb = Workbook()
    ws = wb.active
    ws.title = data_sheet
    _init_data_sheet(ws)
    wb.create_sheet(summary_sheet)
    return wb


def _load() -> Workbook:
    data_sheet, summary_sheet = _active_sheets()
    wallet_sheet = settings.get_active_wallet_sheet()
    if os.path.exists(EXCEL_PATH):
        wb = load_workbook(EXCEL_PATH)
        if data_sheet not in wb.sheetnames:
            ws = wb.create_sheet(data_sheet)
            _init_data_sheet(ws)
        if summary_sheet not in wb.sheetnames:
            wb.create_sheet(summary_sheet)
        if wallet_sheet and wallet_sheet not in wb.sheetnames:
            _init_wallet_sheet(wb.create_sheet(wallet_sheet))
        return wb
    return _new_workbook()


def _parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return date.today()


def _coerce_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _aggregate(ws, since: date | None = None) -> dict:
    """Суммирует строки листа расходов в готовые числа (без формул Excel).
    since — нижняя граница даты включительно (для периодных сводок)."""
    per_currency = {cur: 0.0 for cur in CURRENCIES}
    per_category = {cat: 0.0 for cat in CATEGORIES}
    total_usd = 0.0
    count = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        amount, cur, cat, usd = row[2], row[3], row[4], row[5]
        if amount is None and usd is None:
            continue
        if since is not None:
            d = _coerce_date(row[0])
            if d is None or d < since:
                continue
        count += 1
        if cur in per_currency and isinstance(amount, (int, float)):
            per_currency[cur] += amount
        if cat in per_category and isinstance(usd, (int, float)):
            per_category[cat] += usd
        if isinstance(usd, (int, float)):
            total_usd += usd
    return {
        "per_currency": per_currency,
        "per_category": per_category,
        "total_usd": round(total_usd, 2),
        "count": count,
    }


def _rebuild_summary(
    wb: Workbook, data_sheet: str, summary_sheet: str, wallet_sheet: str | None = None
) -> None:
    ws = wb[summary_sheet]
    ws.delete_rows(1, ws.max_row or 1)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 16

    totals = _aggregate(wb[data_sheet])
    net = None
    if wallet_sheet and wallet_sheet in wb.sheetnames:
        net = _wallet_net_from_ws(wb[wallet_sheet])
    row = 1

    if net is not None:
        ws.cell(row=row, column=1, value="Остаток в кошельке").font = _TITLE_FONT
        row += 1
        for cur in CURRENCIES:
            label = f"{CURRENCY_LABELS.get(cur, cur)} ({cur})"
            remaining = round(net.get(cur, 0.0) - totals["per_currency"][cur], 2)
            ws.cell(row=row, column=1, value=label)
            c = ws.cell(row=row, column=2, value=remaining)
            c.number_format = "#,##0.00"
            row += 1
        row += 1

    ws.cell(row=row, column=1, value="Итого по валютам").font = _TITLE_FONT
    row += 1
    for cur in CURRENCIES:
        label = f"{CURRENCY_LABELS.get(cur, cur)} ({cur})"
        ws.cell(row=row, column=1, value=label)
        c = ws.cell(row=row, column=2, value=round(totals["per_currency"][cur], 2))
        c.number_format = "#,##0.00"
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="Всего в USD").font = _TITLE_FONT
    c = ws.cell(row=row, column=2, value=totals["total_usd"])
    c.number_format = "#,##0.00"
    c.font = _TITLE_FONT
    row += 2

    ws.cell(row=row, column=1, value="Итого по категориям (USD)").font = _TITLE_FONT
    row += 1
    for cat in CATEGORIES:
        ws.cell(row=row, column=1, value=cat)
        c = ws.cell(row=row, column=2, value=round(totals["per_category"][cat], 2))
        c.number_format = "#,##0.00"
        row += 1


def compute_totals(since: date | None = None) -> dict:
    """Считает итоги по активному профилю: суммы по валютам, общую в USD
    и суммы по категориям (в USD). since — нижняя граница даты включительно."""
    empty = {
        "per_currency": {cur: 0.0 for cur in CURRENCIES},
        "per_category": {cat: 0.0 for cat in CATEGORIES},
        "total_usd": 0.0,
        "count": 0,
    }
    data_sheet, _ = _active_sheets()

    if not os.path.exists(EXCEL_PATH):
        return empty

    wb = load_workbook(EXCEL_PATH, data_only=True)
    if data_sheet not in wb.sheetnames:
        return empty
    return _aggregate(wb[data_sheet], since)


def add_expense(entry: dict) -> int:
    """entry: {date, description, amount, currency, category, amount_usd}.
    Пишет в лист активного профиля, возвращает стабильный id траты."""
    data_sheet, summary_sheet = _active_sheets()
    wb = _load()
    ws = wb[data_sheet]
    _ensure_ids(ws)
    next_row = ws.max_row + 1 if ws.max_row >= 1 else 2
    if next_row < 2:
        next_row = 2
    expense_id = _next_id(ws)

    date_cell = ws.cell(row=next_row, column=1, value=_parse_date(entry["date"]))
    date_cell.number_format = "yyyy-mm-dd"
    ws.cell(row=next_row, column=2, value=entry["description"])
    amt = ws.cell(row=next_row, column=3, value=entry["amount"])
    amt.number_format = "#,##0.00"
    ws.cell(row=next_row, column=4, value=entry["currency"])
    ws.cell(row=next_row, column=5, value=entry["category"])
    usd = ws.cell(row=next_row, column=6, value=entry["amount_usd"])
    usd.number_format = "#,##0.00"
    ws.cell(row=next_row, column=_ID_COL, value=expense_id)

    wallet_sheet = settings.get_active_wallet_sheet()
    _rebuild_summary(wb, data_sheet, summary_sheet, wallet_sheet)
    _atomic_save(wb)
    return expense_id


def _row_to_entry(ws, row: int) -> dict | None:
    if row < 2 or row > ws.max_row:
        return None
    d, desc, amt, cur, cat, usd = (ws.cell(row=row, column=c).value for c in range(1, 7))
    if amt is None and usd is None:
        return None
    return {
        "date": d.date().isoformat() if isinstance(d, datetime) else str(d),
        "description": desc,
        "amount": amt,
        "currency": cur,
        "category": cat,
        "amount_usd": usd,
        "id": ws.cell(row=row, column=_ID_COL).value,
    }


def get_expense(expense_id: int) -> dict | None:
    if not os.path.exists(EXCEL_PATH):
        return None
    data_sheet, _ = _active_sheets()
    wb = load_workbook(EXCEL_PATH)
    if data_sheet not in wb.sheetnames:
        return None
    ws = wb[data_sheet]
    row = _find_row_by_id(ws, expense_id)
    return _row_to_entry(ws, row) if row else None


def last_entry() -> dict | None:
    """Последняя добавленная трата активного профиля (для /undo), либо None."""
    if not os.path.exists(EXCEL_PATH):
        return None
    data_sheet, _ = _active_sheets()
    wb = load_workbook(EXCEL_PATH)
    if data_sheet not in wb.sheetnames:
        return None
    ws = wb[data_sheet]
    for row in range(ws.max_row, 1, -1):
        if ws.cell(row=row, column=3).value is not None:
            return _row_to_entry(ws, row)
    return None


def update_category(expense_id: int, category: str) -> dict | None:
    """Меняет категорию траты по стабильному id."""
    data_sheet, summary_sheet = _active_sheets()
    wb = _load()
    ws = wb[data_sheet]
    row = _find_row_by_id(ws, expense_id)
    entry = _row_to_entry(ws, row) if row else None
    if entry is None:
        return None
    ws.cell(row=row, column=5, value=category)
    entry["category"] = category
    wallet_sheet = settings.get_active_wallet_sheet()
    _rebuild_summary(wb, data_sheet, summary_sheet, wallet_sheet)
    _atomic_save(wb)
    return entry


def update_amount(
    expense_id: int, amount: float, cur: str | None = None
) -> dict | None:
    """Меняет сумму (и при желании валюту) траты по id, пересчитывает USD."""
    data_sheet, summary_sheet = _active_sheets()
    wb = _load()
    ws = wb[data_sheet]
    row = _find_row_by_id(ws, expense_id)
    entry = _row_to_entry(ws, row) if row else None
    if entry is None:
        return None
    amount = round(float(amount), 2)
    new_cur = (cur or entry["currency"] or "USD").upper()
    if new_cur not in CURRENCIES:
        new_cur = entry["currency"]
    usd = currency.to_usd(amount, new_cur)
    ws.cell(row=row, column=3, value=amount).number_format = "#,##0.00"
    ws.cell(row=row, column=4, value=new_cur)
    ws.cell(row=row, column=6, value=usd).number_format = "#,##0.00"
    entry.update(amount=amount, currency=new_cur, amount_usd=usd)
    wallet_sheet = settings.get_active_wallet_sheet()
    _rebuild_summary(wb, data_sheet, summary_sheet, wallet_sheet)
    _atomic_save(wb)
    return entry


def update_description(expense_id: int, description: str) -> dict | None:
    """Меняет описание траты по id."""
    data_sheet, summary_sheet = _active_sheets()
    wb = _load()
    ws = wb[data_sheet]
    row = _find_row_by_id(ws, expense_id)
    entry = _row_to_entry(ws, row) if row else None
    if entry is None:
        return None
    description = (description or "").strip() or entry["description"]
    ws.cell(row=row, column=2, value=description)
    entry["description"] = description
    wallet_sheet = settings.get_active_wallet_sheet()
    _rebuild_summary(wb, data_sheet, summary_sheet, wallet_sheet)
    _atomic_save(wb)
    return entry


def delete_expense(expense_id: int) -> dict | None:
    """Удаляет трату по стабильному id (id остальных строк не меняются)."""
    data_sheet, summary_sheet = _active_sheets()
    wb = _load()
    ws = wb[data_sheet]
    row = _find_row_by_id(ws, expense_id)
    entry = _row_to_entry(ws, row) if row else None
    if entry is None:
        return None
    ws.delete_rows(row, 1)
    wallet_sheet = settings.get_active_wallet_sheet()
    _rebuild_summary(wb, data_sheet, summary_sheet, wallet_sheet)
    _atomic_save(wb)
    return entry


def profile_csv() -> bytes | None:
    """CSV активного профиля (UTF-8 с BOM, чтобы Excel читал кириллицу)."""
    if not os.path.exists(EXCEL_PATH):
        return None
    data_sheet, _ = _active_sheets()
    wb = load_workbook(EXCEL_PATH, data_only=True)
    if data_sheet not in wb.sheetnames:
        return None
    ws = wb[data_sheet]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_HEADERS[:6])  # без технического ID
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[2] is None and row[5] is None:
            continue
        d = _coerce_date(row[0])
        writer.writerow([
            d.isoformat() if d else (row[0] or ""),
            row[1] or "",
            row[2] if row[2] is not None else "",
            row[3] or "",
            row[4] or "",
            row[5] if row[5] is not None else "",
        ])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _safe_sheet_name(base: str, existing: set[str], prefix: str = "") -> str:
    """Корректное имя листа Excel (<=31 симв., без запрещённых символов, уникальное)."""
    cleaned = _INVALID_SHEET_CHARS.sub(" ", base).strip() or "Профиль"
    candidate = (prefix + cleaned)[:31].strip()
    n = 2
    while candidate in existing or not candidate:
        suffix = f" {n}"
        candidate = (prefix + cleaned)[: 31 - len(suffix)].strip() + suffix
        n += 1
    return candidate


def create_profile(name: str) -> dict:
    """Создаёт новый профиль: листы данных/итогов/кошелька и запись в настройках."""
    name = name.strip()
    wb = _load()
    existing = set(wb.sheetnames)
    data_sheet = _safe_sheet_name(name, existing)
    existing.add(data_sheet)
    summary_sheet = _safe_sheet_name(name, existing, prefix="Итоги ")
    existing.add(summary_sheet)
    wallet_sheet = _gen_wallet_name(name, existing)
    existing.add(wallet_sheet)

    _init_data_sheet(wb.create_sheet(data_sheet))
    wb.create_sheet(summary_sheet)
    _init_wallet_sheet(wb.create_sheet(wallet_sheet))
    _rebuild_summary(wb, data_sheet, summary_sheet, wallet_sheet)
    _atomic_save(wb)

    settings.register_profile(name, data_sheet, summary_sheet, wallet_sheet)
    return {
        "name": name,
        "data": data_sheet,
        "summary": summary_sheet,
        "wallet": wallet_sheet,
    }


# --- Кошелёк ----------------------------------------------------------------
# Лист «Кошелёк» — единственный источник истины по остатку. Каждая строка —
# движение (Старт/Пополнение/Обмен) со знаком: поступления «+», выдачи «−».
# Чистый остаток = сумма движений; доступно = остаток − траты по валюте.


def wallet_net(name: str | None = None) -> dict:
    """Чистый остаток кошелька по валютам (поступления + обмены − выдачи)."""
    empty = {cur: 0.0 for cur in CURRENCIES}
    wallet_sheet = settings.get_wallet_sheet(name)
    if not wallet_sheet or not os.path.exists(EXCEL_PATH):
        return empty
    wb = load_workbook(EXCEL_PATH, data_only=True)
    if wallet_sheet not in wb.sheetnames:
        return empty
    return _wallet_net_from_ws(wb[wallet_sheet])


def add_movement(kind: str, currency: str, amount: float, note: str = "") -> dict:
    """Добавляет одно движение в кошелёк активного профиля, возвращает остаток."""
    data_sheet, summary_sheet = _active_sheets()
    wallet_sheet = settings.get_active_wallet_sheet()
    wb = _load()
    ws = wb[wallet_sheet]
    _append_movement(ws, kind, currency, amount, note)
    _rebuild_summary(wb, data_sheet, summary_sheet, wallet_sheet)
    _atomic_save(wb)
    return _wallet_net_from_ws(ws)


def add_exchange(
    out_cur: str,
    out_amt: float,
    in_cur: str,
    in_amt: float,
    kind: str = "Обмен",
) -> dict:
    """Записывает обмен: списывает out_cur и зачисляет in_cur. Возвращает остаток."""
    data_sheet, summary_sheet = _active_sheets()
    wallet_sheet = settings.get_active_wallet_sheet()
    wb = _load()
    ws = wb[wallet_sheet]
    _append_movement(ws, kind, out_cur, -abs(float(out_amt)), f"→ {in_amt:,.2f} {in_cur}")
    _append_movement(ws, kind, in_cur, abs(float(in_amt)), f"← {out_amt:,.2f} {out_cur}")
    _rebuild_summary(wb, data_sheet, summary_sheet, wallet_sheet)
    _atomic_save(wb)
    return _wallet_net_from_ws(ws)


def ensure_ids_setup() -> None:
    """Миграция: проставляет стабильные ID существующим тратам всех профилей."""
    if not os.path.exists(EXCEL_PATH):
        return
    profiles = settings.all_profiles()
    wb = load_workbook(EXCEL_PATH)
    changed = False
    for sheets in profiles.values():
        data_sheet = sheets.get("data")
        if data_sheet in wb.sheetnames and _ensure_ids(wb[data_sheet]):
            changed = True
    if changed:
        _atomic_save(wb)


def ensure_wallet_setup() -> None:
    """Миграция: создаёт листы «Кошелёк» для всех профилей, переносит старые
    остатки из settings.json в строки «Старт» и регистрирует листы."""
    profiles = settings.all_profiles()
    if not profiles:
        return
    wb = _load()
    existing = set(wb.sheetnames)
    changed = False

    for name, sheets in profiles.items():
        wallet_sheet = sheets.get("wallet_sheet")
        if not wallet_sheet:
            wallet_sheet = _gen_wallet_name(name, existing)
        existing.add(wallet_sheet)
        if wallet_sheet not in wb.sheetnames:
            _init_wallet_sheet(wb.create_sheet(wallet_sheet))
            changed = True
        if sheets.get("wallet_sheet") != wallet_sheet:
            settings.set_wallet_sheet(name, wallet_sheet)
            changed = True

        ws = wb[wallet_sheet]
        legacy = settings.get_legacy_wallet(name)
        if legacy and ws.max_row < 2:
            for cur in CURRENCIES:
                amt = legacy.get(cur)
                if isinstance(amt, (int, float)) and amt:
                    _append_movement(ws, "Старт", cur, amt, "Начальный баланс")
            settings.clear_legacy_wallet(name)
            changed = True

    if changed:
        for name, sheets in settings.all_profiles().items():
            data_sheet = sheets.get("data")
            summary_sheet = sheets.get("summary")
            wallet_sheet = sheets.get("wallet_sheet")
            if data_sheet in wb.sheetnames and summary_sheet in wb.sheetnames:
                _rebuild_summary(wb, data_sheet, summary_sheet, wallet_sheet)
        _atomic_save(wb)
