#!/usr/bin/env python3
"""Общее ядро терминальной экосистемы NEON.

Модуль отвечает за:
  * пути к данным и настройкам;
  * оформление и удобный ввод в терминале;
  * вход по общему паролю;
  * атомарную запись файлов и JSON.

Программы экосистемы (Neonotes, NeonTodo, NeoPass) импортируют его
как ``neoncore``.
"""

from __future__ import annotations

import copy
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Sequence

VERSION = "2.0"
APP_NAME = "NEON"

# Старый пароль из первой версии Neonotes. При первом запуске он
# превращается в хеш и продолжает работать, пока его не сменят.
DEFAULT_PASSWORD = "Ilovepelmeni759"
PBKDF2_ITERATIONS = 240_000
MAX_LOGIN_ATTEMPTS = 3

RULE_WIDTH = 52
TERMINATOR = "."


# --------------------------------------------------------------------------
# Пути
# --------------------------------------------------------------------------

def _xdg_dir(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable)
    return Path(value).expanduser() if value else Path.home() / fallback


_custom_home = os.environ.get("NEON_HOME")
if _custom_home:
    # Удобно для переносимой установки и для тестов:
    # NEON_HOME=/path/to/folder python3 neon.py
    _root = Path(_custom_home).expanduser()
    DATA_DIR = _root / "data"
    CONFIG_DIR = _root / "config"
else:
    DATA_DIR = _xdg_dir("XDG_DATA_HOME", ".local/share") / "neon"
    CONFIG_DIR = _xdg_dir("XDG_CONFIG_HOME", ".config") / "neon"

NOTES_DIR = DATA_DIR / "notes"
TODO_FILE = DATA_DIR / "todos.json"
VAULT_FILE = DATA_DIR / "vault.neon"
CONFIG_FILE = CONFIG_DIR / "config.json"


# --------------------------------------------------------------------------
# Цвета и оформление
# --------------------------------------------------------------------------

def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty()


_COLOR = _color_enabled()


def _paint(text: Any, code: str) -> str:
    text = str(text)
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(text: Any) -> str:
    return _paint(text, "1")


def dim(text: Any) -> str:
    return _paint(text, "2")


def red(text: Any) -> str:
    return _paint(text, "31")


def green(text: Any) -> str:
    return _paint(text, "32")


def yellow(text: Any) -> str:
    return _paint(text, "33")


def blue(text: Any) -> str:
    return _paint(text, "34")


def magenta(text: Any) -> str:
    return _paint(text, "35")


def cyan(text: Any) -> str:
    return _paint(text, "36")


def neon(text: Any) -> str:
    return _paint(text, "1;96")


def info(message: Any) -> None:
    print(f"  {cyan('·')} {message}")


def success(message: Any) -> None:
    print(f"  {green('✓')} {message}")


def warn(message: Any) -> None:
    print(f"  {yellow('!')} {message}")


def error(message: Any) -> None:
    print(f"  {red('×')} {message}")


def header(title: str, subtitle: str | None = None) -> None:
    print()
    print(cyan("╭" + "─" * RULE_WIDTH + "╮"))
    print(cyan("│ ") + bold(title))
    if subtitle:
        print(cyan("│ ") + dim(subtitle))
    print(cyan("╰" + "─" * RULE_WIDTH + "╯"))


def rule(width: int = RULE_WIDTH) -> str:
    return cyan("─" * width)


def banner() -> str:
    art = [
        "███╗   ██╗███████╗ ██████╗ ███╗   ██╗",
        "████╗  ██║██╔════╝██╔═══██╗████╗  ██║",
        "██╔██╗ ██║█████╗  ██║   ██║██╔██╗ ██║",
        "██║╚██╗██║██╔══╝  ██║   ██║██║╚██╗██║",
        "██║ ╚████║███████╗╚██████╔╝██║ ╚████║",
        "╚═╝  ╚═══╝╚══════╝ ╚═════╝ ╚═╝  ╚═══╝",
    ]
    lines = [neon(line) for line in art]
    lines.append("")
    lines.append(dim("   заметки · задачи · пароли"))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Ввод
# --------------------------------------------------------------------------

