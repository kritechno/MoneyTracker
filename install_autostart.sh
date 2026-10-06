#!/bin/bash
# Устанавливает автозапуск бота через macOS LaunchAgent.
# Бот будет запускаться при входе в систему и автоматически перезапускаться.
set -e

PLIST="com.moneytracker.bot.plist"
SRC="$(cd "$(dirname "$0")" && pwd)/$PLIST"
DEST="$HOME/Library/LaunchAgents/$PLIST"

mkdir -p "$HOME/Library/LaunchAgents"
# Подставляем путь к проекту вместо плейсхолдера в шаблоне plist
sed "s|__PROJECT_DIR__|$(dirname "$SRC")|g" "$SRC" > "$DEST"

# Перезагружаем, если уже был установлен
launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"

echo "✅ Автозапуск установлен. Бот работает в фоне и стартует при входе в систему."
echo "   Логи: $(dirname "$SRC")/bot.log"
echo "   Остановить:  launchctl unload \"$DEST\""
echo "   Запустить:   launchctl load \"$DEST\""
