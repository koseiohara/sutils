#!/usr/bin/env python3
"""A task-aware, deliberately thin facade over a dedicated sclipple store.

CLI due dates accept DD, MM-DD, and YYYY-MM-DD and are stored as ISO dates.
"""

import argparse
import datetime as dt
import json
import os
import re
import shlex
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

SCLIPPLE = os.environ.get("SCLIPPLE", "sclipple")
STODO_DIR = Path(os.environ.get(
    "STODO_DIR", str(Path.home() / ".local/share/sclipple-todo")
)).expanduser()
STODO_EXTENSION = "txt"
TASK_TAG = "task"
SELF = str(Path(__file__).resolve())
CAPTURE_MAGIC = b"STODO-CAPTURE-V1\0"
INTERNAL_ENV = "_STODO_INTERNAL_CALLBACK"
VALID_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
DAY_INPUT = re.compile(r"^[0-9]{2}$")
MONTH_DAY_INPUT = re.compile(r"^[0-9]{2}-[0-9]{2}$")
ISO_DATE_INPUT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TASK_FIELDS = ("title", "created", "due", "priority", "completed")
OWNED_COMMANDS = {"add", "ls", "show", "set", "done", "reopen"}
INTERNAL_COMMANDS = {"__capture", "__create", "__set", "__complete"}


class Error(Exception):
    pass


def fail(message):
    raise Error(message)


def now():
    return dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def today():
    return dt.date.today()


def ensure_store():
    if not STODO_DIR.is_absolute():
        fail(f"STODO_DIR must be absolute: {STODO_DIR}")
    if STODO_DIR.exists() and not STODO_DIR.is_dir():
        fail(f"not a directory: {STODO_DIR}")
    STODO_DIR.mkdir(parents=True, exist_ok=True)


def fixed_options_are_valid(arguments):
    """Keep the namespace fixed without enumerating sclipple subcommands."""
    for argument in arguments:
        if argument == "--directory" or argument.startswith("--directory="):
            fail("the sclipple directory is fixed")
        if argument == "--extension" or argument.startswith("--extension="):
            fail(f"the stodo extension is fixed to {STODO_EXTENSION}")


def sclipple_command(*arguments, editor=None):
    command = [SCLIPPLE, f"--directory={STODO_DIR}",
               f"--extension={STODO_EXTENSION}"]
    if editor is not None:
        command.append(f"--editor={editor}")
    command.extend(map(str, arguments))
    return command


def run_sclipple(*arguments, editor=None, capture=False, check=True, env=None):
    ensure_store()
    fixed_options_are_valid(arguments)
    process = subprocess.run(
        sclipple_command(*arguments, editor=editor), text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env=env,
    )
    if check and process.returncode:
        message = (process.stderr or "").strip() if capture else ""
        fail(message or "sclipple command failed")
    return process


def run_native(arguments):
    if not arguments:
        fail("native requires a sclipple command, selector, or option")
    return run_sclipple(*arguments, check=False).returncode


def callback_command(name, *arguments):
    words = [sys.executable, SELF, name, *map(str, arguments)]
    return " ".join(shlex.quote(word) for word in words)


def callback_environment():
    environment = os.environ.copy()
    environment[INTERNAL_ENV] = "1"
    return environment


def callback(name, selectors, *arguments):
    run_sclipple(
        *selectors,
        editor=callback_command(f"__{name}", *arguments),
        env=callback_environment(),
    )


def list_keys(selectors=()):
    """Ask sclipple for keys; never discover or construct note filenames."""
    process = run_sclipple("ls", "--short", *selectors, capture=True, check=False)
    if process.returncode and not process.stdout.strip():
        return []
    keys = []
    for raw_line in process.stdout.splitlines():
        token = raw_line.strip().split(None, 1)[0] if raw_line.strip() else ""
        if token.startswith("[") and token.endswith("]"):
            token = token[1:-1]
        if VALID_KEY.fullmatch(token) and token not in keys:
            keys.append(token)
    return keys


def validate_key(key):
    if not VALID_KEY.fullmatch(key) or key in {".", ".."}:
        fail("KEY must contain only ASCII letters, digits, _ or -")


def resolve_key(token, keys=None):
    keys = list_keys() if keys is None else keys
    if token in keys:
        return token
    matches = [key for key in keys if key.startswith(token)]
    if not matches:
        fail(f"unknown KEY or prefix '{token}'")
    if len(matches) > 1:
        fail(f"ambiguous KEY prefix '{token}': {', '.join(matches)}")
    return matches[0]


def slugify(title):
    value = title.lower().encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:48].rstrip("-")


def unique_key(base, keys):
    if base not in keys:
        return base
    suffix = 2
    while f"{base}-{suffix}" in keys:
        suffix += 1
    return f"{base}-{suffix}"


