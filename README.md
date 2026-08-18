# sutils

## Features

### `srun`

Stores shell command files with `sclipple` and executes one or more selected
files in order. All selected files run in one shell process, so variables and
the working directory are shared between them. Execution stops on an unhandled
error. Commands inherit the caller's working directory and standard input.

Native `sclipple` commands and options remain available through `srun`.
Command files are stored in:

```text
~/.local/share/sclipple-run
```

### `scd`

Associates a key with a directory and changes the current interactive shell to
the selected directory. Native `sclipple` commands and options remain
available through `scd`. Selecting more than one directory is rejected.

Directory entries are stored in:

```text
~/.local/share/sclipple-cd
```

### `stodo`

Provides a task manager backed by `sclipple`. It supports task status,
priority, due dates, tags, dependencies, notes, filtered views, statistics,
and event history.

Task data is stored in:

```text
~/.local/share/sclipple-todo
```

## Dependencies

- `sclipple`
- Python 3
- Bash or zsh
- A POSIX-compatible operating system

The Python implementation uses only the Python standard library.

## Usage

Place the `sutils` directory anywhere, then source its loader from `~/.zshrc`
or `~/.bashrc` using the actual path:

```sh
source "/path/to/sutils/sutils"
```

Open a new shell or reload the configuration file:

```sh
source ~/.zshrc
```

or:

```sh
source ~/.bashrc
```

### `srun`

Create a command file:

```sh
srun add build
```

Edit it:

```sh
srun edit build
```

A useful command-file format is:

```sh
# Configure, build, and install the project
./configure
make
make install
```

The first non-empty line is shown by `srun ls`. A leading `#` makes the
description a shell comment, so it is not executed.

Run the command file:

```sh
srun build
```

Run several command files in order:

```sh
srun prepare build install
```

Use native `sclipple` operations:

```sh
srun ls
srun show build
srun search make
srun tag build -t project
srun git status
```

### `scd`

Save the current directory:

```sh
scd add project
```

Save another directory:

```sh
scd add downloads "$HOME/Downloads"
```

Change to a saved directory:

```sh
scd project
```

Edit the stored directory manually:

```sh
scd edit project
```

Use native `sclipple` operations:

```sh
scd ls
scd show project
scd tag project -t work
scd git status
```

### `stodo`

Initialize the task store once:

```sh
stodo init
```

Add and inspect tasks:

```sh
stodo add Write the report
stodo add -k report -p A -d 2026-08-31 -t school -- Write the report
stodo inbox
stodo agenda
stodo stats
```

Change task state and fields:

```sh
stodo start report
stodo set-priority A report
stodo set-due 2026-08-31 report
stodo note report "Draft completed"
stodo done report
stodo archive report
```

Manage tags and dependencies:

```sh
stodo tag school report
stodo untag school report
stodo depend report research
stodo undepend report
```

Show available commands or command-specific help:

```sh
stodo --help
stodo add --help
```

The loader and each individual wrapper resolve `sutil_modules` relative to
their own locations. The containing directory may be moved or renamed. After
moving it, update only the `source` line.

The executable used for `sclipple` can be overridden for all utilities:

```sh
export SCLIPPLE=/path/to/sclipple
```