def menu(
    title: str,
    options: Sequence[tuple[str, str]],
    subtitle: str | None = None,
    exit_label: str = "Выход",
) -> str | None:
    """Показать меню и вернуть выбранный ключ или None для выхода."""
    while True:
        header(title, subtitle)
        print()
        for key, label in options:
            print(f"  {cyan('[' + key + ']')} {label}")
        print(f"  {dim('[-]')} {exit_label}")
        print()
        try:
            choice = input(cyan("  > ")).strip()
        except EOFError:
            print()
            return None
        if choice == "":
            continue
        if choice in ("-", "0", "q", "Q", "й", "Й", "exit", "выход"):
            return None
        for key, _ in options:
            if choice == key:
                return key
        warn("Нет такого пункта.")


def ask(prompt: str, default: str | None = None, allow_empty: bool = False) -> str:
    hint = f" {dim('[' + default + ']')}" if default is not None else ""
    while True:
        try:
            raw = input(f"{prompt}{hint}: ").strip()
        except EOFError:
            print()
            raise
        if raw:
            return raw
        if default is not None:
            return default
        if allow_empty:
            return ""
        warn("Значение не может быть пустым.")


def confirm(prompt: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = ask(f"{prompt} {dim('[' + hint + ']')}", allow_empty=True).casefold()
    if not raw:
        return default
    return raw in ("y", "yes", "д", "да", "1", "+", "true")


def read_password(prompt: str = "Пароль: ") -> str:
    try:
        return getpass.getpass(dim(prompt))
    except (EOFError, KeyboardInterrupt):
        print()
        raise
    except Exception:
        # На случай терминалов, где getpass не работает.
        return input(dim(prompt))


def multiline(prompt: str = "Текст", terminator: str = TERMINATOR) -> str:
    info(f"{prompt}. Строка с одной точкой ({terminator}) завершает ввод.")
    lines: list[str] = []
    while True:
        try:
            line = input(dim("  │ "))
        except EOFError:
            print()
            break
        if line.strip() == terminator:
            break
        lines.append(line)
    return "\n".join(lines).strip("\n")


def pause(message: str = "Enter — продолжить") -> None:
    try:
        input(dim(f"  {message}..."))
    except EOFError:
        print()


def choose(
    items: Sequence[Any],
    render: Callable[[Any], str] = str,
    prompt: str = "Номер",
    allow_empty: bool = True,
) -> Any | None:
    items = list(items)
    if not items:
        warn("Список пуст.")
        return None
    print()
    for index, item in enumerate(items, 1):
        print(f"  {index:>2}. {render(item)}")
    print()
    while True:
        label = prompt + (" (Enter — отмена)" if allow_empty else "")
        raw = ask(label, allow_empty=allow_empty)
        if raw == "":
            return None
        try:
            number = int(raw)
        except ValueError:
            warn("Нужен номер из списка.")
            continue
        if 1 <= number <= len(items):
            return items[number - 1]
        warn(f"Номер от 1 до {len(items)}.")


# --------------------------------------------------------------------------
# Время, размеры, мелочи
# --------------------------------------------------------------------------

def human_date(timestamp: float) -> str:
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(timestamp))


def human_size(size: int | float) -> str:
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            if unit == "Б":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ"


def plural(number: int, one: str, few: str, many: str) -> str:
    remainder = abs(number) % 100
    if 11 <= remainder <= 19:
        return many
    remainder %= 10
    if remainder == 1:
        return one
    if 2 <= remainder <= 4:
        return few
    return many


def bar(done: int, total: int, width: int = 22) -> str:
    if total <= 0:
        return dim("░" * width)
    filled = round(width * done / total)
    return green("█" * filled) + dim("░" * (width - filled))


# --------------------------------------------------------------------------
# Файлы
# --------------------------------------------------------------------------

class NeonError(Exception):
    """Понятная пользователю ошибка."""


def ensure_dirs() -> None:
    for folder in (DATA_DIR, CONFIG_DIR, NOTES_DIR):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.chmod(folder, 0o700)
        except OSError as exc:
            raise NeonError(f"Не удалось создать папку {folder}: {exc}") from exc