def fallback_key(keys):
    suffix = 1
    while f"task-{suffix}" in keys:
        suffix += 1
    return f"task-{suffix}"


def parse_stored_date(value, field="due"):
    if value == "-":
        return None
    if not ISO_DATE_INPUT.fullmatch(value):
        fail(f"invalid {field}: {value}; expected YYYY-MM-DD or -")
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        fail(f"invalid {field}: {value}; expected YYYY-MM-DD or -")


def normalize_date_input(value, field="due", base=None):
    """Normalize DD, MM-DD, or YYYY-MM-DD against the local current date."""
    if value == "-":
        return value
    base = base or today()
    try:
        if DAY_INPUT.fullmatch(value):
            parsed = dt.date(base.year, base.month, int(value))
        elif MONTH_DAY_INPUT.fullmatch(value):
            month, day = map(int, value.split("-"))
            parsed = dt.date(base.year, month, day)
        elif ISO_DATE_INPUT.fullmatch(value):
            parsed = dt.date.fromisoformat(value)
        else:
            fail(
                f"invalid {field}: {value}; expected DD, MM-DD, "
                "YYYY-MM-DD, or -"
            )
    except ValueError:
        fail(
            f"invalid {field}: {value}; expected a valid DD, MM-DD, "
            "YYYY-MM-DD, or -"
        )
    return parsed.isoformat()


def validate_timestamp(value, field):
    if value == "-":
        return
    try:
        dt.datetime.fromisoformat(value)
    except ValueError:
        fail(f"invalid {field}: {value}")


def callback_file(filename):
    """Validate a callback-supplied path without assuming where it lives."""
    path = Path(filename)
    if path.is_symlink() or not path.is_file():
        fail(f"refusing unsafe task file: {path}")
    return path


def validate_task_values(task, key):
    for field in TASK_FIELDS:
        if field not in task:
            fail(f"{key}: missing required field: {field}")
    if not task["title"].strip():
        fail(f"{key}: title must not be empty")
    parse_stored_date(task["due"])
    if task["priority"] not in {"A", "B", "C"}:
        fail(f"{key}: priority must be A, B, or C")
    validate_timestamp(task["created"], "created")
    validate_timestamp(task["completed"], "completed")


def parse_task_text(key, text):
    header, separator, body = text.partition("\n---\n")
    if not separator:
        fail(f"{key}: missing metadata separator")
    task = {}
    for line in header.splitlines():
        if ": " not in line:
            fail(f"{key}: invalid metadata line: {line}")
        name, value = line.split(": ", 1)
        if name in task:
            fail(f"{key}: duplicate metadata field: {name}")
        task[name] = value
    validate_task_values(task, key)
    task.update({"_body": body, "_key": key})
    return task


