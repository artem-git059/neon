#!/usr/bin/env python3
"""NeonTodo — задачи экосистемы NEON.

Задачи хранятся в JSON: ~/.local/share/neon/todos.json
(или NEON_HOME/data/todos.json).
"""

from __future__ import annotations

import datetime as dt
import time

import neoncore as core

DEFAULT_DATA = {"version": 1, "next_id": 1, "tasks": []}

PRIORITIES = {
    1: ("P1", "низкий", core.dim),
    2: ("P2", "важно", core.yellow),
    3: ("P3", "срочно", core.red),
}


# --------------------------------------------------------------------------
# Хранилище
# --------------------------------------------------------------------------

def load() -> dict:
    data = core.read_json(core.TODO_FILE, DEFAULT_DATA)
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        core.warn("Файл задач выглядит странно — начинаю с чистого списка.")
        return {"version": 1, "next_id": 1, "tasks": []}

    tasks = []
    for task in data["tasks"]:
        if not isinstance(task, dict) or not isinstance(task.get("text"), str) or not task["text"]:
            continue
        try:
            task["id"] = int(task.get("id", 0))
        except (TypeError, ValueError):
            continue
        try:
            priority = int(task.get("priority", 1))
        except (TypeError, ValueError):
            priority = 1
        task["priority"] = priority if priority in PRIORITIES else 1
        task["done"] = bool(task.get("done"))
        tasks.append(task)

    next_id = data.get("next_id")
    max_id = max((task["id"] for task in tasks), default=0)
    if not isinstance(next_id, int) or next_id <= max_id:
        next_id = max_id + 1
    return {"version": 1, "next_id": next_id, "tasks": tasks}


def save(data: dict) -> None:
    core.write_json(core.TODO_FILE, data)


# --------------------------------------------------------------------------
# Даты и отображение
# --------------------------------------------------------------------------

def parse_due(raw: str) -> str | None:
    raw = raw.strip()
    if not raw or raw in ("-", "нет", "без срока"):
        return None
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    raise core.NeonError(f"Не понял дату «{raw}». Пример: 31.12.2026")


def due_date(task: dict) -> dt.date | None:
    value = task.get("due")
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return None


def due_label(task: dict) -> str:
    date = due_date(task)
    if date is None:
        return ""
    pretty = date.strftime("%d.%m.%Y")
    if task.get("done"):
        return core.dim(f"до {pretty}")
    today = dt.date.today()
    if date < today:
        days = (today - date).days
        return core.red(
            f"просрочено на {days} {core.plural(days, 'день', 'дня', 'дней')}"
        )
    if date == today:
        return core.yellow("сегодня")
    if date == today + dt.timedelta(days=1):
        return core.yellow("завтра")
    return core.dim(f"до {pretty}")


def sort_tasks(tasks: list[dict]) -> list[dict]:
    def key(task: dict) -> tuple:
        due = due_date(task) or dt.date.max
        return (
            bool(task.get("done")),
            -int(task.get("priority", 1)),
            due,
            int(task.get("id", 0)),
        )

    return sorted(tasks, key=key)


def render_task(task: dict) -> str:
    done = bool(task.get("done"))
    priority = int(task.get("priority", 1))
    if priority not in PRIORITIES:
        priority = 1
    label, _, colour = PRIORITIES[priority]

    status = core.green("✓") if done else core.dim("·")
    text = core.dim(task["text"]) if done else task["text"]
    parts = [status, colour(label), text]
    due = due_label(task)
    if due:
        parts.append(due)
    return " ".join(parts)


def ask_priority(default: str = "2") -> int:
    while True:
        raw = core.ask("Приоритет: 1 — низкий, 2 — важно, 3 — срочно", default=default)
        if raw in ("1", "2", "3"):
            return int(raw)
        core.warn("Введите 1, 2 или 3.")


def ask_due(task: dict | None = None) -> str | None:
    current = due_date(task) if task else None
    default = current.strftime("%d.%m.%Y") if current else "-"
    prompt = "Срок (ДД.ММ.ГГГГ; «-» — без срока)"
    while True:
        raw = core.ask(prompt, default=default)
        try:
            return parse_due(raw)
        except core.NeonError as exc:
            core.warn(str(exc))


# --------------------------------------------------------------------------
# Действия
# --------------------------------------------------------------------------

def list_tasks() -> None:
    data = load()
    tasks = sort_tasks(data["tasks"])
    if not tasks:
        core.info("Задач пока нет. Добавьте первую: пункт 2.")
        core.pause()
        return

    active = sum(1 for task in tasks if not task.get("done"))
    core.header(
        "Задачи",
        f"{len(tasks)} всего · {active} в работе · {core.TODO_FILE}",
    )
    print()
    printed_done_header = False
    for index, task in enumerate(tasks, 1):
        if task.get("done") and not printed_done_header:
            print(core.dim("  ── выполнено ──"))
            printed_done_header = True
        print(f"  {index:>2}. {render_task(task)}")
    core.pause()


