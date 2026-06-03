import os
import re
from datetime import datetime

from config import BILLS_DIR


def _slug(caption: str) -> str:
    return re.sub(r"[^\w]+", "_", caption or "", flags=re.UNICODE).strip("_")[:40]


def save_bill(image_bytes: bytes, caption: str = "", ext: str = "jpg") -> str:
    """Сохраняет приложенное фото-чек в папку чеков, возвращает путь к файлу.
    Имя = дата_время + кусок подписи, чтобы чек было легко опознать."""
    os.makedirs(BILLS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slug(caption)
    base = f"{stamp}_{slug}" if slug else stamp
    path = os.path.join(BILLS_DIR, f"{base}.{ext}")
    i = 1
    while os.path.exists(path):
        path = os.path.join(BILLS_DIR, f"{base}_{i}.{ext}")
        i += 1
    with open(path, "wb") as f:
        f.write(image_bytes)
    return path


def list_bills() -> list[str]:
    """Все сохранённые чеки, от старых к новым."""
    if not os.path.isdir(BILLS_DIR):
        return []
    files = [
        os.path.join(BILLS_DIR, n)
        for n in os.listdir(BILLS_DIR)
        if os.path.isfile(os.path.join(BILLS_DIR, n))
    ]
    files.sort(key=os.path.getmtime)
    return files
