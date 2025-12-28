# Telegram Lead Bot (aiogram 3)

Два бота: основной принимает заявки, уведомительный получает структурированные данные. Используются inline + reply кнопки, FSM и кастомное SQLite-хранилище (без внешних сервисов).

## Структура
- `main.py` — точка входа.
- `app/config.py` — загрузка конфигов из `.env`.
- `app/handlers/main.py` — сценарий заявки (FSM) для основного бота.
- `app/handlers/notification.py` — команды уведомительного бота (`/all` с пагинацией).
- `app/keyboards/main.py` — inline клавиатуры.
- `app/notifications.py` — отправка данных во второй бот + история лидов (SQLite).
- `app/storage/sqlite.py` — SQLite FSM storage (aiosqlite).
- `app/payments/yookassa.py` — заготовка клиента YooKassa (stub, можно подставить ключи).
- `app/utils/net.py`, `app/utils/ssh.py` — утилиты для IP/DNS и SSH-проверки.
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
   - `LEADS_DB_PATH` — путь к базе истории заявок (по умолчанию `data/leads.sqlite3`).
   - `YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY` — при наличии включат реальную оплату (сейчас работает stub).
   - `YOOKASSA_RETURN_URL` — URL возврата после оплаты (по умолчанию `https://t.me/`).
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
Основной бот (один раз на пользователя):
- `/start` → выбор сценария:
  - «Есть VPS и домен» → запрашиваем IP, проверяем SSH (root/22), спрашиваем домен и сверяем DNS → выбор протоколов (inline, мультивыбор) → опциональный BOT TOKEN → кнопка оплаты 999 ₽ (ЮKassa stub) → финальное сообщение и блокировка повторов.
  - «Нужен специалист» → имя → задача (inline) → опциональные детали (inline пропуск) → финальное сообщение и блокировка повторов.
- После завершения любого сценария дальнейшие сообщения получают автоответ «Ваш VPN уже в работе…».
- Все заявки пишутся в SQLite `LEADS_DB_PATH` и уходят в Notification BOT.

Уведомительный бот:
- `/all` — последние заявки (пагинация, inline кнопки «Назад/Вперёд», «В начало/В конец»).

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
