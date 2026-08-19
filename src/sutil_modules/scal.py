#!/usr/bin/env python3
"""scal: a calendar-aware facade over a dedicated sclipple instance.

scal owns only calendar semantics. Everything else is delegated to sclipple
with a fixed calendar --directory and --extension=md. Only Python's standard
library is used. scal never discovers or constructs sclipple-managed note
paths; it reads/writes only paths supplied explicitly through --editor.
"""

import argparse
import calendar
import datetime as dt
import json
import os
import re
import shlex
import struct
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional


SCLIPPLE = os.environ.get("SCLIPPLE", "sclipple")
SCAL_DIR = Path(
    os.environ.get("SCAL_DIR", str(Path.home() / ".local/share/sclipple-calendar"))
).expanduser()
SCAL_EXTENSION = "md"
SELF = str(Path(__file__).resolve())
CAPTURE_MAGIC = b"SCAL-CAPTURE-V1\0"
VALID_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
RELATIVE_SELECTOR = re.compile(r"^([1-9][0-9]*)([dwm])$")
MONTH_SELECTOR = re.compile(r"^([0-9]{4})-([0-9]{2})$")
TIME_INPUT = re.compile(r"^(\d{1,2})(?::(\d{1,2}))?$")
META_FIELDS = ("date", "start", "end-date", "end", "location")


class ScalError(Exception):
    pass


@dataclass(frozen=True)
class Event:
    key: str
    title: str
    date: dt.date
    start: Optional[dt.time] = None
    end_date: Optional[dt.date] = None
    end: Optional[dt.time] = None
    location: Optional[str] = None
    body: str = ""

    @property
    def last_date(self):
        return self.end_date or self.date


@dataclass(frozen=True)
class DateRange:
    start: dt.date
    end: dt.date


def fail(message):
    raise ScalError(message)


def ensure_store():
    if not SCAL_DIR.is_absolute():
        fail(f"SCAL_DIR must be absolute: {SCAL_DIR}")
    SCAL_DIR.mkdir(parents=True, exist_ok=True)


def sclipple_command(*arguments, editor=None):
    command = [
        SCLIPPLE,
        f"--directory={SCAL_DIR}",
        f"--extension={SCAL_EXTENSION}",
    ]
    if editor is not None:
        command.append(f"--editor={editor}")
    command.extend(map(str, arguments))
    return command


def run_sclipple(*arguments, editor=None, capture=False, check=True, env=None, pass_fds=()):
    ensure_store()
    process = subprocess.run(
        sclipple_command(*arguments, editor=editor),
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env=env,
        pass_fds=pass_fds,
    )
    if check and process.returncode:
        message = (process.stderr or "").strip() if capture else ""
        fail(message or "sclipple command failed")
    return process


def run_native(arguments):
    """Run a sclipple-native operation in scal's dedicated store.

    Arguments are forwarded verbatim.  scal contributes only its namespace:
    the fixed calendar --directory and --extension=md options.
    """
    ensure_store()
    command = sclipple_command(*arguments)
    process = subprocess.run(command)
    if process.returncode:
        raise SystemExit(process.returncode)
    return 0


def callback_command(name, *arguments):
    words = [sys.executable, SELF, name, *map(str, arguments)]
    return " ".join(shlex.quote(word) for word in words)


def capture_callback(fd, files):
    with os.fdopen(fd, "wb", closefd=True) as output:
        output.write(CAPTURE_MAGIC)
        output.write(struct.pack("!I", len(files)))
        for filename in files:
            data = Path(filename).read_bytes()
            output.write(struct.pack("!Q", len(data)))
            output.write(data)


def decode_capture(data):
    if not data:
        return None
    if not data.startswith(CAPTURE_MAGIC):
        fail("invalid response from capture callback")
    pos = len(CAPTURE_MAGIC)
    if len(data) < pos + 4:
        fail("truncated capture response")
    count = struct.unpack_from("!I", data, pos)[0]
    pos += 4
    records = []
    for _ in range(count):
        if len(data) < pos + 8:
            fail("truncated capture response")
        size = struct.unpack_from("!Q", data, pos)[0]
        pos += 8
        end = pos + size
        if end > len(data):
            fail("truncated capture response")
        records.append(data[pos:end])
        pos = end
    if pos != len(data):
        fail("unexpected data after capture response")
    return records


