#!/usr/bin/env python3
"""NeoPass — хранилище паролей экосистемы NEON.

Хранилище шифруется общим паролем NEON (scrypt + HMAC-SHA256).
Всё сделано на стандартной библиотеке Python, без внешних зависимостей.
Файл: ~/.local/share/neon/vault.neon (или NEON_HOME/data/vault.neon).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import time

import neoncore as core

MAGIC = "NEONVAULT1"
VAULT_VERSION = 1
SCRYPT_N = 1 << 15
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LEN = 64  # 32 байта для шифрования + 32 байта для MAC
MAX_MEM = 64 * 1024 * 1024

LOWER = "abcdefghijkmnopqrstuvwxyz"
UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
DIGITS = "23456789"
SYMBOLS = "!@#$%^&*()-_=+"
ALPHABET = LOWER + UPPER + DIGITS + SYMBOLS


class VaultError(Exception):
    """Ошибка чтения или расшифровки хранилища."""


# --------------------------------------------------------------------------
# Криптография хранилища
# --------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def _derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_LEN,
        maxmem=MAX_MEM,
    )


def _keystream_xor(key: bytes, nonce: bytes, data: bytes) -> bytes:
    """Шифр на HMAC-SHA256 в режиме счётчика (по сути CTR)."""
    result = bytearray(data)
    encryption_key = key[:32]
    for block in range((len(result) + 31) // 32):
        stream = hmac.new(
            encryption_key,
            nonce + block.to_bytes(8, "big"),
            hashlib.sha256,
        ).digest()
        start = block * 32
        chunk = result[start : start + 32]
        result[start : start + 32] = bytes(a ^ b for a, b in zip(chunk, stream))
    return bytes(result)


def _tag(key: bytes, salt: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    mac = hmac.new(key[32:], digestmod=hashlib.sha256)
    mac.update(MAGIC.encode("ascii"))
    mac.update(bytes([VAULT_VERSION]))
    mac.update(SCRYPT_N.to_bytes(4, "big"))
    mac.update(SCRYPT_R.to_bytes(4, "big"))
    mac.update(SCRYPT_P.to_bytes(4, "big"))
    mac.update(salt)
    mac.update(nonce)
    mac.update(ciphertext)
    return mac.digest()


def _encrypt(plaintext: bytes, password: str) -> dict:
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(16)
    key = _derive_key(password, salt)
    ciphertext = _keystream_xor(key, nonce, plaintext)
    return {
        "magic": MAGIC,
        "version": VAULT_VERSION,
        "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P},
        "salt": _b64(salt),
        "nonce": _b64(nonce),
        "ciphertext": _b64(ciphertext),
        "mac": _b64(_tag(key, salt, nonce, ciphertext)),
    }


def _decrypt(blob: dict, password: str) -> bytes:
    try:
        if blob.get("magic") != MAGIC:
            raise ValueError("чужой формат")
        if int(blob.get("version", 0)) != VAULT_VERSION:
            raise ValueError("неподдерживаемая версия")
        salt = _unb64(blob["salt"])
        nonce = _unb64(blob["nonce"])
        ciphertext = _unb64(blob["ciphertext"])
        tag = _unb64(blob["mac"])
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise VaultError("Файл хранилища повреждён.") from exc

    if len(salt) != 16 or len(nonce) != 16 or len(tag) != 32:
        raise VaultError("Файл хранилища повреждён.")

    key = _derive_key(password, salt)
    expected = _tag(key, salt, nonce, ciphertext)
    if not hmac.compare_digest(expected, tag):
        raise VaultError("Неверный пароль или повреждённое хранилище.")
    return _keystream_xor(key, nonce, ciphertext)


# --------------------------------------------------------------------------
# Файл хранилища
# --------------------------------------------------------------------------

def vault_exists() -> bool:
    return core.VAULT_FILE.exists()


def _read_blob() -> dict:
    try:
        text = core.VAULT_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        raise VaultError(f"Не удалось прочитать хранилище: {exc}") from exc
    try:
        blob = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VaultError("Файл хранилища повреждён (это не JSON).") from exc
    if not isinstance(blob, dict):
        raise VaultError("Файл хранилища повреждён.")
    return blob


def load_vault(password: str) -> dict:
    if not vault_exists():
        return {"version": 1, "next_id": 1, "entries": []}

    blob = _read_blob()
    try:
        payload = _decrypt(blob, password)
    except VaultError:
        raise
    except Exception as exc:
        raise VaultError(f"Не удалось расшифровать хранилище: {exc}") from exc

    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VaultError("Внутри хранилища испорченные данные.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise VaultError("Внутри хранилища неожиданная структура.")
    if not isinstance(data.get("next_id"), int):
        data["next_id"] = 1 + max(
            (int(entry.get("id", 0)) for entry in data["entries"] if isinstance(entry, dict)),
            default=0,
        )
    data["entries"] = [
        entry
        for entry in data["entries"]
        if isinstance(entry, dict) and entry.get("service")
    ]
    return data


def save_vault(data: dict, password: str) -> None:
    core.ensure_dirs()
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    blob = _encrypt(payload, password)
    core.write_json(core.VAULT_FILE, blob)
    try:
        os.chmod(core.VAULT_FILE, 0o600)
    except OSError:
        pass


def rotate_master_password(old_password: str, new_password: str) -> str | None:
    """Перешифровать хранилище. Возвращает путь резервной копии."""
    if not vault_exists():
        return None

    data = load_vault(old_password)  # неверный пароль — VaultError
    backup = core.VAULT_FILE.with_name(
        f"vault.neon.bak-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    )
    try:
        shutil.copy2(core.VAULT_FILE, backup)
        os.chmod(backup, 0o600)
    except OSError:
        backup = None

    save_vault(data, new_password)
    return str(backup) if backup else None


# --------------------------------------------------------------------------
# Помощники
# --------------------------------------------------------------------------

def generate_password(length: int = 20) -> str:
    length = max(8, min(64, length))
    while True:
        chars = [
            secrets.choice(LOWER),
            secrets.choice(UPPER),
            secrets.choice(DIGITS),
            secrets.choice(SYMBOLS),
        ]
        chars += [secrets.choice(ALPHABET) for _ in range(length - 4)]
        secrets.SystemRandom().shuffle(chars)
        candidate = "".join(chars)
        # Без одинаковых символов подряд — так пароль легче читать.
        if all(candidate[i] != candidate[i + 1] for i in range(len(candidate) - 1)):
            return candidate


def _password() -> str:
    return core.current_password()


def _load_current() -> dict:
    try:
        return load_vault(_password())
    except VaultError as exc:
        raise core.NeonError(str(exc)) from exc


def _entries(data: dict) -> list[dict]:
    return sorted(
        data["entries"],
        key=lambda entry: str(entry.get("service", "")).casefold(),
    )


def render_entry(entry: dict) -> str:
    login = entry.get("login") or core.dim("без логина")
    note = entry.get("note") or ""
    suffix = f"  {core.dim(note[:48])}" if note else ""
    return f"{core.bold(entry.get('service', '?'))}  {core.dim('·')} {login}{suffix}"


def pick_entry(data: dict, prompt: str = "Номер записи") -> dict | None:
    entries = _entries(data)
    if not entries:
        core.warn("Записей пока нет.")
        core.pause()
        return None
    return core.choose(entries, render=render_entry, prompt=prompt)


def _copy_secret(value: str) -> None:
    if core.copy_to_clipboard(value):
        core.success("Скопировано в буфер обмена.")
    else:
        core.warn("Буфер обмена недоступен. Нужен wl-clipboard, xclip или xsel.")


# --------------------------------------------------------------------------
# Действия
# --------------------------------------------------------------------------

def list_entries() -> None:
    data = _load_current()
    entries = _entries(data)
    if not entries:
        core.info("Хранилище пока пустое. Добавьте первую запись: пункт 3.")
        core.pause()
        return

    core.header(
        "Пароли",
        f"{len(entries)} {core.plural(len(entries), 'запись', 'записи', 'записей')} · {core.VAULT_FILE}",
    )
    for index, entry in enumerate(entries, 1):
        print(f"  {index:>2}. {render_entry(entry)}")
    core.pause()


def show_entry() -> None:
    data = _load_current()
    entry = pick_entry(data, "Какую запись показать?")
    if entry is None:
        return

    core.header(entry.get("service", "?"), "Данные записи")
    print()
    print(f"  Логин:   {entry.get('login') or core.dim('—')}")
    password = str(entry.get("password", ""))
    print(f"  Пароль:  {'•' * min(len(password), 24)}  {core.dim(f'({len(password)} символов)')}")
    if entry.get("note"):
        print(f"  Заметка: {entry['note']}")
    if entry.get("updated"):
        print(f"  Изменено: {core.human_date(float(entry['updated']))}")
    print()

    while True:
        action = core.ask(
            "Показать (п) · скопировать (к) · Enter — назад",
            allow_empty=True,
        ).casefold()
        if action == "":
            return
        if action in ("п", "p", "показать", "show"):
            print(f"\n  {core.bold(password)}\n")
        elif action in ("к", "k", "c", "copy", "скопировать"):
            _copy_secret(password)
        else:
            core.warn("Выберите «п», «к» или Enter.")


def add_entry() -> None:
    created = not vault_exists()
    data = _load_current()
    service = core.ask("Сервис")
    login = core.ask("Логин", allow_empty=True)
    typed = core.read_password("Пароль (Enter — сгенерировать): ")

    if typed:
        if typed != core.read_password("Повторите пароль: "):
            core.warn("Пароли не совпали. Запись не создана.")
            core.pause()
            return
        password = typed
    else:
        password = generate_password()
        core.info(f"Сгенерирован пароль: {core.bold(password)}")
        if core.confirm("Скопировать его в буфер обмена?", default=True):
            _copy_secret(password)

    note = core.ask("Заметка", allow_empty=True)
    now = time.time()
    entry = {
        "id": data["next_id"],
        "service": service,
        "login": login,
        "password": password,
        "note": note,
        "created": now,
        "updated": now,
    }
    data["entries"].append(entry)
    data["next_id"] += 1
    save_vault(data, _password())
    core.success(f"Запись «{service}» сохранена.")
    if created:
        core.info("Хранилище создано и зашифровано общим паролем NEON.")
    core.pause()


def edit_entry() -> None:
    data = _load_current()
    entry = pick_entry(data, "Какую запись изменить?")
    if entry is None:
        return

    entry["service"] = core.ask("Сервис", default=str(entry.get("service", "")))
    if entry.get("login"):
        login = core.ask(
            "Логин (Enter — оставить, «-» — очистить)",
            default=str(entry["login"]),
        )
        entry["login"] = "" if login == "-" else login
    else:
        entry["login"] = core.ask("Логин (Enter — пропустить)", allow_empty=True)

    fresh = core.read_password("Новый пароль (Enter — оставить, «-» — новый): ")
    if fresh == "-":
        entry["password"] = generate_password()
        core.info(f"Новый пароль: {core.bold(entry['password'])}")
        if core.confirm("Скопировать его в буфер обмена?", default=True):
            _copy_secret(entry["password"])
    elif fresh:
        if fresh != core.read_password("Повторите новый пароль: "):
            core.warn("Пароли не совпали. Изменения не сохранены.")
            core.pause()
            return
        entry["password"] = fresh

    if entry.get("note"):
        note = core.ask(
            "Заметка (Enter — оставить, «-» — очистить)",
            default=str(entry["note"]),
        )
        entry["note"] = "" if note == "-" else note
    else:
        entry["note"] = core.ask("Заметка (Enter — пропустить)", allow_empty=True)
    entry["updated"] = time.time()
    save_vault(data, _password())
    core.success("Запись обновлена.")
    core.pause()


def delete_entry() -> None:
    data = _load_current()
    entry = pick_entry(data, "Какую запись удалить?")
    if entry is None:
        return
    if not core.confirm(f"Удалить запись «{entry.get('service')}»?"):
        core.info("Отменено.")
        core.pause()
        return

    data["entries"] = [
        item for item in data["entries"] if item.get("id") != entry.get("id")
    ]
    save_vault(data, _password())
    core.success("Запись удалена.")
    core.pause()


def search_entries() -> None:
    data = _load_current()
    query = core.ask("Что искать (сервис, логин, заметка)")
    needle = query.casefold()

    found = []
    for entry in _entries(data):
        haystack = " ".join(
            str(entry.get(field, "")) for field in ("service", "login", "note")
        ).casefold()
        if needle in haystack:
            found.append(entry)

    if not found:
        core.warn("Ничего не найдено.")
        core.pause()
        return

    core.header("Поиск", f"«{query}» — {len(found)}")
    for index, entry in enumerate(found, 1):
        print(f"  {index:>2}. {render_entry(entry)}")
    core.pause()


def generate_only() -> None:
    raw = core.ask("Длина пароля", default="20")
    try:
        length = int(raw)
    except ValueError:
        core.warn("Нужно число.")
        core.pause()
        return
    if not 8 <= length <= 64:
        core.warn("Длина должна быть от 8 до 64.")
        core.pause()
        return

    password = generate_password(length)
    core.header("Генератор паролей", f"{length} символов")
    print()
    print(f"  {core.bold(password)}")
    print()
    if core.confirm("Скопировать в буфер обмена?", default=True):
        _copy_secret(password)
    core.pause()


# --------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------

ACTIONS = {
    "1": list_entries,
    "2": show_entry,
    "3": add_entry,
    "4": edit_entry,
    "5": delete_entry,
    "6": search_entries,
    "7": generate_only,
}


def run() -> None:
    while True:
        choice = core.menu(
            "NeoPass — пароли",
            [
                ("1", "Список записей"),
                ("2", "Показать / скопировать запись"),
                ("3", "Добавить запись"),
                ("4", "Изменить запись"),
                ("5", "Удалить запись"),
                ("6", "Поиск"),
                ("7", "Сгенерировать пароль"),
            ],
            subtitle=str(core.VAULT_FILE),
            exit_label="Назад",
        )
        if choice is None:
            return
        try:
            ACTIONS[choice]()
        except core.NeonError as exc:
            core.warn(str(exc))
            core.pause()
        except VaultError as exc:
            core.warn(str(exc))
            core.pause()
        except OSError as exc:
            core.error(f"Ошибка файловой системы: {exc}")
            core.pause()
        except (KeyboardInterrupt, EOFError):
            print()
            return


def main() -> None:
    try:
        core.require_login()
        run()
    except (KeyboardInterrupt, EOFError):
        print()
        core.info("Выход из NeoPass.")
    except core.NeonError as exc:
        core.error(str(exc))
    except VaultError as exc:
        core.error(str(exc))
    except OSError as exc:
        core.error(f"Ошибка файловой системы: {exc}")


if __name__ == "__main__":
    main()
