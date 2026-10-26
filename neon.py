#!/usr/bin/env python3
"""neon.py — единая точка входа в экосистему NEON.

Запуск:            python3 neon.py
Сразу программа:   python3 neon.py notes | todo | pass
Пути к данным:     python3 neon.py --where
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Чтобы import работал из любого каталога.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import neoncore as core
import Neonotes
import NeonTodo
import NeoPass

APPS = {
    "notes": ("Заметки", Neonotes.main),
    "todo": ("Задачи", NeonTodo.main),
    "pass": ("Пароли", NeoPass.main),
}


# --------------------------------------------------------------------------
# Настройки
# --------------------------------------------------------------------------

def show_paths() -> None:
    core.header("Где лежат данные")
    print()
    print(f"  {core.bold('Заметки')}:   {core.NOTES_DIR}")
    print(f"  {core.bold('Задачи')}:    {core.TODO_FILE}")
    print(f"  {core.bold('Пароли')}:    {core.VAULT_FILE}")
    print(f"  {core.bold('Настройки')}: {core.CONFIG_FILE}")
    print()
    core.info("Заметки и задачи — обычный текст. Хранилище паролей зашифровано.")
    core.pause()


def show_stats() -> None:
    notes = [path for path in core.NOTES_DIR.glob("*.txt") if path.is_file()]
    todo_data = core.read_json(core.TODO_FILE, {"tasks": []})
    tasks = todo_data.get("tasks", []) if isinstance(todo_data, dict) else []
    if not isinstance(tasks, list):
        tasks = []
    active = sum(1 for task in tasks if isinstance(task, dict) and not task.get("done"))
    done = sum(1 for task in tasks if isinstance(task, dict) and task.get("done"))

    if NeoPass.vault_exists():
        try:
            vault = NeoPass.load_vault(core.current_password())
            vault_info = f"{len(vault['entries'])} {core.plural(len(vault['entries']), 'запись', 'записи', 'записей')}"
        except (NeoPass.VaultError, core.NeonError) as exc:
            vault_info = core.red(f"ошибка: {exc}")
    else:
        vault_info = core.dim("ещё не создано")

    core.header("Статистика NEON")
    print()
    print(f"  Заметок:          {core.bold(len(notes))}")
    print(f"  Задач в работе:   {core.bold(active)}")
    print(f"  Задач выполнено:  {core.bold(done)}")
    print(f"  Паролей:          {vault_info}")
    print()
    core.pause()


def change_password() -> None:
    core.header("Смена общего пароля")
    print()
    old = core.read_password("Текущий пароль: ")
    try:
        valid = core.verify_password(old)
    except core.NeonError as exc:
        core.error(str(exc))
        core.pause()
        return
    if not valid:
        core.warn("Текущий пароль неверный.")
        core.pause()
        return

    new = core.prompt_new_password(old_password=old)

    # Сначала перешифровываем хранилище паролей старым паролем:
    # если старый ключ вдруг не подходит, общий пароль останется прежним.
    backup = None
    try:
        backup = NeoPass.rotate_master_password(old, new)
    except NeoPass.VaultError as exc:
        core.error(f"Не удалось перешифровать хранилище паролей: {exc}")
        core.warn("Общий пароль не изменён.")
        core.pause()
        return
    except OSError as exc:
        core.error(f"Не удалось сохранить хранилище: {exc}")
        core.warn("Общий пароль не изменён.")
        core.pause()
        return

    try:
        core.change_password(new)
    except OSError as exc:
        core.error(f"Не удалось сохранить настройки: {exc}")
        core.warn("Пробую вернуть хранилище к старому паролю...")
        try:
            NeoPass.rotate_master_password(new, old)
        except Exception:  # noqa: BLE001 — сообщаем и оставляем резервную копию
            core.error("Откат не удался. Резервная копия лежит рядом с vault.neon.")
        core.pause()
        return

    core.success("Пароль изменён.")
    if backup:
        core.info(f"Резервная копия хранилища: {backup}")
    core.pause()


def settings() -> None:
    while True:
        choice = core.menu(
            "Настройки",
            [
                ("1", "Сменить общий пароль"),
                ("2", "Показать пути к данным"),
                ("3", "Статистика"),
                ("4", "Заблокировать сеанс"),
            ],
            subtitle="Пароль хранится только в виде хеша",
            exit_label="Назад",
        )
        if choice is None:
            return
        if choice == "1":
            change_password()
        elif choice == "2":
            show_paths()
        elif choice == "3":
            show_stats()
        elif choice == "4":
            core.lock()
            core.info("Сеанс заблокирован.")
            return


# --------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------

def run() -> None:
    core.require_login()
    print(core.banner())

    while True:
        if not core.is_authenticated():
            core.require_login()

        choice = core.menu(
            "Экосистема NEON",
            [
                ("1", "Заметки — Neonotes"),
                ("2", "Задачи — NeonTodo"),
                ("3", "Пароли — NeoPass"),
                ("4", "Настройки"),
            ],
            subtitle="Один пароль — все программы",
            exit_label="Выход",
        )
        if choice is None:
            break

        try:
            if choice == "1":
                Neonotes.main()
            elif choice == "2":
                NeonTodo.main()
            elif choice == "3":
                NeoPass.main()
            elif choice == "4":
                settings()
        except (KeyboardInterrupt, EOFError):
            print()
            core.info("Возврат в меню.")
        except core.NeonError as exc:
            core.error(str(exc))
            core.pause()
        except OSError as exc:
            core.error(f"Ошибка файловой системы: {exc}")
            core.pause()

    print()
    print(core.dim("  До встречи."))
    print()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="neon",
        description="NEON — маленькая терминальная экосистема: заметки, задачи, пароли.",
    )
    parser.add_argument(
        "app",
        nargs="?",
        choices=sorted(APPS),
        help="запустить сразу: notes, todo или pass",
    )
    parser.add_argument(
        "--where",
        action="store_true",
        help="показать пути к данным и выйти",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"NEON {core.VERSION}",
    )
    args = parser.parse_args(argv)

    if args.where:
        print(f"Данные:    {core.DATA_DIR}")
        print(f"Настройки: {core.CONFIG_DIR}")
        return

    try:
        if args.app:
            core.require_login()
            APPS[args.app][1]()
        else:
            run()
    except (KeyboardInterrupt, EOFError):
        print()
        core.info("Выход.")
    except core.NeonError as exc:
        core.error(str(exc))
    except OSError as exc:
        core.error(f"Ошибка файловой системы: {exc}")


if __name__ == "__main__":
    main()