def capture_key(key):
    ensure_store()
    read_fd, write_fd = os.pipe()
    editor = callback_command("__capture", write_fd)
    try:
        process = subprocess.Popen(
            sclipple_command(key, editor=editor),
            pass_fds=(write_fd,),
            stdout=subprocess.DEVNULL,
        )
    except Exception:
        os.close(read_fd)
        os.close(write_fd)
        raise
    os.close(write_fd)
    try:
        with os.fdopen(read_fd, "rb", closefd=True) as source:
            data = source.read()
    finally:
        status = process.wait()
    if status:
        fail(f"failed to read KEY: {key}")
    records = decode_capture(data)
    if records is None or len(records) != 1:
        fail(f"KEY did not resolve to exactly one schedule: {key}")
    try:
        return records[0].decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"{key}: schedule is not valid UTF-8")
        raise AssertionError from exc


def list_keys(tags=None, tag_match=None):
    args = ["ls", "--short"]
    for tag in tags or []:
        args.extend(("-t", tag))
    if tag_match:
        args.extend(("--tag-match", tag_match))
    process = run_sclipple(*args, capture=True, check=False)
    if process.returncode:
        # An uninitialized/empty store may produce a nonzero result depending on
        # sclipple version. Treat no stdout as an empty calendar; preserve real errors.
        if not (process.stdout or "").strip():
            return []
        fail((process.stderr or "").strip() or "failed to list schedules")
    keys = []
    for raw_line in process.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        token = line.split(None, 1)[0]
        if token.startswith("[") and token.endswith("]"):
            token = token[1:-1]
        if VALID_KEY.fullmatch(token) and token not in keys:
            keys.append(token)
    return keys


def validate_key(key):
    if not VALID_KEY.fullmatch(key) or key in {".", ".."}:
        fail("KEY must contain only ASCII letters, digits, _ or -")


def parse_date(value, field="date"):
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        fail(f"invalid {field}: {value}; expected YYYY-MM-DD")


def parse_time(value, field="time"):
    match = TIME_INPUT.fullmatch(value)
    if not match:
        fail(f"invalid {field}: {value}; expected H, HH, H:MM, or HH:MM")
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    if hour > 23 or minute > 59:
        fail(f"invalid {field}: {value}")
    return dt.time(hour, minute)


def format_time(value):
    return value.strftime("%H:%M")


def parse_document(key, text):
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# ") or not lines[0][2:].strip():
        fail(f"{key}: first line must be '# TITLE'")
    title = lines[0][2:].strip()
    index = 1
    while index < len(lines) and lines[index] == "":
        index += 1
    fields = {}
    while index < len(lines) and lines[index] != "":
        line = lines[index]
        if ":" not in line:
            fail(f"{key}: invalid metadata line: {line}")
        name, value = line.split(":", 1)
        name = name.strip()
        value = value.lstrip()
        if name not in META_FIELDS:
            fail(f"{key}: unknown metadata field: {name}")
        if name in fields:
            fail(f"{key}: duplicate metadata field: {name}")
        if not value:
            fail(f"{key}: empty metadata field: {name}")
        fields[name] = value
        index += 1
    while index < len(lines) and lines[index] == "":
        index += 1
    body = "\n".join(lines[index:])
    if text.endswith("\n") and body:
        body += "\n"
    if "date" not in fields:
        fail(f"{key}: missing required field: date")
    start_date = parse_date(fields["date"], f"{key} date")
    start = parse_time(fields["start"], f"{key} start") if "start" in fields else None
    end_date = parse_date(fields["end-date"], f"{key} end-date") if "end-date" in fields else None
    end = parse_time(fields["end"], f"{key} end") if "end" in fields else None
    event = Event(
        key=key,
        title=title,
        date=start_date,
        start=start,
        end_date=end_date,
        end=end,
        location=fields.get("location"),
        body=body,
    )
    validate_event(event)
    return event