def atomic_write_text(path: Path | str, text: str) -> None:
    """Записать файл целиком, не оставив половинчатого результата."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".neon-", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def read_json(path: Path | str, default: Any) -> Any:
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        broken = path.with_name(f"{path.name}.broken-{int(time.time())}")
        note = ""
        try:
            path.replace(broken)
            note = f", копия сохранена как {broken.name}"
        except OSError:
            pass
        warn(f"Не удалось прочитать {path.name}: {exc}{note}")
        return copy.deepcopy(default)


def write_json(path: Path | str, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def note_path(name: str) -> Path:
    """Безопасное имя заметки внутри NOTES_DIR."""
    cleaned = name.strip().replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"[^\w\s.-]+", "_", cleaned, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    if cleaned.lower().endswith(".txt"):
        cleaned = cleaned[:-4].strip(" ._")
    if not cleaned:
        raise NeonError("Пустое имя заметки.")
    return NOTES_DIR / f"{cleaned[:80].strip(' ._')}.txt"


def copy_to_clipboard(text: str) -> bool:
    candidates = (
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    )
    for command in candidates:
        if shutil.which(command[0]) is None:
            continue
        try:
            result = subprocess.run(
                command,
                input=text,
                text=True,
                timeout=3,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return True
    return False


# --------------------------------------------------------------------------
# Пароль и сеанс
# --------------------------------------------------------------------------

_authenticated = False
_current_password: str | None = None


def _hash_password(password: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    ).hex()


def _new_config(password: str) -> dict[str, Any]:
    salt = secrets.token_bytes(16)
    return {
        "version": 2,
        "salt": salt.hex(),
        "iterations": PBKDF2_ITERATIONS,
        "hash": _hash_password(password, salt, PBKDF2_ITERATIONS),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _read_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        raise NeonError("Файл настроек не найден. Запустите neon.py.")
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NeonError(f"Настройки повреждены: {exc}") from exc
    if not isinstance(data, dict) or "salt" not in data or "hash" not in data:
        raise NeonError(f"Файл настроек повреждён: {CONFIG_FILE}")
    return data


def verify_password(password: str) -> bool:
    config = _read_config()
    try:
        salt = bytes.fromhex(str(config["salt"]))
        iterations = int(config.get("iterations", PBKDF2_ITERATIONS))
    except (ValueError, TypeError) as exc:
        raise NeonError("В настройках испорчены параметры пароля.") from exc
    expected = _hash_password(password, salt, iterations)
    return hmac.compare_digest(expected, str(config["hash"]))


def require_login() -> None:
    """Проверить пароль. В рамках одного процесса достаточно одного раза."""
    global _authenticated, _current_password
    if _authenticated:
        return

    ensure_dirs()
    created = not CONFIG_FILE.exists()
    if created:
        write_json(CONFIG_FILE, _new_config(DEFAULT_PASSWORD))

    print()
    header("Вход в NEON", "Один пароль — все программы" if not created else None)
    if created:
        info("Первый запуск: профиль создан в " + str(CONFIG_DIR))
        info("Пароль по умолчанию — старый пароль Neonotes (см. README.md).")
        info("Сменить его можно в neon.py → Настройки.")
        print()

    for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
        try:
            password = read_password("Пароль: ")
        except (EOFError, KeyboardInterrupt):
            print()
            error("Вход отменён.")
            raise SystemExit(1)
        try:
            accepted = verify_password(password)
        except NeonError as exc:
            error(str(exc))
            error(f"Проверьте файл {CONFIG_FILE}")
            raise SystemExit(1) from exc
        if accepted:
            _authenticated = True
            _current_password = password
            success("Добро пожаловать!")
            return
        left = MAX_LOGIN_ATTEMPTS - attempt
        if left:
            warn(f"Неверный пароль. Осталось попыток: {left}")
    error("Слишком много неудачных попыток.")
    raise SystemExit(1)


def change_password(new_password: str) -> None:
    """Записать новый пароль в настройки (хеш, не сам пароль)."""
    global _current_password
    config = _read_config()
    config.update(_new_config(new_password))
    config["changed"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write_json(CONFIG_FILE, config)
    _current_password = new_password


def current_password() -> str:
    if not _authenticated or _current_password is None:
        raise NeonError("Сеанс не разблокирован. Запустите программу заново.")
    return _current_password


def is_authenticated() -> bool:
    return _authenticated


def lock() -> None:
    global _authenticated, _current_password
    _authenticated = False
    _current_password = None
