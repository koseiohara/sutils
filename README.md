# sutils

A small collection of shell utilities built on top of
[`sclipple`](https://github.com/koseiohara/sclipple).

`sutils` provides four commands:

* `srun` — store and execute reusable shell snippets
* `scd` — store directories and jump to them
* `stodo` — manage structured tasks
* `scal` — manage and display calendar schedules

The commands use separate `sclipple` stores, so scripts, directories, tasks,
and calendar events do not share the same namespace.

`src/sutils` is a loader for Bash and zsh that exposes all four commands in
the current interactive shell.

---

## Requirements

`sutils` requires:

* Bash or zsh
* Python 3
* `sclipple` available as a command

No third-party Python packages are required by the `src/` implementation.

By default, the executable used for `sclipple` is:

```sh
sclipple
```

It can be overridden with the `SCLIPPLE` environment variable:

```sh
export SCLIPPLE=/path/to/sclipple
```

---

## Setup

Clone or otherwise place the repository at any location, then source
`src/sutils` from your shell configuration.

### zsh

Add this to `~/.zshrc`:

```sh
source /path/to/sutils/src/sutils
```

### Bash

Add this to `~/.bashrc`:

```sh
source /path/to/sutils/src/sutils
```

Reload the shell configuration afterward.

For zsh:

```sh
source ~/.zshrc
```

For Bash:

```sh
source ~/.bashrc
```

The loader locates `srun`, `scd`, `stodo`, and `scal` relative to its own
physical location, so the `src/` directory does not need to be installed at a
specific path.

Individual utilities may also be sourced separately:

```sh
source /path/to/sutils/src/srun
source /path/to/sutils/src/scd
source /path/to/sutils/src/stodo
source /path/to/sutils/src/scal
```

---

# `srun`

`srun` stores shell snippets in a dedicated `sclipple` store and executes the
selected note as a shell script.

Its store is fixed to:

```text
~/.local/share/sclipple-run
```

Its file extension is fixed to:

```text
sh
```

Both values are enforced by `sutils`.

Trying to override them with `--directory` or `--extension` is rejected.

## Basic usage

The arguments given to `srun` are normally passed to `sclipple`.

For example, a stored script can be selected with its key:

```sh
srun build
```

The selected note contents are collected from `sclipple` and executed using
the same shell family from which `srun` was invoked:

* Bash → `bash`
* zsh → `zsh`

If multiple notes are selected, their contents are concatenated before
execution.

## Editing a script

`srun` owns the special `edit` operation:

```sh
srun edit KEY
```

Multiple keys may be supplied:

```sh
srun edit first second
```

At least one key is required.

Internally, `edit` removes the `edit` word and invokes `sclipple` directly on
the selected script notes instead of executing their contents.

## Shell execution behavior

Stored scripts are executed in a new child Bash or zsh process.

The child shell is started with:

```text
-e -s
```

Therefore a failing simple command can terminate execution according to the
shell's `-e` semantics.

The original standard input is restored inside the executed script before the
stored commands run. This allows commands inside an `srun` note to read from
the terminal or other original stdin source.

For example, a stored script may contain:

```sh
read -r name
printf 'Hello, %s\n' "$name"
```

Because execution occurs in a child shell, shell-state changes made by a
stored script do not modify the calling interactive shell after `srun`
returns.

For example:

```sh
cd /tmp
export EXAMPLE=value
```

changes the directory and environment of the `srun` child process, not the
interactive parent shell.

This is different from `scd`, whose `cd` operation deliberately runs in the
calling shell.

---

# `scd`

`scd` associates `sclipple` keys with filesystem directories and changes the
directory of the current interactive shell.

Its store is fixed to:

```text
~/.local/share/sclipple-cd
```

Unlike `srun`, no file extension is forced.

The `--directory` option cannot be overridden by the caller.

## Add a directory

```sh
scd add KEY [DIRECTORY]
```

For example:

```sh
scd add project ~/src/project
```

If `DIRECTORY` is omitted, the current directory is stored:

```sh
cd ~/src/project
scd add project
```

The supplied directory is expanded and converted to an absolute normalized
path before being stored.

`~` expansion is supported:

```sh
scd add docs ~/Documents
```

Relative paths are resolved relative to the current logical working
directory.

For example:

```sh
cd ~/src
scd add project ./project
```

stores the absolute path corresponding to `~/src/project`.

The target must already exist and must be a directory.

## Symbolic links and logical paths

`scd add` distinguishes the shell's logical working directory from Python's
physical working directory.

When `$PWD` is:

* absolute, and
* refers to the same filesystem object as `os.getcwd()`

the logical `$PWD` spelling is preserved.

This matters when the current path contains symbolic links.

For example, suppose:

```text
~/work/current -> /data/projects/current
```

and the shell is logically located at:

```sh
~/work/current
```

When that logical path and the physical path refer to the same directory,
`scd add` uses the logical path as its base rather than unconditionally
collapsing the symbolic link to `/data/projects/current`.

If `$PWD` cannot safely be verified, the physical working directory is used
instead.

## Jump to a stored directory

Select a directory using the normal `sclipple` selection syntax exposed by
`scd`:

```sh
scd KEY
```

After `sclipple` selects the note, the Python helper reads the stored
directory and returns it to the shell wrapper.

The actual directory change is then performed by:

```sh
builtin cd -- "$directory"
```

inside the shell function.

This design is necessary because an external Python process cannot change
the working directory of its parent interactive shell.

After a successful change, `scd` prints:

```text
cd /resulting/path
```

## Exactly one directory must be selected

A normal `scd` operation must resolve to exactly one stored directory.

If multiple notes are selected, `scd` reports:

```text
sutils: multiple directories selected:
...
```

and does not change directory.

The stored note must also contain a non-empty first line encoded as UTF-8.

Only the first line is interpreted as the target directory.

## Editing entries

```sh
scd edit KEY
```

Multiple entries may be supplied:

```sh
scd edit foo bar
```

At least one key is required.

---

# `stodo`

`stodo` is a task-aware facade over a dedicated `sclipple` store.

Unlike `srun` and `scd`, it defines structured task semantics rather than
simply executing or interpreting arbitrary notes.

The default store is:

```text
~/.local/share/sclipple-todo
```

The store can be changed with:

```sh
export STODO_DIR=/absolute/path/to/todo-store
```

`STODO_DIR` must resolve to an absolute path.

The task note extension is fixed to:

```text
txt
```

Every task created through `stodo add` automatically receives the `sclipple`
tag:

```text
task
```

Additional tags may also be attached.

---

## Add a task

```sh
stodo add [OPTIONS] TITLE...
```

Options:

```text
-k, --key KEY
-d, --due DATE
-p, --priority {A,B,C}
-t, --tag TAG
```

Example:

```sh
stodo add Finish the report
```

With metadata:

```sh
stodo add \
    -k report \
    -d 2026-08-31 \
    -p A \
    -t work \
    Finish the report
```

The default values are:

```text
due:      -
priority: B
```

`-` means no due date.

Because the title consumes the remaining command-line arguments, task options
should be placed before the title.

---

## Task keys

A key may contain only:

```text
A-Z
a-z
0-9
_
-
```

If `--key` is omitted, `stodo` derives a key from the title.

The generated key is:

1. converted to lowercase,
2. converted to ASCII where possible,
3. stripped of non-alphanumeric groups,
4. joined with `-`,
5. limited to 48 characters.

For example:

```text
Finish the report
```

becomes approximately:

```text
finish-the-report
```

If that key already exists, suffixes are added:

```text
finish-the-report-2
finish-the-report-3
...
```

If the title produces no usable ASCII slug, a fallback key is generated:

```text
task-1
task-2
...
```

This can occur, for example, with a title consisting entirely of characters
that disappear during ASCII conversion.

The generated or supplied key is printed after successful creation.

---

## Due dates

CLI due dates accept four forms:

```text
-
DD
MM-DD
YYYY-MM-DD
```

Examples:

```sh
stodo add -d - No deadline
stodo add -d 25 Finish this month
stodo add -d 09-15 September task
stodo add -d 2026-12-01 December task
```

Interpretation is based on the local current date:

* `DD` uses the current year and current month.
* `MM-DD` uses the current year.
* `YYYY-MM-DD` is used directly.
* `-` means no due date.

The resulting date is stored in ISO form:

```text
YYYY-MM-DD
```

The date must be valid. There is no automatic rollover to a future month or
future year.

For example, if the current month is August:

```text
05
```

means the fifth day of August of the current year, even if that date has
already passed.

---

## Priorities

Three priorities are supported:

```text
A
B
C
```

The default priority is:

```text
B
```

Task listings sort primarily by priority in this order:

```text
A
B
C
```

Within a priority, dated tasks sort before tasks without a due date, followed
by the task key.

---

## List tasks

```sh
stodo ls
```

By default, only incomplete tasks are displayed.

The normal output columns are:

```text
P  DUE         KEY                       TITLE
```

### Include completed tasks

```sh
stodo ls --all
```

When `--all` is used, an additional state column is displayed.

### Only completed tasks

```sh
stodo ls --done
```

### Overdue tasks

```sh
stodo ls --overdue
```

A task is considered overdue only when:

* it is incomplete,
* it has a due date, and
* its due date is earlier than today.

A task due today is not treated as overdue.

### Filter by exact due date

```sh
stodo ls --due 2026-08-31
```

The abbreviated date forms accepted by `stodo add` are also accepted.

### Filter by priority

```sh
stodo ls --priority A
```

### Select keys

```sh
stodo ls foo bar
```

### Filter by tags

```sh
stodo ls -t work
```

Multiple tags can be supplied:

```sh
stodo ls -t work -t urgent
```

The `sclipple` tag matching mode can be selected explicitly:

```sh
stodo ls -t work -t urgent --tag-match and
```

or:

```sh
stodo ls -t work -t urgent --tag-match or
```

With no explicit keys or tags, `stodo ls` selects notes carrying the automatic
`task` tag.

---

## Show a task

```sh
stodo show KEY
```

Example output contains:

```text
key:
title:
created:
due:
priority:
completed:
```

If the task contains a body after its metadata section, that body is printed
after the structured fields.

Keys may be abbreviated to a unique prefix.

For example, if the only matching key is:

```text
finish-report
```

then:

```sh
stodo show fin
```

resolves to that task.

If a prefix matches multiple keys, `stodo` reports it as ambiguous rather than
choosing one.

---

## Update task metadata

```sh
stodo set KEY [OPTIONS]
```

Available changes:

```text
--title TITLE
--due DATE
--clear-due
--priority {A,B,C}
```

Examples:

```sh
stodo set report --priority A
```

```sh
stodo set report --due 2026-09-01
```

```sh
stodo set report --clear-due
```

```sh
stodo set report --title "Final report"
```

At least one actual change must be requested.

---

## Complete tasks

```sh
stodo done KEY [KEY ...]
```

Example:

```sh
stodo done report slides
```

Completion is stored as a local timezone-aware timestamp.

---

## Reopen tasks

```sh
stodo reopen KEY [KEY ...]
```

Reopening a task restores:

```text
completed: -
```

---

## Task file format

A task is stored as a UTF-8 text note with structured metadata followed by a
body.

Conceptually:

```text
title: Finish the report
created: 2026-08-20T15:00:00+0900
due: 2026-08-31
priority: A
completed: -
---
Optional free-form body...
```

The five required metadata fields are:

```text
title
created
due
priority
completed
```

The separator:

```text
---
```

marks the start of the free-form body.

`stodo` validates this structure whenever it parses or modifies the task.

---

## Native `sclipple` operations

`stodo` owns only these command names:

```text
add
ls
show
set
done
reopen
```

Other invocations are passed directly to `sclipple` while automatically
injecting the task store and `.txt` extension.

For example, a `sclipple` operation not owned by `stodo` may be invoked as:

```sh
stodo SOME_SCLIPPLE_OPERATION ...
```

If a native `sclipple` command collides with a command owned by `stodo`, use:

```sh
stodo native ...
```

The following cannot be overridden:

```text
--directory
--extension
```

---

# `scal`

`scal` is a calendar-aware facade over another dedicated `sclipple` store.

The default store is:

```text
~/.local/share/sclipple-calendar
```

It can be changed with:

```sh
export SCAL_DIR=/absolute/path/to/calendar-store
```

The extension is fixed to:

```text
md
```

Calendar notes are structured Markdown documents.

---

## Add an event

There are two modes.

### Non-interactive

```sh
scal add KEY DATE [OPTIONS] TITLE...
```

Example:

```sh
scal add meeting 2026-08-24 \
    --start 13:00 \
    --end 14:30 \
    --location "Room A" \
    Project meeting
```

Supported options are:

```text
-s, --start TIME
-e, --end TIME
--end-date YYYY-MM-DD
-l, --location LOCATION
--all-day
-t, --tag TAG
```

Dates used by `scal` must be full ISO dates:

```text
YYYY-MM-DD
```

Unlike `stodo`, abbreviated `DD` and `MM-DD` forms are not accepted.

### Interactive

If only the key is supplied:

```sh
scal add meeting
```

`scal` enters an interactive creation flow and prompts for the event fields.

Interactive creation collects and validates all required input before the
underlying note is created. Cancelling during the prompts therefore does not
intentionally leave an empty calendar note.

---

## Time syntax

Times accept:

```text
H
HH
H:MM
HH:MM
```

Examples:

```text
9
09
9:30
09:30
23:59
```

Internally they are normalized for storage and display to:

```text
HH:MM
```

Hours must be in the range `0..23`, and minutes must be in `0..59`.

---

## All-day events

An event without a start time is treated as an all-day event.

It can also be requested explicitly:

```sh
scal add holiday 2026-09-21 --all-day Autumn holiday
```

`--all-day` cannot be combined with `--start` or `--end`.

---

## End times and multi-day events

An end time requires a start time.

For example, this is valid:

```sh
scal add meeting 2026-08-24 \
    --start 10:00 \
    --end 11:00 \
    Meeting
```

A multi-day event can specify `--end-date`:

```sh
scal add conference 2026-09-10 \
    --end-date 2026-09-12 \
    Conference
```

A timed multi-day event may include both dates and times:

```sh
scal add trip 2026-09-10 \
    --start 18:00 \
    --end-date 2026-09-11 \
    --end 08:00 \
    Overnight trip
```

The end date may not be earlier than the start date.

When start and end are on the same date, the end time may not be earlier than
the start time.

---

## Calendar Markdown format

A calendar entry is represented as a Markdown document.

Example:

```md
# Project meeting

date: 2026-08-24
start: 13:00
end: 14:30
location: Room A

Optional notes about the meeting.
```

A multi-day entry may contain:

```md
# Conference

date: 2026-09-10
end-date: 2026-09-12

Conference notes.
```

The first line must be:

```text
# TITLE
```

The required metadata field is:

```text
date
```

Optional fields are:

```text
start
end-date
end
location
```

Unknown or duplicate metadata fields are rejected by the parser.

The remaining text is the free-form event body.

---

## View the agenda

Running `scal` without arguments displays the default range:

```sh
scal
```

The default selector is:

```text
1w
```

meaning the period beginning today and covering the next seven days including
today.

The agenda can also be selected explicitly.

### Today

```sh
scal today
```

### Tomorrow

```sh
scal tomorrow
```

### Current week

```sh
scal week
```

`week` means Monday through Sunday of the current week.

### Current month

```sh
scal month
```

### Relative ranges

```sh
scal 3d
scal 2w
scal 1m
```

The supported units are:

```text
d  days
w  weeks
m  months
```

Relative ranges begin today.

For example:

```sh
scal 3d
```

covers today plus the following two days.

### Exact date

```sh
scal 2026-08-24
```

### Explicit range

```sh
scal 2026-08-24 2026-08-31
```

The ending date may not be earlier than the starting date.

---

## Agenda ordering

Selected events are ordered by:

1. start date,
2. all-day events before timed events,
3. start time,
4. case-insensitive title,
5. key.

The agenda groups entries by their start date and displays columns for:

```text
Time
Key
Location
Title
```

---

## Filter the calendar by tags

Agenda views accept `sclipple` tags:

```sh
scal -t work
```

or:

```sh
scal week -t work
```

Multiple tags can be used:

```sh
scal month -t work -t important --tag-match and
```

Supported matching modes are:

```text
and
or
```

---

## Show an event

```sh
scal show KEY
```

This prints parsed event information including:

```text
key
title
date
time
location
```

followed by the body when present.

---

## Edit an event

```sh
scal edit KEY
```

This invokes the underlying `sclipple` note editing behavior for the selected
Markdown schedule.

The edited document must remain compatible with the calendar format for
structured `scal` operations to parse it successfully.

---

## Update event metadata

```sh
scal set KEY [OPTIONS]
```

Supported options:

```text
--title TITLE
--date YYYY-MM-DD
-s, --start TIME
-e, --end TIME
--end-date YYYY-MM-DD
-l, --location LOCATION
--all-day
--clear-end
--clear-location
```

Examples:

```sh
scal set meeting --start 14:00 --end 15:00
```

```sh
scal set meeting --location "Room B"
```

```sh
scal set meeting --clear-location
```

```sh
scal set meeting --clear-end
```

```sh
scal set meeting --all-day
```

`--clear-end` clears both the end date and end time.

`--all-day` clears the start and end times.

Conflicting combinations are rejected, including:

```text
--all-day with --start or --end
--clear-end with --end-date or --end
--clear-location with --location
```

At least one change must be requested.

---

## Search calendar entries

```sh
scal search REGEX
```

Search is case-insensitive and uses Python regular-expression syntax.

The pattern is matched against the combined:

```text
key
title
location
body
```

of each parsed calendar entry.

Example:

```sh
scal search meeting
```

A date selector can restrict the search:

```sh
scal search meeting month
```

```sh
scal search 'seminar|meeting' 2026-09-01 2026-09-30
```

Tag filters are also supported:

```sh
scal search meeting month -t work
```

---

## Monthly calendar

```sh
scal cal
```

displays the current month.

A specific month may be given as:

```sh
scal cal 2026-09
```

Days containing one or more events are marked with:

```text
*
```

Multi-day events mark every overlapping day in the displayed month.

Tag filtering is supported:

```sh
scal cal 2026-09 -t work
```

---

## Native `sclipple` operations

`scal` owns these command names:

```text
add
set
show
edit
search
cal
```

It also recognizes calendar selector syntax such as:

```text
today
tomorrow
week
month
1d
2w
1m
YYYY-MM-DD
```

Anything that is neither a `scal` command nor an unambiguous calendar view is
forwarded to `sclipple`.

The forwarded command automatically receives:

```text
--directory=<SCAL_DIR>
--extension=md
```

To explicitly bypass `scal` command/selector interpretation, use:

```sh
scal native ...
```

For example, `native` is useful when a `sclipple` operation happens to have a
name that would otherwise be interpreted by `scal`.

---

# Storage overview

The default stores are:

| Utility | Store                              | Extension  |
| ------- | ---------------------------------- | ---------- |
| `srun`  | `~/.local/share/sclipple-run`      | `sh`       |
| `scd`   | `~/.local/share/sclipple-cd`       | not forced |
| `stodo` | `~/.local/share/sclipple-todo`     | `txt`      |
| `scal`  | `~/.local/share/sclipple-calendar` | `md`       |

`STODO_DIR` can override the `stodo` store.

`SCAL_DIR` can override the `scal` store.

`SCLIPPLE` can override the `sclipple` executable used by all Python-backed
utilities.

The `srun` and `scd` store paths are fixed by their current implementation.

---

# Architecture

The implementation is split between shell functions and Python helpers.

```text
src/
├── sutils
├── srun
├── scd
├── stodo
├── scal
└── sutil_modules/
    ├── sutils_core.py
    ├── stodo.py
    └── scal.py
```

## Shell layer

The files:

```text
sutils
srun
scd
stodo
scal
```

are intended to be sourced into Bash or zsh.

`srun`, `stodo`, and `scal` primarily delegate their work to Python.

`scd` is different: Python selects and validates the saved directory, but the
final `cd` remains in the shell function so that the caller's current working
directory actually changes.

## Python layer

`sutils_core.py` implements the shared lower-level behavior for:

```text
srun
scd
```

including:

* calling `sclipple`,
* capturing selected note contents through an editor callback,
* executing `srun` records,
* validating and storing `scd` directories,
* communicating the selected directory back to the shell.

`stodo.py` implements structured task semantics.

`scal.py` implements structured calendar semantics.

Both `stodo` and `scal` intentionally leave general note selection and
management to `sclipple` instead of reproducing its storage abstraction.

---

# Exit behavior

The Python utilities preserve meaningful nonzero exit statuses where possible.

Notable behavior includes:

```text
127  required executable/shell not found
130  interrupted with Ctrl-C
```

Validation and malformed-data errors return nonzero status and print an error
message to stderr.

`srun` also returns the exit status of the child shell that executes the
selected script.

---

# Summary

`sutils` builds four specialized workflows on top of `sclipple`:

* use `srun` for reusable Bash/zsh snippets;
* use `scd` for persistent directory shortcuts that change the current shell;
* use `stodo` for structured tasks with due dates, priorities, completion
  state, and tags;
* use `scal` for structured Markdown calendar entries, agenda views, searches,
  and month displays.

All four utilities retain `sclipple` as the underlying note-selection and
storage mechanism while adding only the semantics required for their
respective use cases.