def validate_event(event):
    if event.end is not None and event.start is None:
        fail(f"{event.key}: end time requires a start time")
    if event.end_date is not None and event.end_date < event.date:
        fail(f"{event.key}: end-date is earlier than date")
    if event.start is not None and event.end is not None:
        effective_end_date = event.end_date or event.date
        start_dt = dt.datetime.combine(event.date, event.start)
        end_dt = dt.datetime.combine(effective_end_date, event.end)
        if end_dt < start_dt:
            fail(f"{event.key}: end is earlier than start")


def render_document(event):
    lines = [f"# {event.title}", "", f"date: {event.date.isoformat()}"]
    if event.start is not None:
        lines.append(f"start: {format_time(event.start)}")
    if event.end_date is not None:
        lines.append(f"end-date: {event.end_date.isoformat()}")
    if event.end is not None:
        lines.append(f"end: {format_time(event.end)}")
    if event.location:
        lines.append(f"location: {event.location}")
    lines.append("")
    text = "\n".join(lines) + "\n"
    if event.body:
        text += event.body
        if not text.endswith("\n"):
            text += "\n"
    return text


def write_callback(expected_key, *files):
    if len(files) != 1:
        fail("schedule writer expects exactly one note file")
    path = Path(files[0])
    content = sys.stdin.read()
    # Validate before replacing the sclipple-selected file.
    parse_document(expected_key, content)
    path.write_text(content, encoding="utf-8")


def update_callback(key, encoded_updates, *files):
    if len(files) != 1:
        fail("schedule updater expects exactly one note file")
    path = Path(files[0])
    event = parse_document(key, path.read_text(encoding="utf-8"))
    updates = decode_updates(encoded_updates)
    event = apply_updates(event, updates)
    path.write_text(render_document(event), encoding="utf-8")


def encode_updates(updates):
    return json.dumps(updates, ensure_ascii=False, separators=(",", ":"))


def decode_updates(encoded):
    try:
        value = json.loads(encoded)
    except json.JSONDecodeError as exc:
        fail(f"invalid internal update request: {exc}")
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        fail("invalid internal update request")
    return value


def apply_updates(event, updates):
    kwargs = {}
    if "title" in updates:
        title = updates["title"].strip()
        if not title:
            fail("title must not be empty")
        kwargs["title"] = title
    if "date" in updates:
        kwargs["date"] = parse_date(updates["date"])
    if "start" in updates:
        kwargs["start"] = parse_time(updates["start"], "start")
    if "end-date" in updates:
        kwargs["end_date"] = parse_date(updates["end-date"], "end-date")
    if "end" in updates:
        kwargs["end"] = parse_time(updates["end"], "end")
    if "location" in updates:
        kwargs["location"] = updates["location"] or None
    if updates.get("clear-end") == "1":
        kwargs["end_date"] = None
        kwargs["end"] = None
    if updates.get("all-day") == "1":
        kwargs["start"] = None
        kwargs["end"] = None
    updated = replace(event, **kwargs)
    validate_event(updated)
    return updated


def read_event(key):
    validate_key(key)
    return parse_document(key, capture_key(key))


def read_events(tags=None, tag_match=None):
    events = []
    for key in list_keys(tags, tag_match):
        events.append(read_event(key))
    return events


def add_months(value, months):
    month_index = value.year * 12 + (value.month - 1) + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    last_day = calendar.monthrange(year, month)[1]
    return dt.date(year, month, min(value.day, last_day))


def selector_range(tokens, *, base=None):
    base = base or dt.date.today()
    if not tokens:
        tokens = ["1w"]
    if len(tokens) > 2:
        fail("expected one selector or FROM TO")
    if len(tokens) == 2:
        start = parse_date(tokens[0], "FROM")
        end = parse_date(tokens[1], "TO")
        if end < start:
            fail("TO is earlier than FROM")
        return DateRange(start, end)
    token = tokens[0]
    if token == "today":
        return DateRange(base, base)
    if token == "tomorrow":
        tomorrow = base + dt.timedelta(days=1)
        return DateRange(tomorrow, tomorrow)
    if token == "week":
        start = base - dt.timedelta(days=base.weekday())
        return DateRange(start, start + dt.timedelta(days=6))
    if token == "month":
        start = base.replace(day=1)
        end = base.replace(day=calendar.monthrange(base.year, base.month)[1])
        return DateRange(start, end)
    match = RELATIVE_SELECTOR.fullmatch(token)
    if match:
        count = int(match.group(1))
        unit = match.group(2)
        if unit == "d":
            end_exclusive = base + dt.timedelta(days=count)
        elif unit == "w":
            end_exclusive = base + dt.timedelta(days=count * 7)
        else:
            end_exclusive = add_months(base, count)
        return DateRange(base, end_exclusive - dt.timedelta(days=1))
    date = parse_date(token)
    return DateRange(date, date)