def write_task(path, task):
    data = "".join(f"{field}: {task[field]}\n" for field in TASK_FIELDS)
    data += "---\n" + task.get("_body", "")
    fd, temporary = tempfile.mkstemp(prefix=".stodo-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def capture_callback(fd, files):
    if len(files) != 1:
        fail("capture expects exactly one task file")
    data = callback_file(files[0]).read_bytes()
    with os.fdopen(fd, "wb", closefd=True) as output:
        output.write(CAPTURE_MAGIC)
        output.write(struct.pack("!Q", len(data)))
        output.write(data)


def decode_capture(data):
    if not data.startswith(CAPTURE_MAGIC):
        fail("invalid response from capture callback")
    position = len(CAPTURE_MAGIC)
    if len(data) < position + 8:
        fail("truncated capture response")
    size = struct.unpack_from("!Q", data, position)[0]
    position += 8
    if position + size != len(data):
        fail("invalid capture response length")
    try:
        return data[position:].decode("utf-8")
    except UnicodeDecodeError:
        fail("task is not valid UTF-8")


def read_task(key):
    """Read one exact KEY through sclipple's editor callback interface."""
    read_fd, write_fd = os.pipe()
    editor = callback_command("__capture", write_fd)
    ensure_store()
    try:
        process = subprocess.Popen(
            sclipple_command(key, editor=editor),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            pass_fds=(write_fd,),
            env=callback_environment(),
        )
    except Exception:
        os.close(read_fd)
        os.close(write_fd)
        raise
    os.close(write_fd)
    try:
        with os.fdopen(read_fd, "rb", closefd=True) as source:
            captured = source.read()
    finally:
        _, stderr = process.communicate()
    if process.returncode:
        message = stderr.decode("utf-8", "replace").strip()
        fail(message or f"failed to read KEY: {key}")
    return parse_task_text(key, decode_capture(captured))


def task_is_done(task):
    return task["completed"] != "-"


def render_list(tasks, filters):
    mode = filters["mode"]
    rows = []
    for task in tasks:
        done = task_is_done(task)
        due = parse_stored_date(task["due"])
        if mode == "open" and done:
            continue
        if mode == "done" and not done:
            continue
        if filters.get("due") is not None and task["due"] != filters["due"]:
            continue
        if filters.get("priority") is not None and task["priority"] != filters["priority"]:
            continue
        if filters.get("overdue") and (done or due is None or due >= today()):
            continue
        rows.append(task)
    rank = {"A": 0, "B": 1, "C": 2}
    rows.sort(key=lambda task: (
        rank[task["priority"]], parse_stored_date(task["due"]) or dt.date.max,
        task["_key"],
    ))
    show_state = mode == "all"
    if show_state:
        print(f"{'S':1}  {'P':1}  {'DUE':10}  {'KEY':24}  TITLE")
        print(f"{'-':1}  {'-':1}  {'-' * 10}  {'-' * 24}  {'-' * 5}")
    else:
        print(f"{'P':1}  {'DUE':10}  {'KEY':24}  TITLE")
        print(f"{'-':1}  {'-' * 10}  {'-' * 24}  {'-' * 5}")
    for task in rows:
        state = f"{'x' if task_is_done(task) else ' ':1}  " if show_state else ""
        print(f"{state}{task['priority']:1}  {task['due']:10.10}  "
              f"{task['_key']:24.24}  {task['title']}")


def render_show(task):
    completed = task["completed"] if task_is_done(task) else "no"
    print(f"key:        {task['_key']}")
    print(f"title:      {task['title']}")
    print(f"created:    {task['created']}")
    print(f"due:        {task['due']}")
    print(f"priority:   {task['priority']}")
    print(f"completed:  {completed}")
    if task["_body"]:
        print()
        print(task["_body"], end="" if task["_body"].endswith("\n") else "\n")


def title_from_remainder(words):
    words = list(words)
    if words[:1] == ["--"]:
        words = words[1:]
    title = " ".join(words).strip()
    if not title:
        fail("missing title")
    return title


def cmd_add(args):
    title = title_from_remainder(args.title)
    due = normalize_date_input(args.due)
    keys = list_keys()
    if args.key:
        validate_key(args.key)
        if args.key in keys:
            fail(f"KEY already exists: {args.key}")
        key = args.key
    else:
        base = slugify(title)
        key = unique_key(base, keys) if base else fallback_key(keys)
    command = ["add", key, "-t", TASK_TAG]
    for tag in args.tags:
        command.extend(("-t", tag))
    run_sclipple(*command, capture=True)
    payload = json.dumps({
        "title": title, "created": now(), "due": due,
        "priority": args.priority, "completed": "-",
    }, ensure_ascii=False, separators=(",", ":"))
    editor = callback_command("__create", key, payload)
    initialized = run_sclipple(
        key,
        editor=editor,
        capture=True,
        check=False,
        env=callback_environment(),
    )
    if initialized.returncode:
        run_sclipple("rm", key, capture=True, check=False)
        fail((initialized.stderr or "").strip() or "task initialization failed")
    print(key)


def selected_keys(args):
    keys = list_keys()
    if not keys:
        return []
    selectors = [resolve_key(key, keys) for key in args.keys]
    for tag in args.tags:
        selectors.extend(("-t", tag))
    if args.tag_match:
        selectors.extend(("--tag-match", args.tag_match))
    return list_keys(selectors or ["-t", TASK_TAG])


def cmd_ls(args):
    due = normalize_date_input(args.due) if args.due is not None else None
    filters = {
        "mode": "all" if args.all else "done" if args.done else "open",
        "due": due, "priority": args.priority, "overdue": args.overdue,
    }
    keys = selected_keys(args)
    render_list([read_task(key) for key in keys], filters)


def cmd_show(args):
    key = resolve_key(args.key)
    render_show(read_task(key))


def cmd_set(args):
    key = resolve_key(args.key)
    updates = {}
    if args.title is not None:
        title = args.title.strip()
        if not title:
            fail("title must not be empty")
        updates["title"] = title
    if args.due is not None:
        updates["due"] = normalize_date_input(args.due)
    if args.clear_due:
        updates["due"] = "-"
    if args.priority is not None:
        updates["priority"] = args.priority
    if not updates:
        fail("no changes requested")
    callback("set", [key], key, json.dumps(
        updates, ensure_ascii=False, separators=(",", ":")
    ))


def cmd_completion(args, completed):
    keys = list_keys()
    for token in args.keys:
        key = resolve_key(token, keys)
        callback("complete", [key], key, completed)


def cb_create(expected_key, payload, files):
    if len(files) != 1:
        fail("task creator expects exactly one file")
    path = callback_file(files[0])
    if path.stat().st_size:
        fail("refusing to initialize a non-empty task")
    try:
        values = json.loads(payload)
    except json.JSONDecodeError as exc:
        fail(f"invalid internal task data: {exc}")
    if set(values) != set(TASK_FIELDS) or not all(
        isinstance(value, str) for value in values.values()
    ):
        fail("invalid internal task data")
    validate_task_values(values, expected_key)
    write_task(path, {**values, "_body": ""})


def cb_set(expected_key, encoded_updates, files):
    if len(files) != 1:
        fail("set expects exactly one task")
    path = callback_file(files[0])
    task = parse_task_text(expected_key, path.read_text(encoding="utf-8"))
    try:
        updates = json.loads(encoded_updates)
    except json.JSONDecodeError as exc:
        fail(f"invalid internal update request: {exc}")
    if not isinstance(updates, dict) or not set(updates).issubset(
        {"title", "due", "priority"}
    ):
        fail("invalid internal update request")
    task.update(updates)
    validate_task_values(task, expected_key)
    write_task(path, task)


def cb_complete(expected_key, completed, files):
    if len(files) != 1:
        fail("completion update expects exactly one task")
    path = callback_file(files[0])
    task = parse_task_text(expected_key, path.read_text(encoding="utf-8"))
    if completed == "-" and task_is_done(task):
        task["completed"] = completed
        write_task(path, task)
    elif completed != "-" and not task_is_done(task):
        task["completed"] = completed
        write_task(path, task)


def add_selectors(parser):
    parser.add_argument("keys", nargs="*")
    parser.add_argument("-t", "--tag", dest="tags", action="append", default=[])
    parser.add_argument("--tag-match", choices=("and", "or"))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="stodo", description="task-aware facade over a dedicated sclipple store",
        epilog=("Commands not owned by stodo are forwarded unchanged to sclipple. "
                "Use 'stodo native ...' only for a shadowed sclipple command."),
    )
    sub = parser.add_subparsers(dest="command")
    add = sub.add_parser("add", help="add a task")
    add.add_argument("-k", "--key")
    add.add_argument("-d", "--due", default="-")
    add.add_argument("-p", "--priority", choices=("A", "B", "C"), default="B")
    add.add_argument("-t", "--tag", dest="tags", action="append", default=[])
    add.add_argument("title", nargs=argparse.REMAINDER)

    ls = sub.add_parser("ls", help="list parsed tasks")
    add_selectors(ls)
    mode = ls.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true")
    mode.add_argument("--done", action="store_true")
    ls.add_argument("--overdue", action="store_true")
    ls.add_argument("--due")
    ls.add_argument("--priority", choices=("A", "B", "C"))

    show = sub.add_parser("show", help="show one parsed task")
    show.add_argument("key")
    update = sub.add_parser("set", help="update task metadata")
    update.add_argument("key")
    update.add_argument("--title")
    due = update.add_mutually_exclusive_group()
    due.add_argument("--due")
    due.add_argument("--clear-due", action="store_true")
    update.add_argument("--priority", choices=("A", "B", "C"))
    done = sub.add_parser("done", help="mark tasks completed")
    done.add_argument("keys", nargs="+")
    reopen = sub.add_parser("reopen", help="mark tasks incomplete")
    reopen.add_argument("keys", nargs="+")
    return parser


