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

VERSION = "2.1"
APP_NAME = "NEON"

CONFIG_VERSION = 3
MIN_PASSWORD_LENGTH = 8
PBKDF2_ITERATIONS = 240_000
MAX_LOGIN_ATTEMPTS = 3

# SHA-256 старого пароля по умолчанию. Сам пароль в коде не хранится:
# хеш нужен только чтобы запретить выбрать это значение снова.
LEGACY_DEFAULT_SHA256 = (
    "fb338271ce05e1ac9e5e01ed21247e6bd98a3cf60d64ca6e9118909bc38d6f51"
)

COMMON_PASSWORDS = {
    "password",
    "password1",
    "password123",
    "qwerty",
    "qwerty123",
    "12345678",
    "123456789",
    "1234567890",
    "admin",
    "admin123",
    "letmein",
    "welcome",
    "iloveyou",
}

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
        "version": CONFIG_VERSION,
        "custom_password": True,
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


def _has_custom_password(config: dict[str, Any]) -> bool:
    """Профиль создан или обновлён версией, где пароль выбирает пользователь."""
    return config.get("custom_password") is True


def _verify_against_config(password: str, config: dict[str, Any]) -> bool:
    try:
        salt = bytes.fromhex(str(config["salt"]))
        iterations = int(config.get("iterations", PBKDF2_ITERATIONS))
    except (ValueError, TypeError) as exc:
        raise NeonError("В настройках испорчены параметры пароля.") from exc
    expected = _hash_password(password, salt, iterations)
    return hmac.compare_digest(expected, str(config["hash"]))


def verify_password(password: str) -> bool:
    return _verify_against_config(password, _read_config())


def _is_legacy_default(password: str) -> bool:
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, LEGACY_DEFAULT_SHA256)


def password_problem(password: str, old_password: str | None = None) -> str | None:
    """Вернуть причину, по которой пароль не подходит, или None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов."
    if len(password) > 1024:
        return "Пароль слишком длинный."
    if password != password.strip():
        return "Пароль не должен начинаться или заканчиваться пробелом."
    if len(set(password)) < 4:
        return "Слишком простой пароль: нужно хотя бы 4 разных символа."
    if password.casefold() in COMMON_PASSWORDS:
        return "Этот пароль слишком распространён."
    if _is_legacy_default(password):
        return "Старый пароль по умолчанию больше не подходит — придумайте свой."
    if old_password is not None and password == old_password:
        return "Новый пароль совпадает с текущим."
    return None


def prompt_new_password(
    old_password: str | None = None,
    title: str | None = None,
    subtitle: str | None = None,
) -> str:
    """Запросить новый пароль с проверкой и подтверждением."""
    if title is not None:
        header(title, subtitle)
        print()
    info(
        f"Минимум {MIN_PASSWORD_LENGTH} символов. "
        "В настройках хранится только соль и хеш, не сам пароль."
    )
    print()
    while True:
        first = read_password("Придумайте пароль: ")
        problem = password_problem(first, old_password)
        if problem:
            warn(problem)
            continue
        second = read_password("Повторите пароль: ")
        if first != second:
            warn("Пароли не совпали.")
            continue
        return first


def _first_run_setup() -> None:
    """Создать профиль: пароль придумывает пользователь, дефолта нет."""
    global _authenticated, _current_password
    password = prompt_new_password(
        title="Первый запуск NEON",
        subtitle="Придумайте свой пароль — пароля по умолчанию нет",
    )
    try:
        write_json(CONFIG_FILE, _new_config(password))
    except OSError as exc:
        error(f"Не удалось сохранить настройки: {exc}")
        raise SystemExit(1) from exc
    _authenticated = True
    _current_password = password
    success(f"Профиль создан в {CONFIG_DIR}. Пароль установлен.")


def _force_password_change(
    old_password: str, config: dict[str, Any]
) -> str:
    """Заменить пароль из старой версии на выбранный пользователем."""
    new_password = prompt_new_password(
        old_password=old_password,
        title="Нужен новый пароль",
        subtitle="Старый пароль по умолчанию больше не используется",
    )

    neo = None
    vault_existed = False
    backup = None
    try:
        import NeoPass as neo  # ленивый импорт: NeoPass сам импортирует neoncore

        vault_existed = neo.vault_exists()
        if vault_existed:
            backup = neo.rotate_master_password(old_password, new_password)
    except Exception as exc:  # noqa: BLE001 — показываем причину и не меняем вход
        error(f"Не удалось перешифровать хранилище паролей: {exc}")
        warn("Пароль не изменён. Исправьте хранилище или обратитесь к резервной копии.")
        raise SystemExit(1) from exc

    fresh = _new_config(new_password)
    fresh["created"] = config.get("created") or fresh["created"]
    fresh["changed"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        write_json(CONFIG_FILE, fresh)
    except OSError as exc:
        if vault_existed and neo is not None:
            try:
                neo.rotate_master_password(new_password, old_password)
            except Exception:  # noqa: BLE001 — откат мог не сработать
                error("Не удалось вернуть хранилище к старому паролю.")
        error(f"Не удалось сохранить настройки: {exc}")
        warn("Пароль не изменён.")
        raise SystemExit(1) from exc

    if backup:
        info(f"Резервная копия хранилища: {backup}")
    success("Пароль изменён.")
    return new_password


def require_login() -> None:
    """Проверить пароль. В рамках одного процесса достаточно одного раза."""
    global _authenticated, _current_password
    if _authenticated:
        return

    ensure_dirs()
    if not CONFIG_FILE.exists():
        _first_run_setup()
        return

    print()
    header("Вход в NEON", "Один пароль — все программы")

    for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
        try:
            password = read_password("Пароль: ")
        except (EOFError, KeyboardInterrupt):
            print()
            error("Вход отменён.")
            raise SystemExit(1)
        try:
            config = _read_config()
            accepted = _verify_against_config(password, config)
        except NeonError as exc:
            error(str(exc))
            error(f"Проверьте файл {CONFIG_FILE}")
            raise SystemExit(1) from exc
        if accepted:
            if not _has_custom_password(config):
                if _is_legacy_default(password):
                    password = _force_password_change(password, config)
                else:
                    # Старый профиль, но пароль уже не дефолтный: просто
                    # помечаем его как пользовательский, не заставляя менять.
                    config["version"] = CONFIG_VERSION
                    config["custom_password"] = True
                    try:
                        write_json(CONFIG_FILE, config)
                    except OSError as exc:
                        warn(f"Не удалось обновить профиль: {exc}")
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
    created = config.get("created")
    config.update(_new_config(new_password))
    if created:
        config["created"] = created
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