def overlaps(event, date_range):
    return event.date <= date_range.end and event.last_date >= date_range.start


def event_sort_key(event):
    all_day_rank = 0 if event.start is None else 1
    start = event.start or dt.time.min
    return (event.date, all_day_rank, start, event.title.casefold(), event.key)


def display_time(event):
    if event.start is None:
        return "all-day"
    if event.end is None:
        return format_time(event.start)
    if event.end_date is None or event.end_date == event.date:
        return f"{format_time(event.start)}-{format_time(event.end)}"
    return f"{format_time(event.start)}->{event.end_date.isoformat()} {format_time(event.end)}"


AGENDA_TIME_WIDTH_MAX = 28
AGENDA_KEY_WIDTH_MAX = 24
AGENDA_LOCATION_WIDTH_MAX = 30


def agenda_column_width(values, header, maximum):
    """Return a practical minimum width without letting one value dominate."""
    longest = max([len(header), *(len(value) for value in values)], default=len(header))
    return min(longest, maximum)


def display_agenda_day(day, events):
    time_width = agenda_column_width(
        [display_time(event) for event in events], "TIME", AGENDA_TIME_WIDTH_MAX
    )
    key_width = agenda_column_width(
        [event.key for event in events], "KEY", AGENDA_KEY_WIDTH_MAX
    )
    location_width = agenda_column_width(
        [event.location or "-" for event in events],
        "LOCATION",
        AGENDA_LOCATION_WIDTH_MAX,
    )

    print(f"{day.strftime('%a')} {day.isoformat()}")
    print(
        f"  {'Time':<{time_width}}  {'Key':<{key_width}}  "
        f"{'Location':<{location_width}}  Title"
    )
    # print(
    #     f"  {'---':<{time_width}}  {'---':<{key_width}}  "
    #     f"{'---':<{location_width}}  ---"
    # )
    for event in events:
        print(
            f"  {display_time(event):<{time_width}}  "
            f"{event.key:<{key_width}}  "
            f"{(event.location or '-'):<{location_width}}  "
            f"{event.title}"
        )


def display_agenda(events, date_range):
    selected = sorted(
        (event for event in events if overlaps(event, date_range)),
        key=event_sort_key,
    )
    current = None
    day_events = []
    for event in selected:
        if current is None:
            current = event.date
        if event.date != current:
            display_agenda_day(current, day_events)
            print()
            current = event.date
            day_events = []
        day_events.append(event)
    if day_events:
        display_agenda_day(current, day_events)


def display_show(event):
    print(f"key:       {event.key}")
    print(f"title:     {event.title}")
    if event.end_date:
        print(f"date:      {event.date.isoformat()} - {event.end_date.isoformat()}")
    else:
        print(f"date:      {event.date.isoformat()}")
    print(f"time:      {display_time(event)}")
    if event.location:
        print(f"location:  {event.location}")
    if event.body:
        print()
        print(event.body, end="" if event.body.endswith("\n") else "\n")


def display_calendar(events, year, month):
    first = dt.date(year, month, 1)
    last = dt.date(year, month, calendar.monthrange(year, month)[1])
    month_range = DateRange(first, last)
    marked = set()
    for event in events:
        if not overlaps(event, month_range):
            continue
        start = max(event.date, first)
        end = min(event.last_date, last)
        day = start
        while day <= end:
            marked.add(day.day)
            day += dt.timedelta(days=1)
    print(f"{calendar.month_name[month]} {year}".center(27))
    print(" Mon Tue Wed Thu Fri Sat Sun")
    cal = calendar.Calendar(firstweekday=calendar.MONDAY)
    for week in cal.monthdayscalendar(year, month):
        cells = ["  "]
        for day in week:
            if day == 0:
                cells.append("    ")
            else:
                marker = "*" if day in marked else " "
                cells.append(f"{day:2d}{marker} ")
        print("".join(cells).rstrip())


