#!/bin/bash
# Загружает код бота на сервер (Oracle Cloud / любой Ubuntu) по SSH.
# Использование: ./deploy_push.sh <IP-сервера> <путь-к-ssh-ключу>
set -e

IP="$1"
KEY="$2"
if [ -z "$IP" ] || [ -z "$KEY" ]; then
    echo "Использование: ./deploy_push.sh <IP-сервера> <путь-к-ssh-ключу>"
    echo "Пример:        ./deploy_push.sh 123.45.67.89 ~/Downloads/ssh-key.key"
    exit 1
fi

DIR="$(cd "$(dirname "$0")" && pwd)"

rsync -avz -e "ssh -i $KEY" \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'bot.log' \
    --exclude 'expenses.xlsx' \
    --exclude 'expenses_backup_*.xlsx' \
    --exclude 'settings.json' \
    "$DIR/" "ubuntu@$IP:~/MoneyTracker/"

echo "✅ Код загружен в ~/MoneyTracker на сервере $IP."
echo "   Данные (expenses.xlsx, settings.json) не трогаются — они живут на сервере."
