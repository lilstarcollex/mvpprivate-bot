# Telegram Lead Bot (aiogram 3)

Два бота: основной принимает заявки, уведомительный получает структурированные данные. Используются inline + reply кнопки, FSM и кастомное SQLite-хранилище (без внешних сервисов).

## Структура
- `main.py` — точка входа.
- `app/config.py` — загрузка конфигов из `.env`.
- `app/handlers/main.py` — сценарий заявки (FSM).
- `app/keyboards/main.py` — inline + reply клавиатуры.
- `app/notifications.py` — отправка данных во второй бот.
- `app/storage/sqlite.py` — SQLite FSM storage (aiosqlite).
- `data/` — SQLite-файл состояний/данных.
- `requirements.txt` — зависимости.
- `.env.example` — пример переменных окружения.

## Быстрый старт (локально или на VPS Ubuntu)
1) Установить Python ≥ 3.11 (рекомендуется 3.12).  
2) Скопировать `.env.example` → `.env` и заполнить:
   - `MAIN_BOT_TOKEN` — токен основного бота.
   - `NOTIFICATION_BOT_TOKEN` — токен бота-уведомителя.
   - `NOTIFICATION_CHAT_ID` — chat_id, куда слать уведомления (личка/группа/канал, при необходимости с `-100`).
   - `STORAGE_PATH` — путь к SQLite (по умолчанию `data/state.sqlite3`).
3) Создать окружение и установить зависимости (Windows пример):
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\activate
   python -m pip install -r requirements.txt
   ```
   Linux/macOS:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   ```
4) Запуск:
   ```bash
   python main.py
   ```

## Сценарий
1. `/start` → вопрос «Как к вам обращаться?»  
2. Получаем имя → inline-кнопки: «Для бизнеса | Корпоративный», «Личное использование», «Свой вариант».  
3. При «Свой вариант» просим описать задачу.  
4. Просим доп. инфо с reply-кнопкой «Пропустить».  
5. Отправляем итоговое сообщение пользователю и пересылаем структурированные данные в Notification BOT.

## Systemd для VPS (пример)
`/etc/systemd/system/tg-lead-bot.service`:
```ini
[Unit]
Description=Telegram Lead Bot
After=network.target

[Service]
WorkingDirectory=/opt/tg-bot
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/tg-bot/.venv/bin/python /opt/tg-bot/main.py
Restart=always
RestartSec=5
User=www-data

[Install]
WantedBy=multi-user.target
```
Затем:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tg-lead-bot.service
sudo journalctl -u tg-lead-bot.service -f
```

## Заметки
- SQLite лежит в `STORAGE_PATH`, не требует отдельной установки.
- При смене токенов перезапустите сервис.
- Для группового уведомления добавьте уведомительного бота в чат и используйте его `chat_id`.