def tags_from_args(args):
    return getattr(args, "tags", []) or []


def tag_match_from_args(args):
    return getattr(args, "tag_match", None)


def add_filter_options(parser):
    parser.add_argument("-t", "--tag", dest="tags", action="append", default=[])
    parser.add_argument("--tag-match", choices=("and", "or"))




def prompt_required(prompt, *, input_fn=input, output=sys.stderr):
    while True:
        value = input_fn(prompt).strip()
        if value:
            return value
        print("value is required", file=output)


def prompt_date_value(prompt, *, default=None, minimum=None, input_fn=input, output=sys.stderr):
    while True:
        raw = input_fn(prompt).strip()
        if not raw and default is not None:
            return default
        if not raw:
            return None
        try:
            value = parse_date(raw)
        except ScalError as exc:
            print(exc, file=output)
            continue
        if minimum is not None and value < minimum:
            print(f"date must not be earlier than {minimum.isoformat()}", file=output)
            continue
        return value


def prompt_yes_no(prompt, *, default=False, input_fn=input, output=sys.stderr):
    while True:
        raw = input_fn(prompt).strip().casefold()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("please answer y or n", file=output)


def prompt_time_value(prompt, *, field, required_start=None, start_date=None, end_date=None, input_fn=input, output=sys.stderr):
    while True:
        raw = input_fn(prompt).strip()
        if not raw:
            return None
        try:
            value = parse_time(raw, field)
        except ScalError as exc:
            print(exc, file=output)
            continue
        if field == "end":
            if required_start is None:
                print("end time requires a start time", file=output)
                continue
            effective_end_date = end_date or start_date
            if effective_end_date == start_date and value < required_start:
                print("end is earlier than start", file=output)
                continue
        return value


def prompt_interactive_event(key, *, input_fn=input, output=sys.stderr, today=None):
    today = today or dt.date.today()
    title = prompt_required("Title: ", input_fn=input_fn, output=output)
    start_date = prompt_date_value(
        f"Date [{today.isoformat()}]: ",
        default=today,
        input_fn=input_fn,
        output=output,
    )
    all_day = prompt_yes_no("All day? [y/N]: ", input_fn=input_fn, output=output)
    start = None
    if not all_day:
        start = prompt_time_value(
            "Start time (optional): ",
            field="start",
            input_fn=input_fn,
            output=output,
        )
    end_date = prompt_date_value(
        "End date (optional): ",
        minimum=start_date,
        input_fn=input_fn,
        output=output,
    )
    end = None
    if not all_day:
        end = prompt_time_value(
            "End time (optional): ",
            field="end",
            required_start=start,
            start_date=start_date,
            end_date=end_date,
            input_fn=input_fn,
            output=output,
        )
    location = input_fn("Location (optional): ").strip() or None
    event = Event(
        key=key,
        title=title,
        date=start_date,
        start=start,
        end_date=end_date,
        end=end,
        location=location,
    )
    validate_event(event)
    return event

def title_from_remainder(words):
    words = list(words)
    if words[:1] == ["--"]:
        words = words[1:]
    title = " ".join(words).strip()
    if not title:
        fail("missing title")
    return title


