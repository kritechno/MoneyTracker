# Деплой бота на Oracle Cloud (Always Free) — бесплатно навсегда

Цель: бот работает 24/7 в облаке, Мак больше не нужен.
Все команды ниже выполняются по очереди. Если что-то не получится — пришли вывод, разберёмся.

---

## Шаг 1. Создать бесплатный сервер

1. Зарегистрируйся: https://www.oracle.com/cloud/free/ → **Start for free**.
   - Понадобится email и банковская карта (только для проверки, списаний не будет — выбираешь **Always Free**).
2. В консоли Oracle: **Menu → Compute → Instances → Create Instance**.
3. Настройки:
   - **Image:** Canonical Ubuntu 22.04
   - **Shape:** любой с пометкой **Always Free-eligible**
     (например `VM.Standard.E2.1.Micro`, или ARM `VM.Standard.A1.Flex` 1 OCPU / 6 GB).
   - **SSH keys:** нажми **Save private key** — скачается файл `ssh-key-….key`. Сохрани его, он нужен для входа.
4. **Create**. Через минуту у инстанса появится **Public IP address** — запиши его.

---

## Шаг 2. Открыть подключение (один раз)

По умолчанию Oracle закрывает порты, но боту входящие порты не нужны (он сам ходит наружу).
Достаточно убедиться, что SSH (порт 22) открыт — он открыт по умолчанию.

На Маке дай права ключу:
```bash
chmod 600 ~/Downloads/ssh-key-XXXX.key   # подставь имя своего файла
```

Проверь вход (подставь свой IP и путь к ключу):
```bash
ssh -i ~/Downloads/ssh-key-XXXX.key ubuntu@ТВОЙ_IP
```
При первом входе ответь `yes`. Если попал в консоль сервера — отлично, набери `exit` и вернись на Мак.

---

## Шаг 3. Загрузить код бота на сервер

С Мака, из папки проекта:
```bash
cd /Users/a.buzubayev/Documents/CODING_projects/MoneyTracker
./deploy_push.sh ТВОЙ_IP ~/Downloads/ssh-key-XXXX.key
```
Код окажется в `~/MoneyTracker` на сервере. Файл `.env` (с токенами) тоже загрузится.

---

## Шаг 4. Установить и запустить на сервере

Зайди на сервер:
```bash
ssh -i ~/Downloads/ssh-key-XXXX.key ubuntu@ТВОЙ_IP
```

Дальше — команды **на сервере**:
```bash
# системные пакеты
sudo apt update && sudo apt install -y python3-venv python3-pip rsync

# окружение и зависимости
cd ~/MoneyTracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# проверь, что .env на месте и с ключами
cat .env    # должны быть TELEGRAM_BOT_TOKEN и GEMINI_API_KEY

# установить автозапуск (systemd)
sudo cp moneytracker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now moneytracker
```

Проверить, что работает:
```bash
systemctl status moneytracker        # должно быть active (running)
journalctl -u moneytracker -f        # живой лог; Ctrl+C чтобы выйти
```
В логе должно появиться `Application started` и `Меню команд зарегистрировано`.

---

## Шаг 5. Выключить бота на Маке (ВАЖНО!)

Один токен — только один бот. Пока на Маке тоже крутится бот, в облаке будет ошибка 409.
На Маке выполни:
```bash
launchctl unload ~/Library/LaunchAgents/com.moneytracker.bot.plist
```
Чтобы он не запускался снова при входе в систему, можно удалить автозапуск:
```bash
rm ~/Library/LaunchAgents/com.moneytracker.bot.plist
```

Готово — теперь бот живёт в облаке и работает с выключенным Маком. 🎉

---

## Перенести текущие траты на сервер (необязательно)

Если хочешь забрать накопленный `expenses.xlsx` с Мака в облако:
```bash
# на Маке
scp -i ~/Downloads/ssh-key-XXXX.key \
    /Users/a.buzubayev/Documents/CODING_projects/MoneyTracker/expenses.xlsx \
    ubuntu@ТВОЙ_IP:~/MoneyTracker/expenses.xlsx
# затем на сервере перезапусти бота:
sudo systemctl restart moneytracker
```

---

## Полезные команды на сервере

| Действие | Команда |
|---|---|
| Статус | `systemctl status moneytracker` |
| Логи | `journalctl -u moneytracker -f` |
| Перезапуск | `sudo systemctl restart moneytracker` |
| Остановить | `sudo systemctl stop moneytracker` |
| Обновить код | с Мака: `./deploy_push.sh IP KEY`, затем на сервере `sudo systemctl restart moneytracker` |

Получить файл таблицы в любой момент — просто напиши боту `/excel`.
