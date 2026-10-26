#!/usr/bin/env python3
"""Neonotes — заметки экосистемы NEON.

Заметки лежат обычными текстовыми файлами в ~/.local/share/neon/notes
(или в NEON_HOME/data/notes, если задана эта переменная).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import neoncore as core


# --------------------------------------------------------------------------
# Список и отображение
# --------------------------------------------------------------------------

def notes() -> list[Path]:
    core.ensure_dirs()
    result = []
    for path in core.NOTES_DIR.glob("*.txt"):
        try:
            path.stat()
        except OSError:
            continue
        result.append(path)

    def modified(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    return sorted(result, key=modified, reverse=True)


def preview(path: Path, limit: int = 44) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = " ".join(line.split())
                if line:
                    return line[:limit] + ("…" if len(line) > limit else "")
    except OSError:
        pass
    return "(пусто)"


def render_note(path: Path) -> str:
    return f"{core.bold(path.stem)}  {core.dim(preview(path))}"


def pick_note(prompt: str = "Номер заметки") -> Path | None:
    items = notes()
    if not items:
        core.warn("Заметок пока нет.")
        core.pause()
        return None
    return core.choose(items, render=render_note, prompt=prompt)


def highlight(line: str, query: str) -> str:
    if not query:
        return line
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    return pattern.sub(lambda match: core.yellow(core.bold(match.group(0))), line)


# --------------------------------------------------------------------------
# Действия
# --------------------------------------------------------------------------

def list_notes() -> None:
    items = notes()
    if not items:
        core.info("Заметок пока нет. Создайте первую: пункт 2.")
        core.pause()
        return

    core.header(
        "Заметки",
        f"{len(items)} {core.plural(len(items), 'заметка', 'заметки', 'заметок')} · {core.NOTES_DIR}",
    )
    for index, path in enumerate(items, 1):
        try:
            size = core.human_size(path.stat().st_size)
        except OSError:
            size = "?"
        print(f"  {index:>2}. {core.bold(path.stem)}  {core.dim(size)}")
        print(f"      {core.dim(preview(path, 52))}")
    core.pause()


def create_note() -> None:
    name = core.ask("Имя заметки")
    path = core.note_path(name)
    append = False
    if path.exists():
        core.warn(f"Заметка «{path.stem}» уже есть.")
        if not core.confirm("Дописать текст в конец?"):
            core.info("Отменено.")
            core.pause()
            return
        append = True

    text = core.multiline("Текст заметки")
    if not text.strip():
        core.warn("Пустая заметка не сохранена.")
        core.pause()
        return

    if append:
        try:
            old = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise core.NeonError(f"Не удалось прочитать заметку: {exc}") from exc
        if old and not old.endswith("\n"):
            old += "\n"
        if old and not old.endswith("\n\n"):
            old += "\n"
        core.atomic_write_text(path, old + text.rstrip() + "\n")
    else:
        core.atomic_write_text(path, text.rstrip() + "\n")

    symbols = len(text)
    core.success(
        f"Сохранено: {path.stem} ({symbols} {core.plural(symbols, 'символ', 'символа', 'символов')})."
    )
    core.pause()


def read_note() -> None:
    path = pick_note("Какую заметку прочитать?")
    if path is None:
        return
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        modified = path.stat().st_mtime
    except OSError as exc:
        raise core.NeonError(f"Не удалось прочитать заметку: {exc}") from exc

    core.header(path.stem, core.human_date(modified))
    print()
    print(content.rstrip() if content.strip() else core.dim("(пусто)"))
    print()
    print(core.rule())
    words = len(content.split())
    symbols = len(content.rstrip("\n"))
    print(
        core.dim(
            f"{symbols} {core.plural(symbols, 'символ', 'символа', 'символов')} · "
            f"{words} {core.plural(words, 'слово', 'слова', 'слов')}"
        )
    )
    core.pause()


def append_note() -> None:
    path = pick_note("Какую заметку дополнить?")
    if path is None:
        return
    text = core.multiline("Новый текст")
    if not text.strip():
        core.warn("Ничего не добавлено.")
        core.pause()
        return
    try:
        old = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise core.NeonError(f"Не удалось прочитать заметку: {exc}") from exc
    if old and not old.endswith("\n"):
        old += "\n"
    if old and not old.endswith("\n\n"):
        old += "\n"
    core.atomic_write_text(path, old + text.rstrip() + "\n")
    core.success(f"Дописано в «{path.stem}».")
    core.pause()


def open_in_editor() -> None:
    path = pick_note("Какую заметку открыть в редакторе?")
    if path is None:
        return

    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        for candidate in ("micro", "nano", "vim", "vi"):
            if shutil.which(candidate):
                editor = candidate
                break
    if not editor:
        core.warn("Редактор не найден. Установите nano/vim или задайте $EDITOR.")
        core.pause()
        return

    command = shlex.split(editor) + [str(path)]
    core.info(f"Запускаю: {' '.join(command)}")
    try:
        subprocess.run(command, check=False)
    except OSError as exc:
        core.error(f"Не удалось запустить редактор: {exc}")
    else:
        core.success("Редактор закрыт.")
    core.pause()


def search_notes() -> None:
    query = core.ask("Что искать")
    needle = query.casefold()
    found = 0

    for path in notes():
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        name_matches = needle in path.stem.casefold()
        content_matches = needle in content.casefold()
        if not (name_matches or content_matches):
            continue
        matches = [
            line.strip()
            for line in content.splitlines()
            if needle in line.casefold()
        ]
        if not matches:
            matches = ["(совпадение в имени заметки)"]

        if found == 0:
            core.header("Поиск", f"«{query}»")
        found += 1
        print()
        print(
            f"  {core.bold(path.stem)} "
            + core.dim(f"— {len(matches)} {core.plural(len(matches), 'совпадение', 'совпадения', 'совпадений')}")
        )
        for line in matches[:3]:
            print(f"    {highlight(line[:110], query)}")
        if len(matches) > 3:
            print(core.dim(f"    … ещё {len(matches) - 3}"))

    if found == 0:
        core.warn("Ничего не найдено.")
    else:
        print()
        core.info(
            f"Найдено в {found} {core.plural(found, 'заметке', 'заметках', 'заметках')}."
        )
    core.pause()


def delete_note() -> None:
    path = pick_note("Какую заметку удалить?")
    if path is None:
        return
    if not core.confirm(f"Удалить «{path.stem}» навсегда?"):
        core.info("Отменено.")
        core.pause()
        return
    try:
        path.unlink()
    except OSError as exc:
        raise core.NeonError(f"Не удалось удалить заметку: {exc}") from exc
    core.success(f"Заметка «{path.stem}» удалена.")
    core.pause()


# --------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------

ACTIONS = {
    "1": list_notes,
    "2": create_note,
    "3": read_note,
    "4": append_note,
    "5": open_in_editor,
    "6": search_notes,
    "7": delete_note,
}


def run() -> None:
    while True:
        choice = core.menu(
            "Neonotes — заметки",
            [
                ("1", "Список заметок"),
                ("2", "Создать заметку"),
                ("3", "Прочитать заметку"),
                ("4", "Дополнить заметку"),
                ("5", "Открыть в редакторе"),
                ("6", "Поиск по тексту"),
                ("7", "Удалить заметку"),
            ],
            subtitle=str(core.NOTES_DIR),
            exit_label="Назад",
        )
        if choice is None:
            return
        try:
            ACTIONS[choice]()
        except core.NeonError as exc:
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
        core.info("Выход из Neonotes.")
    except core.NeonError as exc:
        core.error(str(exc))
    except OSError as exc:
        core.error(f"Ошибка файловой системы: {exc}")


if __name__ == "__main__":
    main()