def cmd_add(args):
    validate_key(args.key)
    if args.date is None:
        if args.title:
            fail("interactive add does not accept a title argument without DATE")
        if args.start is not None or args.end is not None or args.end_date is not None or args.location is not None or args.all_day:
            fail("interactive add accepts only KEY and optional tag filters; enter schedule fields at the prompts")
        event = prompt_interactive_event(args.key)
    else:
        start_date = parse_date(args.date)
        start = parse_time(args.start, "start") if args.start is not None else None
        end = parse_time(args.end, "end") if args.end is not None else None
        end_date = parse_date(args.end_date, "end-date") if args.end_date else None
        if args.all_day and (start is not None or end is not None):
            fail("--all-day cannot be combined with --start or --end")
        event = Event(
            key=args.key,
            title=title_from_remainder(args.title),
            date=start_date,
            start=None if args.all_day else start,
            end_date=end_date,
            end=None if args.all_day else end,
            location=args.location,
        )
        validate_event(event)

    # Creation starts only after all interactive input has been collected and
    # validated, so Ctrl-C or EOF during prompts cannot leave an empty note.
    command = ["add", args.key]
    for tag in args.tags:
        command.extend(("-t", tag))
    run_sclipple(*command, capture=True)
    content = render_document(event)
    editor = callback_command("__write", args.key)
    process = subprocess.run(
        sclipple_command(args.key, editor=editor),
        input=content,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode:
        # Remove the empty note that add just created if initialization failed.
        run_sclipple("rm", args.key, check=False, capture=True)
        fail((process.stderr or "").strip() or "schedule initialization failed")
    print(args.key)


def cmd_set(args):
    validate_key(args.key)
    if args.all_day and (args.start is not None or args.end is not None):
        fail("--all-day cannot be combined with --start or --end")
    if args.clear_end and (args.end_date is not None or args.end is not None):
        fail("--clear-end cannot be combined with --end-date or --end")
    if args.clear_location and args.location is not None:
        fail("--clear-location cannot be combined with --location")
    updates = {}
    for attr, field in (
        ("title", "title"),
        ("date", "date"),
        ("start", "start"),
        ("end_date", "end-date"),
        ("end", "end"),
        ("location", "location"),
    ):
        value = getattr(args, attr)
        if value is not None:
            updates[field] = value
    if args.clear_end:
        updates["clear-end"] = "1"
    if args.clear_location:
        updates["location"] = ""
    if args.all_day:
        updates["all-day"] = "1"
    if not updates:
        fail("no changes requested")
    # Normalize and validate user time syntax before invoking the callback.
    if "start" in updates:
        updates["start"] = format_time(parse_time(updates["start"], "start"))
    if "end" in updates:
        updates["end"] = format_time(parse_time(updates["end"], "end"))
    editor = callback_command("__update", args.key, encode_updates(updates))
    run_sclipple(args.key, editor=editor)


def cmd_show(args):
    display_show(read_event(args.key))


def cmd_edit(args):
    validate_key(args.key)
    run_sclipple(args.key)


def cmd_search(args):
    try:
        pattern = re.compile(args.pattern, re.IGNORECASE)
    except re.error as exc:
        fail(f"invalid regular expression: {exc}")
    date_range = selector_range(args.selector) if args.selector else None
    events = read_events(tags_from_args(args), tag_match_from_args(args))
    matches = []
    for event in events:
        if date_range is not None and not overlaps(event, date_range):
            continue
        haystack = "\n".join(
            part for part in (event.key, event.title, event.location or "", event.body) if part
        )
        if pattern.search(haystack):
            matches.append(event)
    if date_range is None:
        if matches:
            date_range = DateRange(min(e.date for e in matches), max(e.last_date for e in matches))
        else:
            return
    display_agenda(matches, date_range)


def cmd_cal(args):
    if args.month is None:
        base = dt.date.today()
        year, month = base.year, base.month
    else:
        match = MONTH_SELECTOR.fullmatch(args.month)
        if not match:
            fail("calendar month must be YYYY-MM")
        year = int(match.group(1))
        month = int(match.group(2))
        if month < 1 or month > 12:
            fail("calendar month must be YYYY-MM")
    events = read_events(tags_from_args(args), tag_match_from_args(args))
    display_calendar(events, year, month)


def cmd_view(args):
    date_range = selector_range(args.selector)
    events = read_events(tags_from_args(args), tag_match_from_args(args))
    display_agenda(events, date_range)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="scal",
        description="calendar-aware facade over a dedicated sclipple store",
        epilog=(
            "Commands not owned by scal are forwarded to sclipple with the "
            "calendar --directory and --extension=md injected automatically. "
            "Use 'scal native ...' to force native forwarding when a name "
            "collides with a scal command or selector."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    add = sub.add_parser("add", help="add a calendar schedule")
    add.add_argument("key")
    add.add_argument("date", nargs="?")
    add.add_argument("-s", "--start")
    add.add_argument("-e", "--end")
    add.add_argument("--end-date")
    add.add_argument("-l", "--location")
    add.add_argument("--all-day", action="store_true")
    add.add_argument("-t", "--tag", dest="tags", action="append", default=[])
    add.add_argument("title", nargs="*")

    setp = sub.add_parser("set", help="change calendar metadata")
    setp.add_argument("key")
    setp.add_argument("--title")
    setp.add_argument("--date")
    setp.add_argument("-s", "--start")
    setp.add_argument("-e", "--end")
    setp.add_argument("--end-date")
    setp.add_argument("-l", "--location")
    setp.add_argument("--all-day", action="store_true")
    setp.add_argument("--clear-end", action="store_true")
    setp.add_argument("--clear-location", action="store_true")

    show = sub.add_parser("show", help="show one parsed calendar schedule")
    show.add_argument("key")

    edit = sub.add_parser("edit", help="edit one calendar Markdown note")
    edit.add_argument("key")

    search = sub.add_parser("search", help="search parsed schedule data")
    search.add_argument("pattern")
    search.add_argument("selector", nargs="*")
    add_filter_options(search)

    cal = sub.add_parser("cal", help="show a marker calendar")
    cal.add_argument("month", nargs="?")
    add_filter_options(cal)

    native = sub.add_parser("native", help="force direct sclipple pass-through", add_help=False)
    native.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def parse_view(argv):
    parser = argparse.ArgumentParser(prog="scal")
    parser.add_argument("selector", nargs="*")
    add_filter_options(parser)
    return parser.parse_args(argv)


def is_selector_token(token):
    if token in {"today", "tomorrow", "week", "month"}:
        return True
    if RELATIVE_SELECTOR.fullmatch(token):
        return True
    try:
        dt.date.fromisoformat(token)
        return True
    except ValueError:
        return False


def is_view_invocation(argv):
    """Return True only when argv is unambiguously a scal agenda request."""
    if not argv:
        return True
    first = argv[0]
    # Allow default-range filtering: scal -t work / scal --tag work.
    if first in {"-t", "--tag", "--tag-match"}:
        return True
    return is_selector_token(first)


def main(argv):
    # Internal --editor callbacks are never exposed as user commands.
    if argv[:1] == ["__capture"]:
        capture_callback(int(argv[1]), argv[2:])
        return 0
    if argv[:1] == ["__write"]:
        write_callback(argv[1], *argv[2:])
        return 0
    if argv[:1] == ["__update"]:
        update_callback(argv[1], argv[2], *argv[3:])
        return 0

    if argv[:1] in (["-h"], ["--help"]):
        build_parser().print_help()
        return 0

    # Universal escape hatch. Handle it before argparse so even native
    # options such as --help are forwarded rather than consumed by scal.
    if argv[:1] == ["native"]:
        if len(argv) == 1:
            fail("native requires a sclipple command, selector, or option")
        return run_native(argv[1:])

    # These are the only user-facing operations for which scal owns semantics.
    calendar_commands = {"add", "set", "show", "edit", "search", "cal"}
    if argv and argv[0] in calendar_commands:
        args = build_parser().parse_args(argv)
        handlers = {
            "add": cmd_add,
            "set": cmd_set,
            "show": cmd_show,
            "edit": cmd_edit,
            "search": cmd_search,
            "cal": cmd_cal,
        }
        handlers[args.command](args)
        return 0

    # Agenda syntax is recognized narrowly.  Everything else is a native
    # sclipple operation, so scal does not need to track sclipple's command set.
    if is_view_invocation(argv):
        args = parse_view(argv)
        cmd_view(args)
        return 0

    return run_native(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except ScalError as exc:
        print(f"scal: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except FileNotFoundError as exc:
        print(f"scal: command not found: {exc.filename}", file=sys.stderr)
        raise SystemExit(127)
    except KeyboardInterrupt:
        raise SystemExit(130)