def run_internal(argv):
    ensure_store()
    command = argv[0]
    if command == "__capture":
        capture_callback(int(argv[1]), argv[2:])
    elif command == "__create":
        cb_create(argv[1], argv[2], argv[3:])
    elif command == "__set":
        cb_set(argv[1], argv[2], argv[3:])
    elif command == "__complete":
        cb_complete(argv[1], argv[2], argv[3:])
    else:
        fail(f"unknown internal callback: {command}")


def main(argv):
    if (
        os.environ.get(INTERNAL_ENV) == "1"
        and argv
        and argv[0] in INTERNAL_COMMANDS
    ):
        run_internal(argv)
        return 0
    if argv[:1] == ["native"]:
        return run_native(argv[1:])
    if not argv or argv in (["-h"], ["--help"]):
        build_parser().print_help()
        return 0
    if argv[0] not in OWNED_COMMANDS:
        return run_native(argv)
    args = build_parser().parse_args(argv)
    if args.command == "add":
        cmd_add(args)
    elif args.command == "ls":
        cmd_ls(args)
    elif args.command == "show":
        cmd_show(args)
    elif args.command == "set":
        cmd_set(args)
    elif args.command == "done":
        cmd_completion(args, now())
    elif args.command == "reopen":
        cmd_completion(args, "-")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Error as exc:
        print(f"stodo: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except FileNotFoundError as exc:
        print(f"stodo: command not found: {exc.filename}", file=sys.stderr)
        raise SystemExit(127)
    except KeyboardInterrupt:
        raise SystemExit(130)