def add_task() -> None:
    text = core.ask("Задача")
    priority = ask_priority()
    due = ask_due()

    data = load()
    task = {
        "id": data["next_id"],
        "text": text,
        "priority": priority,
        "due": due,
        "done": False,
        "created": time.time(),
    }
    data["tasks"].append(task)
    data["next_id"] += 1
    save(data)

    extra = ""
    if due:
        extra = f", срок {dt.date.fromisoformat(due).strftime('%d.%m.%Y')}"
    core.success(f"Задача #{task['id']} добавлена ({PRIORITIES[priority][1]}{extra}).")
    core.pause()


def pick_task(prompt: str, only_active: bool = False) -> tuple[dict, dict | None]:
    data = load()
    tasks = [task for task in sort_tasks(data["tasks"]) if not only_active or not task.get("done")]
    if not tasks:
        core.warn("Подходящих задач нет.")
        core.pause()
        return data, None
    task = core.choose(tasks, render=render_task, prompt=prompt)
    return data, task


def toggle_task() -> None:
    data, task = pick_task("Что отметить?")
    if task is None:
        return

    task["done"] = not task.get("done")
    task["updated"] = time.time()
    save(data)

    if task["done"]:
        core.success(f"Готово: {task['text']}")
        if all(item.get("done") for item in data["tasks"]):
            core.info("Все задачи закрыты. Можно выдохнуть.")
    else:
        core.info(f"Снова в работе: {task['text']}")
    core.pause()


def edit_task() -> None:
    data, task = pick_task("Какую задачу изменить?")
    if task is None:
        return

    task["text"] = core.ask("Текст", default=task["text"])
    task["priority"] = ask_priority(str(task.get("priority", 2)))
    task["due"] = ask_due(task)
    task["updated"] = time.time()
    save(data)
    core.success("Задача обновлена.")
    core.pause()


def delete_task() -> None:
    data, task = pick_task("Какую задачу удалить?")
    if task is None:
        return
    if not core.confirm(f"Удалить «{task['text']}»?"):
        core.info("Отменено.")
        core.pause()
        return

    data["tasks"] = [item for item in data["tasks"] if item.get("id") != task.get("id")]
    save(data)
    core.success("Задача удалена.")
    core.pause()


def clear_done() -> None:
    data = load()
    done = [task for task in data["tasks"] if task.get("done")]
    if not done:
        core.info("Выполненных задач нет.")
        core.pause()
        return
    if not core.confirm(
        f"Убрать {len(done)} {core.plural(len(done), 'выполненную задачу', 'выполненные задачи', 'выполненных задач')}?"
    ):
        core.info("Отменено.")
        core.pause()
        return

    data["tasks"] = [task for task in data["tasks"] if not task.get("done")]
    save(data)
    core.success(f"Убрано: {len(done)}.")
    core.pause()


def statistics() -> None:
    data = load()
    tasks = data["tasks"]
    total = len(tasks)
    done = sum(1 for task in tasks if task.get("done"))
    active = total - done
    today = dt.date.today()
    overdue = sum(
        1
        for task in tasks
        if not task.get("done") and due_date(task) and due_date(task) < today
    )
    due_today = sum(
        1
        for task in tasks
        if not task.get("done") and due_date(task) == today
    )

    core.header("Статистика задач")
    if total == 0:
        core.info("Задач пока нет.")
        core.pause()
        return

    percent = round(done * 100 / total)
    print()
    print(f"  {core.bar(done, total)}  {percent}%")
    print(f"  Всего:       {total}")
    print(f"  В работе:    {active}")
    print(f"  Выполнено:   {done}")
    if overdue:
        print(f"  Просрочено:  {core.red(overdue)}")
    if due_today:
        print(f"  На сегодня:  {core.yellow(due_today)}")
    print()
    for priority, (label, title, colour) in PRIORITIES.items():
        count = sum(1 for task in tasks if int(task.get("priority", 1)) == priority)
        print(f"  {colour(label)} {title:<8} {count}")
    core.pause()


# --------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------

ACTIONS = {
    "1": list_tasks,
    "2": add_task,
    "3": toggle_task,
    "4": edit_task,
    "5": delete_task,
    "6": clear_done,
    "7": statistics,
}


def run() -> None:
    while True:
        choice = core.menu(
            "NeonTodo — задачи",
            [
                ("1", "Список задач"),
                ("2", "Добавить задачу"),
                ("3", "Выполнить / вернуть в работу"),
                ("4", "Изменить задачу"),
                ("5", "Удалить задачу"),
                ("6", "Убрать выполненные"),
                ("7", "Статистика"),
            ],
            subtitle=str(core.TODO_FILE),
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
        core.info("Выход из NeonTodo.")
    except core.NeonError as exc:
        core.error(str(exc))
    except OSError as exc:
        core.error(f"Ошибка файловой системы: {exc}")


if __name__ == "__main__":
    main()
