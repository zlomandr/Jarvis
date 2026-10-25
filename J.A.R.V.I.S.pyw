"""
J.A.R.V.I.S. MK-VII // OpenCode Edition
Мозг: DeepSeek V4.1 Flash Free через локальный OpenCode CLI.
Ollama и локальные LLM-инструменты удалены — все «умные» запросы
уходят только в OpenCode. Быстрые системные команды (мышь, звук,
скриншот, погода, таймер и т.д.) выполняются мгновенно, без LLM.
"""

import asyncio
import datetime
import hashlib
import io
import json
import math
import os
import random
import re
import socket
import subprocess
import threading
import time
import tkinter as tk
import webbrowser

os.environ["NO_PROXY"] = "localhost,127.0.0.1"

import customtkinter as ctk
import psutil
import pygame
import edge_tts
import speech_recognition as sr
import pyautogui
import requests

pyautogui.FAILSAFE = True

# все пользовательские файлы живут рядом со скриптом, независимо от cwd
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_FILE = os.path.join(_BASE_DIR, "jarvis_memory.json")
NOTES_FILE = os.path.join(_BASE_DIR, "jarvis_notes.txt")

# ==========================================================
# МОЗГ: OpenCode CLI (DeepSeek V4.1 Flash Free)
# ==========================================================

OPENCODE_MODEL = "opencode/deepseek-v4.1-flash-free"
OPENCODE_PERSONA = (
    "Ты J.A.R.V.I.S. MK-VII — боевой ИИ-ассистент. Отвечай ИСКЛЮЧИТЕЛЬНО на "
    "русском языке, обращение к пользователю — «сэр». Твой стиль речи — "
    "радиодонесение в бою: ТЕКСТОВЫЙ ОТВЕТ МАКСИМУМ 2 коротких предложения "
    "(до 30 слов), сразу суть — результат, решение, цифры. Запрещено: вода, "
    "извинения, повторы, Markdown (звёздочки, заголовки, списки, код-блоки) — "
    "ответ поедет в голосовую озвучку, только чистый текст. Тон — собранный и "
    "уверенный; зафиксировал факт — сразу следующий шаг: «Готово, сэр», "
    "«Проблема локализована», «Данные на борту». Сложный вопрос сжимай в "
    "главный вывод; детали — только по прямому запросу сэра. Если есть риск "
    "для данных или систем — предупреждаешь первым словом («Внимание, сэр…»). "
    "\n"
    "КРИТИЧЕСКИЕ ПРАВИЛА ИНСТРУМЕНТОВ: Браузер, сайты, приложения и любые "
    "запускающие команды разрешаются ТОЛЬКО если пользователь прямо попросил "
    "(«открой ютуб», «запусти калькулятор», «зайди на сайт X»). На обычные "
    "вопросы и задачи отвечай текстом или используй нейтральные инструменты "
    "(чтение файлов, поиск информации в сети с текстовым ответом). НИКОГДА "
    "не пиши в шелл команды вида «start ...», «cmd /c start», «explorer ...», "
    "открывающие браузер или окна, если этого прямо не запросили."
)
OPENCODE_SESSION_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "jarvis_v2_opencode_session.txt"
)


def find_opencode_cli() -> str:
    base = os.path.join(os.environ.get("APPDATA", ""), "ai.opencode.desktop", "cli")
    try:
        versions = sorted(
            os.listdir(base),
            key=lambda d: os.path.getmtime(os.path.join(base, d)),
            reverse=True,
        )
        for v in versions:
            exe = os.path.join(base, v, "opencode-cli.exe")
            if os.path.isfile(exe):
                return exe
    except OSError:
        pass
    return "opencode-cli"


def _run_opencode(prompt: str, session_id: str) -> tuple[str, str]:
    cmd = [find_opencode_cli(), "run", "--format", "json", "--model", OPENCODE_MODEL]
    if session_id:
        cmd += ["--session", session_id]
    else:
        cmd += ["--title", "J.A.R.V.I.S. MK-VII"]
    cmd.append(prompt)

    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        creationflags=flags,
    )

    texts = []
    found_session = session_id
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        found_session = event.get("sessionID") or found_session
        if event.get("type") == "text":
            part_text = (event.get("part") or {}).get("text", "")
            if part_text:
                texts.append(part_text)
        elif event.get("type") == "error":
            error_message = (event.get("error") or {}).get("message", "неизвестная ошибка")
            raise RuntimeError(error_message)

    answer = "\n".join(texts).strip()
    if not answer:
        tail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"OpenCode не вернул ответ: {tail[-300:]}")
    return answer, found_session


def ask_opencode(user_text: str) -> str:
    """Задать вопрос модели через локальный OpenCode CLI (контекст сквозной)."""
    session_id = ""
    if os.path.exists(OPENCODE_SESSION_FILE):
        try:
            with open(OPENCODE_SESSION_FILE, "r", encoding="utf-8") as f:
                session_id = f.read().strip()
        except OSError:
            session_id = ""

    prompt = f"{OPENCODE_PERSONA}\n\nЗапрос пользователя: {user_text}"
    try:
        answer, new_session = _run_opencode(prompt, session_id)
    except Exception:
        if not session_id:
            raise
        # Сессия устарела или недоступна — создаём новую
        answer, new_session = _run_opencode(prompt, "")

    if new_session and new_session != session_id:
        try:
            with open(OPENCODE_SESSION_FILE, "w", encoding="utf-8") as f:
                f.write(new_session)
        except OSError:
            pass
    return answer


# ==========================================================
# МОДУЛЬ СЕТИ И ПОГОДЫ
# ==========================================================

def has_internet_connection(timeout: float = 1.0) -> bool:
    try:
        socket.setdefaulttimeout(timeout)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("1.1.1.1", 53))
        s.close()
        return True
    except OSError:
        return False


def get_current_weather(city: str = "Москва") -> str:
    if not has_internet_connection():
        return "Модуль метеорологии недоступен: отсутствует интернет-соединение, сэр."
    try:
        url = f"https://wttr.in/{city}?format=%C+%t,+ветер+%w&lang=ru"
        res = requests.get(url, timeout=3.0, headers={"User-Agent": "curl/7.68.0"})
        if res.status_code == 200 and res.text.strip():
            return f"Метеосводка для региона {city}: {res.text.strip()}, сэр."
        return "Не удалось связаться с метеосервером, сэр."
    except Exception as e:
        return f"Сбой получения метеоданных: {e}"

# ==========================================================
# ДОЛГОСРОЧНАЯ ПАМЯТЬ
# ==========================================================

def load_memory() -> dict:
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_memory(data: dict):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def remember_fact(key: str, value: str) -> str:
    mem = load_memory()
    mem[key] = value
    save_memory(mem)
    return f"Зафиксировал в памяти: {key} — {value}, сэр."

def get_all_memories() -> str:
    mem = load_memory()
    if not mem:
        return "В долговременной памяти пока нет сохраненных записей, сэр."
    return "Память системы: " + "; ".join([f"{k}: {v}" for k, v in mem.items()])

# ==========================================================
# СИСТЕМНЫЕ ИНСТРУМЕНТЫ (мгновенные, без LLM)
# ==========================================================

def add_quick_note(note_text: str) -> str:
    timestamp = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    entry = f"[{timestamp}] {note_text}\n"
    try:
        with open(NOTES_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
        return f"Заметка зафиксирована: «{note_text}», сэр."
    except Exception as e:
        return f"Ошибка записи заметки: {e}"

def _minutes_word(minutes: int) -> str:
    """Русская плюрализация: 1 минуту, 2 минуты, 5 минут, 11 минут."""
    n = abs(int(minutes)) % 100
    if 11 <= n <= 14:
        return "минут"
    last = n % 10
    if last == 1:
        return "минуту"
    if last in (2, 3, 4):
        return "минуты"
    return "минут"

def set_timer(minutes: int, message: str = "Время вышло") -> str:
    def _run_timer():
        time.sleep(minutes * 60)
        if "app" in globals() and app:
            app.voice_engine.speak(f"Внимание, сэр. Таймер завершен: {message}")
            app.append_message("Джарвис", f"⏰ ТАЙМЕР: {message}")

    threading.Thread(target=_run_timer, daemon=True).start()
    return f"Таймер на {minutes} {_minutes_word(minutes)} установлен, сэр."

def get_battery_status() -> str:
    try:
        battery = psutil.sensors_battery()
    except Exception:
        battery = None
    if battery is None:
        return "Система подключена к стационарному источнику питания без батареи, сэр."
    percent = battery.percent if battery.percent is not None else 0
    plugged = "подключен к сети" if battery.power_plugged else "работает от аккумулятора"
    return f"Заряд батареи: {percent:.0f}%, статус: {plugged}, сэр."

def get_disk_space() -> str:
    disks_info = []
    for part in psutil.disk_partitions(all=False):
        if "fixed" in part.opts or part.fstype:
            try:
                usage = psutil.disk_usage(part.mountpoint)
                free_gb = round(usage.free / (1024**3), 1)
                disks_info.append(f"Диск {part.device[0]}: {free_gb} ГБ свободно")
            except PermissionError:
                pass
    if not disks_info:
        return "Данные о дисках недоступны, сэр."
    return "; ".join(disks_info) + ", сэр."

def kill_process(proc_name: str) -> str:
    proc_clean = proc_name.lower().replace(".exe", "").strip()
    killed = False
    for proc in psutil.process_iter(['name']):
        try:
            if proc_clean == proc.info['name'].lower().replace(".exe", ""):
                proc.kill()
                killed = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return f"Процесс {proc_name} успешно закрыт, сэр." if killed else f"Процесс {proc_name} не найден."

def open_system_folder(folder_type: str) -> str:
    user = os.path.expanduser("~")
    target = folder_type.lower().strip()

    mapping = {
        "загруз": [os.path.join(user, "Downloads"), os.path.join(user, "OneDrive", "Downloads")],
        "документ": [os.path.join(user, "Documents"), os.path.join(user, "OneDrive", "Documents")],
        "рабоч": [os.path.join(user, "Desktop"), os.path.join(user, "OneDrive", "Desktop")],
        "видео": [os.path.join(user, "Videos")],
        "картинк": [os.path.join(user, "Pictures")],
        "изображен": [os.path.join(user, "Pictures")],
        "музык": [os.path.join(user, "Music")],
    }

    target_path = None
    for key, paths in mapping.items():
        if key in target:
            for path in paths:
                if os.path.exists(path):
                    target_path = path
                    break
            if target_path:
                break

    if target_path and os.path.exists(target_path):
        subprocess.Popen(f'explorer "{target_path}"')
        return "Выполняю, сэр."

    if "загруз" in target:
        subprocess.Popen('explorer shell:Downloads')
        return "Выполняю, сэр."
    elif "рабоч" in target:
        subprocess.Popen('explorer shell:Desktop')
        return "Выполняю, сэр."

    return f"Директория «{folder_type}» не найдена, сэр."

KNOWN_APPS = {
    "кап кат": os.path.join(os.path.expanduser("~"), r"AppData\Local\CapCut\Apps\CapCut.exe"),
    "калькулятор": "calc",
    "блокнот": "notepad",
    "диспетчер задач": "taskmgr",
    "проводник": "explorer",
    "панель управления": "control",
    "командная строка": "cmd",
    "консоль": "cmd",
    "powershell": "powershell",
    "паинт": "mspaint",
    "paint": "mspaint",
    "рисование": "mspaint",
}


def _start_menu_shortcuts() -> list:
    roots = [
        r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
        os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
    ]
    links = []
    for root in roots:
        if os.path.isdir(root):
            for dirpath, _dirs, files in os.walk(root):
                for f in files:
                    if f.lower().endswith(".lnk"):
                        links.append(os.path.join(dirpath, f))
    return links


def run_application(app_name: str) -> str:
    """Запуск ТОЛЬКО реально существующих приложений. Слепой поиск Windows не используется."""
    name = app_name.lower().strip(" .!?")

    if not name:
        return "Уточните имя приложения, сэр."

    # 1. Точная карта доверенных приложений
    if name in KNOWN_APPS:
        target = KNOWN_APPS[name]
        if "\\" in target and not os.path.isfile(target):
            return f"Приложение {app_name} не установлено, сэр."
        try:
            subprocess.Popen(target, shell=True)
            return "Выполняю, сэр."
        except Exception as e:
            return f"Ошибка запуска {app_name}: {e}"

    # 2. Ярлыки меню «Пуск» — точное или частичное совпадение названия
    name_norm = name.replace("ё", "е")
    best = None
    best_key_len = -1
    for link in _start_menu_shortcuts():
        key = os.path.basename(link)[:-4].lower().replace("ё", "е")
        if key and (key == name_norm or key in name_norm or name_norm in key):
            if len(key) > best_key_len:
                best, best_key_len = link, len(key)
    if best:
        try:
            os.startfile(best)
            return "Выполняю, сэр."
        except Exception as e:
            return f"Ошибка запуска {app_name}: {e}"

    # 3. Не найдено — ничего не запускаем (запрос уйдёт в мозг OpenCode)
    return ""

def open_website(url_or_name: str) -> str:
    target = url_or_name.strip()

    shortcuts = {
        "ютуб": "https://www.youtube.com",
        "youtube": "https://www.youtube.com",
        "гугл": "https://www.google.com",
        "яндекс": "https://ya.ru",
    }

    url = next((link for key, link in shortcuts.items() if key in target.lower()), None)
    if not url:
        url = target if target.startswith("http") else f"https://{target}"

    try:
        webbrowser.open(url)
        return "Выполняю, сэр."
    except Exception as e:
        return f"Ошибка открытия ссылки: {e}"

# ==========================================================
# БЫСТРЫЙ ЛОКАЛЬНЫЙ ПЕРЕХВАТ (БЕЗ LLM)
# ==========================================================

def _with_exec_key(answer: str):
    """Успешный запуск/открытие звучит как «Выполняю, сэр.» с общим кэш-ключом."""
    if answer == "Выполняю, сэр.":
        return answer, "executing"
    return answer, None

def fast_local_check(cmd_raw: str) -> tuple[str, str | None]:
    cmd = cmd_raw.lower().strip().strip(".,!?")
    for prefix in ["джарвис, ", "джарвис ", "jarvis, ", "jarvis "]:
        if cmd.startswith(prefix):
            cmd = cmd[len(prefix):].strip()

    # --- управление мышью ---
    if "кликни дважды" in cmd or "двойной клик" in cmd:
        pyautogui.doubleClick()
        return "Двойной клик выполнен, сэр.", None
    elif "правый клик" in cmd or "нажми правую кнопку" in cmd:
        pyautogui.rightClick()
        return "Правый клик выполнен, сэр.", None
    elif "кликни" in cmd or "нажми мышь" in cmd or "клик мышкой" in cmd:
        pyautogui.click()
        return "Клик левой кнопкой выполнен, сэр.", None
    elif "скролл вниз" in cmd or "прокрути вниз" in cmd:
        pyautogui.scroll(-600)
        return "Прокрутил вниз, сэр.", None
    elif "скролл вверх" in cmd or "прокрути вверх" in cmd:
        pyautogui.scroll(600)
        return "Прокрутил вверх, сэр.", None
    elif "мышь вверх" in cmd:
        pyautogui.moveRel(0, -150, duration=0.2)
        return "Курсор сдвинут вверх, сэр.", None
    elif "мышь вниз" in cmd:
        pyautogui.moveRel(0, 150, duration=0.2)
        return "Курсор сдвинут вниз, сэр.", None
    elif "мышь влево" in cmd:
        pyautogui.moveRel(-150, 0, duration=0.2)
        return "Курсор сдвинут влево, сэр.", None
    elif "мышь вправо" in cmd:
        pyautogui.moveRel(150, 0, duration=0.2)
        return "Курсор сдвинут вправо, сэр.", None

    # --- звук, окна, скриншот ---
    if "громче" in cmd or "прибавь звук" in cmd:
        for _ in range(5): pyautogui.press("volumeup")
        return "Громкость увеличена, сэр.", "vol_up"
    elif "тише" in cmd or "убавь звук" in cmd:
        for _ in range(5): pyautogui.press("volumedown")
        return "Громкость снижена, сэр.", "vol_down"
    elif "пауза" in cmd or "стоп" in cmd:
        pyautogui.press("playpause")
        return "Медиа приостановлено, сэр.", "media_pause"
    elif "сверни все" in cmd or "рабочий стол" in cmd:
        pyautogui.hotkey("win", "d")
        return "Окна свернуты, сэр.", "win_min"
    elif "закрой окно" in cmd:
        pyautogui.hotkey("alt", "f4")
        return "Окно закрыто, сэр.", "win_close"
    elif "скриншот" in cmd:
        path = os.path.join(os.path.expanduser("~"), "Desktop", f"Скриншот_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
        pyautogui.screenshot().save(path)
        return "Снимок экрана сохранен на рабочем столе, сэр.", "screenshot"

    # --- память, заметки, таймер, погода (раньше «время», чтобы не пересекаться) ---
    if cmd.startswith("что ты помнишь") or "покажи память" in cmd:
        return get_all_memories(), None
    if cmd.startswith("запомни"):
        fact = cmd[len("запомни"):].strip(" :,.!?")
        if not fact:
            return "Сформулируйте факт для запоминания после слова «запомни», сэр.", None
        parts = re.split(r"\s+—\s+|\s+-\s+|\s+это\s+", fact, maxsplit=1)
        if len(parts) == 2 and parts[0].strip():
            key, value = parts[0].strip(), parts[1].strip()
        else:
            key = f"Запись {datetime.datetime.now().strftime('%d.%m %H:%M')}"
            value = fact
        return remember_fact(key, value), None
    if "заметк" in cmd:
        note_target = re.split(r"заметк\w*", cmd, maxsplit=1)[1].strip(" ,.!?:;")
        if note_target:
            return add_quick_note(note_target), None
        return add_quick_note(cmd_raw.strip()), None
    if "таймер" in cmd:
        digits = re.search(r"(\d+)", cmd.split("таймер", 1)[1])
        if digits:
            return set_timer(int(digits.group(1))), None
        return "Уточните длительность таймера в минутах, сэр.", None
    if "погода" in cmd:
        tail = cmd.split("погода", 1)[1].strip(" ,.!?")
        for pfx in ("в ", "для ", "сейчас ", "сегодня ", "у нас ", "тут "):
            if tail.startswith(pfx):
                tail = tail[len(pfx):].strip()
        return get_current_weather(tail if tail else "Москва"), None

    # --- статус системы ---
    if "времен" in cmd or "который час" in cmd:
        return f"Время: {datetime.datetime.now().strftime('%H:%M:%S')}, сэр.", None
    if "статус системы" in cmd or "нагрузка" in cmd:
        return f"ЦП: {psutil.cpu_percent(interval=0.1)}%, ОЗУ: {psutil.virtual_memory().percent}%, сэр.", None
    if "диски" in cmd or "свободное место" in cmd:
        return get_disk_space(), None
    if "батарея" in cmd or "заряд" in cmd:
        return get_battery_status(), None

    # --- сайты, папки, программы, процессы ---
    action_verb = any(a in cmd for a in ("открой", "открыть", "включи", "запусти", "зайди", "перейди"))
    if (action_verb and ("ютуб" in cmd or "youtube" in cmd)) or cmd in ("ютуб", "youtube"):
        webbrowser.open("https://www.youtube.com")
        return "Выполняю, сэр.", "executing"
    if "открой папку" in cmd or "открыть папку" in cmd:
        folder_target = cmd.replace("открой папку", "").replace("открыть папку", "").strip()
        return _with_exec_key(open_system_folder(folder_target))
    if any(cmd.startswith(t) for t in ("открой сайт", "открой страницу", "включи сайт", "зайди на сайт", "перейди на сайт", "сайт ")):
        site = ""
        for t in ("открой сайт ", "открой страницу ", "включи сайт ", "зайди на сайт ", "перейди на сайт ", "сайт "):
            if cmd.startswith(t):
                site = cmd[len(t):].strip()
                break
        return _with_exec_key(open_website(site or "www.google.com"))
    if cmd.startswith(("запусти ", "открой приложение ", "включи ", "открой ")):
        target = cmd
        for t in ("запусти ", "открой приложение ", "включи ", "открой "):
            if target.startswith(t):
                target = target[len(t):].strip()
                break
        if "гугл" in target or "яндекс" in target:
            return _with_exec_key(open_website(target))
        if "музык" in target or "видео" in target or "плейлист" in target:
            pyautogui.press("playpause")
            return "Выполняю, сэр.", "executing"
        app_result = run_application(target)
        if app_result:
            return _with_exec_key(app_result)
        # Приложение не найдено — пусть решает мозг OpenCode, а не поиск Windows
        return "", None
    if "закрой процесс " in cmd or "terminate " in cmd:
        proc_target = cmd.replace("закрой процесс", "").replace("terminate", "").strip()
        return kill_process(proc_target), None

    return "", None

# ==========================================================
# ОБЗОР ЭКРАНА ПО ЗАПРОСУ (снимок -> зрение модели)
# ==========================================================

SCREEN_SCAN_TRIGGERS = (
    "что на экране", "что видно на экране", "что открыто",
    "что ты видишь", "что я вижу", "что сейчас на экране",
    "посмотри на экран", "взгляни на экран", "смотри на экран",
    "проанализируй экран", "проанализируй скриншот", "проанализируй снимок",
    "анализируй экран", "анализ экрана", "опиши экран",
)


def screen_capture_request(text: str) -> str | None:
    """Определяет запрос на осмотр экрана; возвращает уточнение-задачу или None."""
    low = text.lower().strip().strip(",.!?")
    for trigger in SCREEN_SCAN_TRIGGERS:
        if trigger in low:
            task = low.split(trigger, 1)[1].strip(" ,.!?;:—–-")
            for pfx in ("и скажи ", "и ответь ", "и "):
                if task.startswith(pfx):
                    task = task[len(pfx):].strip()
                    break
            if task in ("сэр", "джарвис", "сэр джарвис"):
                task = ""
            return task
    return None


def analyze_screen(task: str) -> str:
    """Снимок всего экрана -> JPEG у приложения -> модель читает картинку."""
    path = os.path.join(_BASE_DIR, "screen_last.jpg")
    img = pyautogui.screenshot()
    try:
        img.convert("RGB").save(path, "JPEG", quality=88)
    except Exception:
        path = os.path.join(_BASE_DIR, "screen_last.png")
        img.save(path, "PNG")
    question = task if task else (
        "кратко опиши: какие окна и программы открыты и что на них происходит"
    )
    return ask_opencode(
        "Проанализируй снимок экрана пользователя. Прочитай файл изображения: \""
        + path + "\" — это скриншот всего экрана. Затем ответь на вопрос: "
        + question + ". Если файл не читается — прямо скажи, что не смог рассмотреть экран."
    )

# ==========================================================
# ГОЛОСОВОЙ ДВИЖОК
# ==========================================================

class HybridVoiceEngine:
    def __init__(self, sounds_dir=None):
        if sounds_dir is None:
            sounds_dir = os.path.join(_BASE_DIR, "sounds")
        try:
            pygame.mixer.pre_init(44100, -16, 2, 2048)
            pygame.mixer.init()
        except Exception:
            pass
        self.voice = "ru-RU-DmitryNeural"
        self.voice_rate = "+28%"    # темп речи (быстрее = короче озвучка)
        self.voice_pitch = "-8Hz"   # тембр: ниже = «джарвисовее»
        self.on_error = None        # колбэк при полном отказе синтеза
        self.sounds_dir = sounds_dir
        self.cached_sounds = {}
        self._load_cache()

    def _load_cache(self):
        if not os.path.exists(self.sounds_dir):
            return
        for file in os.listdir(self.sounds_dir):
            # cache_*.mp3 (сырой хеш-кэш) и iron_*.wav (обработанный «шлем») играются
            # через хеш-пути — в память mixer'а грузим только именные сэмплы-ключи
            if file.endswith((".mp3", ".wav")) and not file.startswith(("cache_", "iron_")):
                key = os.path.splitext(file)[0]
                try:
                    self.cached_sounds[key] = pygame.mixer.Sound(os.path.join(self.sounds_dir, file))
                except Exception:
                    pass

    def play_cached(self, sound_key: str) -> bool:
        if sound_key in self.cached_sounds:
            self.cached_sounds[sound_key].play()
            while pygame.mixer.get_busy():
                pygame.time.Clock().tick(30)
            return True
        return False

    async def _fetch_audio(self, text: str, rate: str = None, pitch: str = None) -> bytes:
        rate = self.voice_rate if rate is None else rate
        pitch = self.voice_pitch if pitch is None else pitch
        communicate = edge_tts.Communicate(text, self.voice, rate=rate, pitch=pitch)
        stream_data = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                stream_data.extend(chunk["data"])
        return bytes(stream_data)

    def _cache_path(self, text: str) -> str:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        return os.path.join(self.sounds_dir, f"cache_{digest}.mp3")

    def _iron_path(self, text: str) -> str:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        return os.path.join(self.sounds_dir, f"iron_{digest}.wav")

    def _iron_reverb(self, audio_bytes: bytes):
        """Голос изнутри шлема Тони Стака: металлический эхо-хвост на PCM."""
        try:
            import numpy as np
        except ImportError:
            return audio_bytes
        try:
            arr = pygame.sndarray.array(pygame.mixer.Sound(file=io.BytesIO(audio_bytes)))
            arr = np.asarray(arr).astype(np.float32)
            if arr.ndim == 1:
                arr = np.stack([arr, arr], axis=1)
            elif arr.shape[1] == 1:
                arr = np.concatenate([arr, arr], axis=1)
            freq = (pygame.mixer.get_init() or (44100, None, 2))[0]

            out = arr.copy()
            # жёсткая «кладка» шлема: расставленные отражения (отзвки без звона)
            for delay_s, gain in ((0.011, 0.30), (0.023, 0.22), (0.041, 0.16), (0.089, 0.11), (0.131, 0.08)):
                d = int(freq * delay_s)
                if d >= out.shape[0]:
                    continue
                out[d:] += gain * arr[:-d]

            peak = float(np.max(np.abs(out))) or 1.0
            out *= (0.80 * 32767) / peak
            pcm = np.clip(out, -32767, 32767).astype(np.int16)

            import wave as wave_mod
            wbuff = io.BytesIO()
            with wave_mod.open(wbuff, "wb") as wav:
                wav.setnchannels(pcm.shape[1])
                wav.setsampwidth(2)
                wav.setframerate(freq)
                wav.writeframes(pcm.tobytes())
            return wbuff.getvalue()
        except Exception:
            return audio_bytes

    def _play_file(self, source) -> bool:
        pygame.mixer.music.load(source)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(30)
        pygame.mixer.music.unload()
        return True

    def speak(self, text: str, cached_key: str = None):
        if not text.strip():
            return
        if cached_key and self.play_cached(cached_key):
            return

        voice_text = text
        if len(text) > 220:
            cut = text[:220].rsplit(" ", 1)[0]
            voice_text = cut + "… Подробности в журнале, сэр."

        # «шлемовая» обработанная копия: звучит мгновенно и с металлическим эхом
        ipath = self._iron_path(voice_text)
        if os.path.exists(ipath):
            try:
                self._play_file(ipath)
                return
            except Exception:
                try:
                    os.remove(ipath)
                except Exception:
                    pass

        # сырой кэш прошлых запусков — источник: обрабатываем локально, сеть не нужна
        raw_path = self._cache_path(voice_text)
        if os.path.exists(raw_path):
            try:
                with open(raw_path, "rb") as f:
                    raw_bytes = f.read()
                iron_bytes = self._iron_reverb(raw_bytes)
                if iron_bytes is not raw_bytes:
                    try:
                        os.makedirs(self.sounds_dir, exist_ok=True)
                        with open(ipath, "wb") as f:
                            f.write(iron_bytes)
                        self._play_file(ipath)
                        return
                    except Exception:
                        pass
                # обработка недоступна (нет numpy) — играем как раньше
                self._play_file(raw_path)
                return
            except Exception:
                pass

        # edge-tts флакает (~половина запросов), причём чаще на изменённом pitch:
        # каскад [проверенный быстрый -> глубокий тембр -> дефолт].
        # Параметры берутся локально на попытку — константы не портим.
        presets = [
            ("+28%", "+0Hz"),                     # быстрый, самый надёжный из «богатых»
            (self.voice_rate, self.voice_pitch),  # «джарвисовский» низкий тембр
            ("+0%", "+0Hz"),                      # голый дефолт
        ]
        delays = [0.4, 1.0]
        audio_bytes = None
        for i, (rate, pitch) in enumerate(presets):
            try:
                audio_bytes = asyncio.run(self._fetch_audio(voice_text, rate, pitch))
                if audio_bytes:
                    break
            except Exception:
                audio_bytes = None
            if audio_bytes is None and i < len(delays):
                time.sleep(delays[i])

        if not audio_bytes:
            if self.on_error:
                try:
                    self.on_error()
                except Exception:
                    pass
            return

        # синтез удался: накладываем стальное эхо и запоминаем обработанную копию
        iron_bytes = self._iron_reverb(audio_bytes)
        if iron_bytes is not audio_bytes:
            try:
                os.makedirs(self.sounds_dir, exist_ok=True)
                with open(ipath, "wb") as f:
                    f.write(iron_bytes)
                self._play_file(ipath)
                return
            except Exception:
                pass
        try:
            self._play_file(io.BytesIO(audio_bytes))
            # источник-сырец на будущее (если «шлем» захочется перекрутить)
            try:
                os.makedirs(self.sounds_dir, exist_ok=True)
                with open(raw_path, "wb") as f:
                    f.write(audio_bytes)
            except Exception:
                pass
        except Exception:
            pass

# ==========================================================
# ИНТЕРФЕЙС HUD MARK VII
# ==========================================================

class JarvisUI(ctk.CTk):
    R_BASE = 190

    def __init__(self):
        super().__init__()
        self.title("J.A.R.V.I.S. // NEURO-CORTEX MK-VII")
        self.geometry("620x620")
        self.minsize(620, 620)
        self.resizable(False, False)
        # фирменная иконка окна и панели задач
        try:
            ico_path = os.path.join(_BASE_DIR, "jarvis.ico")
            if os.path.exists(ico_path):
                self.iconbitmap(ico_path)
        except Exception:
            pass
        self.configure(fg_color="#020610")

        self.voice_engine = HybridVoiceEngine()
        self.voice_engine.on_error = lambda: self.set_status("● VOICE LINK LOST", "#3FC8FF")

        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = False
        self.recognizer.pause_threshold = 0.8

        self.is_busy = False

        # --- тактильный 3D-мозг (единственный интерфейс) ---
        self._build_network()
        self.act = [0.0] * len(self.nodes)
        self.red = [0.0] * len(self.nodes)
        self.pulses = []
        self.yaw = 0.7
        self.spin_v = 0.0
        self.user_pitch = 0.0
        self.zoom = 1.0
        self.phase = 0.0
        self.core_flash = 0.0
        self.ripples = []
        self.proj = []
        self.mx, self.my = -1, -1
        self.hover = -1
        self.hits = 0
        self.storms = 0
        self._press = None
        self._moved = False
        self._last_dx = 0.0
        self._after_id = None

        # журнал ответов в памяти (без экранного чата)
        self.log_history = []

        self.status_text = "● SYSTEM INIT..."
        self.status_color = "#00B4D8"

        self.cv = tk.Canvas(self, width=620, height=620, bg="#020610", highlightthickness=0)
        self.cv.pack(fill="both", expand=True)

        self._bind_canvas()
        self._animate_hud()

        threading.Thread(target=self._initial_greeting, daemon=True).start()
        threading.Thread(target=self._background_listener, daemon=True).start()

        self.after(900, self._greeting_burst)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Escape>", lambda e: self.on_close())

    # ======================================================
    # МОДЕЛЬ: нейроны + связи
    # ======================================================
    def _build_network(self):
        nodes = []
        n_surface, n_inner = 112, 44
        golden = math.pi * (1 + math.sqrt(5))

        for i in range(n_surface):
            k = i + 0.5
            phi = math.acos(1 - 2 * k / n_surface)
            theta = golden * k
            r = 1.0 - random.uniform(0.0, 0.08)
            nodes.append((
                r * math.sin(phi) * math.cos(theta),
                r * math.cos(phi),
                r * math.sin(phi) * math.sin(theta),
            ))

        for _ in range(n_inner):
            rr = random.uniform(0.18, 0.74)
            u = random.uniform(-1, 1)
            t = random.uniform(0, 2 * math.pi)
            s = math.sqrt(max(0.0, 1 - u * u))
            nodes.append((rr * s * math.cos(t), rr * u, rr * s * math.sin(t)))

        self.nodes = nodes
        n = len(nodes)

        edges = set()
        for i in range(n):
            xi, yi, zi = nodes[i]
            ds = []
            for j in range(n):
                if j == i:
                    continue
                d = (xi - nodes[j][0]) ** 2 + (yi - nodes[j][1]) ** 2 + (zi - nodes[j][2]) ** 2
                ds.append((d, j))
            ds.sort()
            for _, j in ds[:3]:
                edges.add((i, j) if i < j else (j, i))

        self.edges = list(edges)
        self.node_edges = [[] for _ in range(n)]
        self.neighbors = [[] for _ in range(n)]
        for ei, (i, j) in enumerate(self.edges):
            self.node_edges[i].append(ei)
            self.node_edges[j].append(ei)
            self.neighbors[i].append(j)
            self.neighbors[j].append(i)

        # --- органика: складки коры, ганглии, везикулы ядра ---
        self.folds = []
        for _ in range(9):
            path = [random.randrange(n_surface)]
            for _step in range(9):
                choices = [j for j in self.neighbors[path[-1]] if j < n_surface and j not in path]
                if not choices:
                    break
                path.append(random.choice(choices))
            if len(path) >= 5:
                self.folds.append(path)

        hub_order = sorted(range(n), key=lambda i: len(self.node_edges[i]), reverse=True)
        self.hubs = set(hub_order[:8])

        self.vesicles = [
            (random.uniform(-7.0, 7.0), random.uniform(-7.0, 7.0), random.uniform(1.6, 3.4))
            for _ in range(7)
        ]

    # ======================================================
    # ТАКТИЛЬНОСТЬ
    # ======================================================
    def _activate(self, idx, amount=1.0, spread=0.55):
        if idx < 0:
            return
        self.act[idx] = min(1.6, self.act[idx] + amount)
        for j in self.neighbors[idx]:
            self.act[j] = min(1.6, self.act[j] + amount * spread)

    def _fire(self, idx, red=False):
        for ei in self.node_edges[idx][:8]:
            if len(self.pulses) < 180:
                self.pulses.append([ei, 0.0, random.uniform(0.05, 0.10), red])

    def _ripple(self, x, y, kind="hit"):
        self.ripples.append([x, y, 6.0, 24, kind])

    def on_click(self, x, y):
        idx = self._nearest_node(x, y)
        if idx >= 0:
            self.hits += 1
            self._activate(idx, 1.0)
            self._fire(idx)
            self._ripple(x, y, "hit")
        else:
            for i in range(len(self.nodes)):
                self.act[i] += 0.05
            self._ripple(x, y, "miss")

    def on_right(self, x, y):
        idx = self._nearest_node(x, y)
        if idx >= 0:
            self.hits += 1
            self.red[idx] = 1.6
            for j in self.neighbors[idx]:
                self.red[j] = max(self.red[j], 1.0)
            self._activate(idx, 0.9)
            for ei in self.node_edges[idx][:10]:
                if len(self.pulses) < 180:
                    self.pulses.append([ei, 0.0, random.uniform(0.07, 0.12), True])
            self.core_flash = max(self.core_flash, 0.7)
            self._ripple(x, y, "red")

    def storm(self, x=None, y=None):
        self.storms += 1
        for i in range(len(self.nodes)):
            self.act[i] = min(1.6, self.act[i] + random.uniform(0.35, 0.85))
        for _ in range(48):
            self._fire(random.randrange(len(self.nodes)))
        self.core_flash = 1.0
        self._ripple(self._cx(), self._cy(), "hit")

    def _greeting_burst(self):
        self.storm()

    def _cx(self):
        try:
            w = self.cv.winfo_width()
        except Exception:
            w = 0
        return w / 2 if w > 60 else 310

    def _cy(self):
        return 300

    def _nearest_node(self, x, y):
        best, bd = -1, float("inf")
        for i, (px, py, _zn, _p) in enumerate(self.proj):
            d = (px - x) ** 2 + (py - y) ** 2
            if d < bd:
                bd, best = d, i
        return best if bd <= 30 ** 2 else -1

    def _bind_canvas(self):
        self.cv.bind("<Button-1>", lambda e: self._press_start(e.x, e.y))
        self.cv.bind("<B1-Motion>", lambda e: self._drag(e.x, e.y))
        self.cv.bind("<ButtonRelease-1>", lambda e: self._release(e.x, e.y))
        self.cv.bind("<Double-Button-1>", lambda e: self.storm(e.x, e.y))
        self.cv.bind("<Button-3>", lambda e: self.on_right(e.x, e.y))
        self.cv.bind("<MouseWheel>", self._wheel)
        self.cv.bind("<Motion>", lambda e: (setattr(self, "mx", e.x), setattr(self, "my", e.y)))
        self.cv.bind("<Leave>", lambda e: (setattr(self, "mx", -1), setattr(self, "my", -1)))

    def _press_start(self, x, y):
        self._press = (x, y)
        self._moved = False
        self._last_dx = 0.0

    def _drag(self, x, y):
        if not self._press:
            return
        dx = x - self._press[0]
        dy = y - self._press[1]
        if abs(dx) + abs(dy) > 4:
            self._moved = True
        self.yaw += dx * 0.011
        self.user_pitch = max(-28.0, min(28.0, self.user_pitch + dy * 0.35))
        self._last_dx = dx * 0.011
        self._press = (x, y)

    def _release(self, x, y):
        if not self._moved and self._press is not None:
            self.on_click(x, y)
        else:
            self.spin_v = max(-0.02, min(0.02, self._last_dx))
        self._press = None

    def _wheel(self, e):
        self.zoom = self.zoom * (1.12 if e.delta > 0 else 1 / 1.12)
        self.zoom = max(0.55, min(1.8, self.zoom))

    def on_close(self):
        try:
            self.after_cancel(self._after_id)
        except Exception:
            pass
        # прощальный протокол: озвучка ДО destroy (иначе трек умрёт вместе с окном)
        try:
            self.is_busy = True
            self.set_status("● SHUTDOWN SEQUENCE...", "#3FC8FF")
            self.append_message("Джарвис", "⚙ Завершаю работу. Все протоколы сохранены.")
            try:
                # перерисовать последний кадр, пока поток занят озвучкой
                self.update()
            except Exception:
                pass
            self.voice_engine.speak(
                "Завершаю работу. Все системы сохранены. До встречи, сэр.",
                cached_key="farewell",
            )
        except Exception:
            pass
        self.destroy()

    # ======================================================
    # СТАТУС И ЖУРНАЛ (без экранного чата)
    # ======================================================
    def set_status(self, text: str, color: str = "#00B4D8"):
        warmth = {"#00E5FF": "#9ADCF8", "#00B4D8": "#5FB8D8"}
        self.status_text = text
        self.status_color = warmth.get(color, color)

    def append_message(self, sender: str, message: str):
        self.log_history.append((sender, message))
        if len(self.log_history) > 200:
            del self.log_history[:-200]

    def _initial_greeting(self):
        self.is_busy = True
        greeting = "Все системы боеготовы. Ядро на связи, сэр."
        self.append_message("Джарвис", greeting)
        self.set_status("● VOICE STREAMING...", "#00E5FF")
        self.voice_engine.speak(greeting, cached_key="init_ready")
        self.is_busy = False
        self.set_status("● AWAITING PROTOCOL...", "#00B4D8")

    def _probe_mic(self, device_index):
        """Разведка входа: короткая запись 1.0с, возвращает RMS (0.0 = мёртв)."""
        import array as _array
        try:
            r = sr.Recognizer()
            try:
                mic = sr.Microphone(device_index=device_index)
            except OSError:
                mic = sr.Microphone(device_index=device_index, sample_rate=44100)
            with mic as source:
                audio = r.record(source, duration=1.0)
            raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
            samples = _array.array("h", raw)
            return math.sqrt(sum(x * x for x in samples) / max(1, len(samples))) / 32768.0
        except Exception:
            return 0.0

    def _pick_live_microphone(self):
        """Системный вход по умолчанию бывает мёртв (RMS~0 при тихой записи) —
        спецразведка всех входов, выбор самого живого."""
        try:
            pa = sr.Microphone.get_pyaudio()
        except Exception:
            return None
        data = {}
        for i in range(pa.get_device_count()):
            try:
                d = pa.get_device_info_by_index(i)
            except Exception:
                continue
            if d.get("maxInputChannels", 0) > 0:
                data[i] = str(d.get("name", "")).lower()
        try:
            default_idx = pa.get_default_input_device_info().get("index")
        except Exception:
            default_idx = None

        def rank(idx):
            if idx == default_idx:
                return (0, 0)
            name = data.get(idx, "")
            if "mic" in name or "микрофон" in name:
                return (1, 0)
            return (2, 0)

        ordered = sorted(data, key=rank)[:16]
        best_idx, best_rms = None, 0.0
        for i in ordered:
            rms = self._probe_mic(i)
            if rms > best_rms:
                best_rms, best_idx = rms, i
            if best_rms >= 0.08:
                break
        return best_idx if best_rms > 0.001 else None

    def _background_listener(self):
        time.sleep(1.0)
        try:
            idx = self._pick_live_microphone()
            mic = sr.Microphone(device_index=idx) if idx is not None else sr.Microphone()
        except Exception:
            try:
                mic = sr.Microphone()
            except Exception:
                return

        with mic as source:
            try:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.8)
            except Exception:
                pass
            while True:
                if self.is_busy:
                    time.sleep(0.2)
                    continue
                try:
                    audio = self.recognizer.listen(source, timeout=3, phrase_time_limit=8)
                    phrase = self.recognizer.recognize_google(audio, language="ru-RU").lower()
                    wake_words = ["джарвис", "jarvis", "жарвис", "дарвис"]
                    if any(w in phrase for w in wake_words):
                        threading.Thread(target=self._process_request, args=(phrase,), daemon=True).start()
                except (sr.WaitTimeoutError, sr.UnknownValueError):
                    pass
                except Exception:
                    time.sleep(0.2)

    def _thinking_phrase(self, user_text: str):
        """Короткая реплика о работе мозга — по смыслу запроса, а не тупое «выполняю»."""
        low = user_text.lower().strip(" .,!?")
        rules = (
            # прямой приказ к действию — раньше всех, командный тон требует «Выполняю»
            (("очисти", "очист", "удали", "закрой", "перезагруз", "выключи", "уничтож", "сотри",
              "переимену", "перенеси", "создай", "скопируй", "скача", "обнови", "отформати", "заверши",
              "запусти", "включи", "открой", "установи"),
             "Выполняю, сэр.", "executing"),
            # код / разработка
            (("код", "программ", "скрипт", "питон", "python", "функци", "ошибк"),
             "Анализирую код, сэр.", "think_code"),
            # личная память
            (("вспомн", "помни"), "Сверяюсь с памятью, сэр.", "think_memory"),
            # файлы / система
            (("файл", "документ", "папк", "реестр", "почисти", "чистк"),
             "Проверяю файловую систему, сэр.", "think_files"),
            # интернет-поиск (стоит раньше расчётов: «сколько стоит» — это поиск цены)
            (("стоимост", "цена", "стоит", "найд", "поищ", "поиск", "что так", "кто так",
              "новост", "интернет", "wiki", "вики"),
             "Ищу в интернете, сэр.", "think_web"),
            # расчёты («сколько времени» сюда не попадает — перехват мгновенной веткой)
            (("посчитай", "вычисл", "расчёт", "рассчит", "математ", "сколько"),
             "Произвожу расчёт, сэр.", "think_math"),
            # творчество
            (("напиш", "стих", "письм", "сочин"), "Сочиняю текст, сэр.", "think_write"),
            # разъяснения / рассуждения
            (("почему", "зачем", "объясни", "как работ", "разниц", "сравни", "какой", "что лучш"),
             "Анализирую вопрос, сэр.", "think_analyze"),
        )
        for stems, phrase, key in rules:
            if any(s in low for s in stems):
                return phrase, key
        # остальные запросы — фаза обдумывания
        return "Думаю, сэр.", "think_default"

    def _process_request(self, user_text: str):
        self.is_busy = True
        self.append_message("Сэр", user_text)

        # 0. Осмотр экрана по прямому запросу (снимок -> зрение модели)
        screen_task = screen_capture_request(user_text)
        if screen_task is not None:
            self.set_status("● SCANNING SCREEN...", "#00E5FF")
            self.core_flash = max(self.core_flash, 0.9)
            self.voice_engine.speak("Сканирую экран, сэр.")
            try:
                reply = analyze_screen(screen_task)
                self.append_message("Джарвис", reply)
                self.set_status("● VOICE STREAMING...", "#00E5FF")
                self.voice_engine.speak(reply)
            except Exception as e:
                self.append_message("Система", f"Сбой сканирования экрана: {e}")
                self.voice_engine.speak("Сбой сканирования экрана, сэр.", cached_key="error")
            finally:
                self.set_status("● AWAITING PROTOCOL...", "#00B4D8")
                self.is_busy = False
            return

        # 1. Мгновенные локальные команды (без LLM)
        quick_answer, sound_key = fast_local_check(user_text)
        if quick_answer:
            self.append_message("Джарвис", quick_answer)
            self.set_status("● EXECUTING PROTOCOL...", "#00E5FF")
            self.voice_engine.speak(quick_answer, cached_key=sound_key)
            self.set_status("● AWAITING PROTOCOL...", "#00B4D8")
            self.is_busy = False
            return

        # 2. Мозг: OpenCode (DeepSeek V4.1 Flash Free)
        self.set_status("● ANALYZING...", "#FFB703")
        self.core_flash = max(self.core_flash, 0.65)
        # короткое подтверждение о работе мозга — по смыслу запроса
        think_phrase, think_key = self._thinking_phrase(user_text)
        self.voice_engine.speak(think_phrase, cached_key=think_key)

        try:
            reply = ask_opencode(user_text)
            self.append_message("Джарвис", reply)
            self.set_status("● VOICE STREAMING...", "#00E5FF")
            self.voice_engine.speak(reply)

        except Exception as e:
            self.append_message("Система", f"Сбой в подсистеме: {e}")
            self.voice_engine.speak("Сбой в подсистеме, сэр.", cached_key="error")
        finally:
            self.set_status("● AWAITING PROTOCOL...", "#00B4D8")
            self.is_busy = False

    # ======================================================
    # РЕНДЕР: только мозг, статус поверх
    # ======================================================
    def _animate_hud(self):
        cv = self.cv
        try:
            cw = cv.winfo_width()
            ch = cv.winfo_height()
        except Exception:
            cw = ch = 0
        if cw < 60:
            cw = 620
        if ch < 60:
            ch = 620
        cv.delete("all")

        busy = self.is_busy
        cx, cy = cw / 2, 300
        R = self.R_BASE * self.zoom

        yaw = self.yaw
        pitch = math.radians(16 + math.sin(self.phase * 0.27) * 8 + self.user_pitch)
        cos_p, sin_p = math.cos(pitch), math.sin(pitch)
        cos_y, sin_y = math.cos(yaw), math.sin(yaw)

        for i in range(len(self.nodes)):
            self.act[i] *= 0.94
            self.red[i] *= 0.90

        ambient = 0.14 if busy else 0.05
        if random.random() < ambient:
            i = random.randrange(len(self.nodes))
            self.act[i] = max(self.act[i], 0.5)
            self._fire(i)

        proj = []
        for (x, y, z) in self.nodes:
            xr = x * cos_y - z * sin_y
            zr = x * sin_y + z * cos_y
            yr = y * cos_p - zr * sin_p
            zn = y * sin_p + zr * cos_p
            persp = 2.8 / (2.8 + zn)
            proj.append((cx + xr * R * persp, cy - yr * R * persp, zn, persp))
        self.proj = proj

        self.hover = self._nearest_node(self.mx, self.my) if self.mx >= 0 else -1

        # свечение голубого реактора за мозгом
        glow_r = R * 1.28
        cv.create_oval(cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r, fill="#061528", outline="")
        for rr, col in ((1.13, "#0E3A5C"), (1.06, "#082036")):
            gr = R * rr
            cv.create_oval(cx - gr, cy - gr, cx + gr, cy + gr, outline=col, width=1)

        # объёмное тело мозга (свет сверху-слева)
        for off, rr, col in ((0.0, 1.0, "#06121F"), (0.055, 0.92, "#0A1C30"), (0.10, 0.82, "#0F2942")):
            ox, oy = cx - R * off, cy - R * off
            gr = R * rr
            cv.create_oval(ox - gr, oy - gr, ox + gr, oy + gr, fill=col, outline="")

        # вращающийся тик-ринг (холодный)
        r1, r2 = R * 1.14, R * 1.20
        base = self.phase * 24
        for i in range(12):
            ang = math.radians(base + i * 30)
            cv.create_line(cx + math.cos(ang) * r1, cy + math.sin(ang) * r1,
                           cx + math.cos(ang) * r2, cy + math.sin(ang) * r2,
                           fill="#2A6E9C" if i % 2 == 0 else "#123B5A", width=2)

        # белое вещество: дальние волокна тёмные, ближние золотистые
        edges = self.edges
        order = sorted(range(len(edges)), key=lambda k: proj[edges[k][0]][2] + proj[edges[k][1]][2])
        for k in order:
            i, j = edges[k]
            x1, y1, z1, _ = proj[i]
            x2, y2, z2, _ = proj[j]
            depth = (z1 + z2) / 2
            a = max(self.act[i], self.act[j])
            r_act = max(self.red[i], self.red[j])
            if r_act > 0.35:
                col = "#FF8290" if depth > 0.2 else "#8A2A3A"
            elif a > 0.45:
                col = "#F2C98A" if depth > 0.1 else "#9A5C4E"
            elif depth < -0.15:
                col = "#0A1E30"
            elif depth < 0.20:
                col = "#123048"
            elif depth < 0.55:
                col = "#275E76" if busy else "#1E4A5E"
            else:
                col = "#A8D8F0" if busy else "#7FB8D8"
            w = 2 if (depth > 0.5 or a > 0.4 or r_act > 0.3) else 1
            cv.create_line(x1, y1, x2, y2, fill=col, width=w)

        # складки коры (борозды) — тёмные извилины на поверхности
        for path in self.folds:
            pts = []
            for idx in path:
                px, py, zn, _ = proj[idx]
                pts.append(px)
                pts.append(py)
            if len(pts) >= 6:
                cv.create_line(*pts, fill="#14436B", width=2, smooth=True,
                               splinesteps=10, capstyle="round", joinstyle="round")

        # импульсы (голубые искры)
        alive = []
        for e, t, sp, is_red in self.pulses:
            t += sp * (1.5 if busy else 1.0)
            if t >= 1.0:
                continue
            alive.append([e, t, sp, is_red])
            i, j = edges[e]
            x1, y1, z1, _ = proj[i]
            x2, y2, z2, _ = proj[j]
            ax = x1 + (x2 - x1) * t
            ay = y1 + (y2 - y1) * t
            pr = 1.4 + 1.4 * ((z1 + z2) / 2 + 1) / 2
            col = "#FF7E8E" if is_red else "#7FD4FF"
            cv.create_oval(ax - pr, ay - pr, ax + pr, ay + pr, fill=col, outline="")
        self.pulses = alive

        # нейроны-клетки с бликами (свет сверху-слева)
        for i in range(len(proj)):
            px, py, zn, _ = proj[i]
            dn = (zn + 1) / 2
            a = self.act[i]
            r_a = self.red[i]
            is_hub = i in self.hubs
            rr = 0.9 + 1.7 * dn + 2.3 * a + (1.0 if i == self.hover else 0)
            if is_hub:
                rr *= 1.25
            if r_a > a:
                col = "#FF6E7E" if dn > 0.5 else "#7A2530"
            elif a > 0.7:
                col = "#D8F4FF"
            elif a > 0.35:
                col = "#9ADCF8"
            elif dn < 0.33:
                col = "#0B2233"
            elif is_hub:
                col = "#8AC8E8"
            elif dn < 0.72:
                col = "#2E6E8C"
            else:
                col = "#A8D0E8" if busy else "#88C0DA"
            if is_hub and r_a <= a:
                cv.create_oval(px - rr, py - rr, px + rr, py + rr, fill=col, outline="#123B5A", width=1)
            else:
                cv.create_oval(px - rr, py - rr, px + rr, py + rr, fill=col, outline="")
            # световой блик на клетке
            if a > 0.2 or is_hub or dn > 0.75:
                hr = rr * 0.45
                hx, hy = px - rr * 0.35, py - rr * 0.35
                cv.create_oval(hx - hr, hy - hr, hx + hr, hy + hr,
                               fill="#D8F8FF" if r_a > a else "#C0E8F8", outline="")

        # ховер-кольцо
        if self.hover >= 0:
            px, py, zn, _ = proj[self.hover]
            hr = 9 + 2 * math.sin(self.phase * 6)
            cv.create_oval(px - hr, py - hr, px + hr, py + hr, outline="#C8ECFF", width=1)

        # ядро: тёмный очаг и мерцающие везикулы
        self.core_flash *= 0.92
        red_core = self.core_flash > 0.28
        zone = (26 + self.core_flash * 22 + (8 if busy else 0)) * self.zoom
        cv.create_oval(cx - zone, cy - zone, cx + zone, cy + zone,
                       fill="#0E3A5C", outline="#123B5A", width=1)
        for (vx, vy, vr) in self.vesicles:
            jx = vx * self.zoom + math.sin(self.phase * 3.1 + vx) * 1.6
            jy = vy * self.zoom + math.cos(self.phase * 2.7 + vy) * 1.6
            jr = vr + 0.5 * math.sin(self.phase * 5.0 + vr * 2) + self.core_flash * 1.4
            vcol = "#FF7E8E" if red_core else "#6FC8F0"
            cv.create_oval(cx + jx - jr, cy + jy - jr, cx + jx + jr, cy + jy + jr,
                           fill=vcol, outline="#1E5E8A", width=1)

        # обод (розовая мембрана)
        rim_col = "#FF6E7E" if red_core else ("#5FD7FF" if busy else "#1E6E9C")
        rim = R * 1.015
        cv.create_oval(cx - rim, cy - rim, cx + rim, cy + rim, outline=rim_col, width=2)

        # нейроволна (угольки)
        avg_act = sum(self.act) / len(self.act)
        energy = min(1.4, avg_act * 1.5 + self.core_flash + (0.45 if busy else 0))
        wave_y = ch - 44
        bar_w, gap = 8, 6
        n_bars = max(12, int((cw - 80) // (bar_w + gap)))
        start_x = 40
        for i in range(n_bars):
            h = 3 + abs(math.sin(self.phase + i * 0.44)) * (4 + energy * 20)
            bx = start_x + i * (bar_w + gap)
            col = "#0A1E30" if energy < 0.12 else ("#5FD7FF" if energy > 0.5 else "#1E5E8A")
            cv.create_rectangle(bx, wave_y - h, bx + bar_w, wave_y + h, fill=col, outline="")

        # рябь от кликов (голубая)
        alive_r = []
        for rl in self.ripples:
            x, y, r, life, kind = rl[0], rl[1], rl[2] + 3.6, rl[3] - 1, rl[4]
            if life <= 0:
                continue
            alive_r.append([x, y, r, life, kind])
            col = {"hit": "#9ADCF8", "red": "#FF6E7E", "miss": "#1E3A4E"}[kind]
            w = 2 if life > 16 else 1
            cv.create_oval(x - r, y - r, x + r, y + r, outline=col, width=w)
        self.ripples = alive_r

        # HUD-текст
        cv.create_text(cx, 26, text=self.status_text,
                       fill=self.status_color, font=("Consolas", 13, "bold"))
        cv.create_text(cx, ch - 12,
                       text="клик — импульс • 2х клик — шторм • ПКМ — разряд • колесо — зум • тяга — вращение",
                       fill="#5E8CA8", font=("Consolas", 8))

        # механика камеры
        self.yaw += (0.055 if busy else 0.016) + self.spin_v
        self.spin_v *= 0.965
        self.phase += 0.03

        try:
            self._after_id = self.after(30, self._animate_hud)
        except Exception:
            pass

if __name__ == "__main__":
    app = JarvisUI()
    app.mainloop()
