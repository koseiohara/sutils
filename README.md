# sutils

## Features

### `srun`

Stores shell command files with `sclipple` and executes one or more selected files in order. All selected files run in one shell process, so variables and the working directory are shared between them. Execution stops on an unhandled error. Commands inherit the caller's working directory and standard input.

Native `sclipple` commands and options remain available through `srun`.

Command files are stored in:

```text
~/.local/share/sclipple-run
```

### `scd`

Associates a key with a directory and changes the current interactive shell to the selected directory. Native `sclipple` commands and options remain available through `scd`. Selecting more than one directory is rejected.

Directory entries are stored in:

```text
~/.local/share/sclipple-cd
```

### `stodo`

Provides a task manager backed by `sclipple`. It supports task status, priority, due dates, tags, dependencies, notes, filtered views, statistics, and event history.

Task data is stored in:

```text
~/.local/share/sclipple-todo
```

## Dependencies

- [sclipple](https://github.com/koseiohara/sclipple) (>2.1.2)
- Python 3
- Bash or zsh
- A POSIX-compatible operating system

The Python implementation uses only the Python standard library.

## Usage

The shell files and Python modules may be installed at any absolute path. No particular installation directory, directory name, or installation-directory environment variable is required.

Source the loader from `~/.zshrc` or `~/.bashrc` using its actual path:

```sh
source "/actual/path/to/the/sutils-file"
```

The loader resolves `srun`, `scd`, and `stodo` from its own location.

Alternatively, source each wrapper using its actual path:

```sh
source "/actual/path/to/the/srun-file"
source "/actual/path/to/the/scd-file"
source "/actual/path/to/the/stodo-file"
```

Each wrapper resolves its Python module from its own location.

Open a new shell or reload the configuration file:

```sh
source ~/.zshrc
```

or:

```sh
source ~/.bashrc
```

### `srun`

#### Create command files

```sh
srun add KEY [KEY ...]
```

This creates one or more empty command files in:

```text
~/.local/share/sclipple-run
```

It does not immediately open the created files.

Example:

```sh
srun add build
```

#### Edit command files

```sh
srun edit KEY [KEY ...]
```

This opens the selected command files using the editor configured for `sclipple`.

Example:

```sh
srun edit build
```

A command file may begin with a description written as a shell comment:

```sh
# Configure, build, and install the project
./configure
make
make install
```

`srun ls` displays the first non-empty line:

```text
# Configure, build, and install the project
```

Because the line begins with `#`, Bash and zsh treat it as a comment during execution.

#### Execute command files

```sh
srun KEY [KEY ...]
```

Example:

```sh
srun build
```

Multiple command files may be selected:

```sh
srun prepare build install
```

The selected files are read after `sclipple` finishes. Their contents are joined in selection order and executed in one child shell.

This means:

- variables set by one selected file are visible to later selected files;
- `cd` in one selected file affects later selected files;
- execution begins in the directory where `srun` was invoked;
- standard input, standard output, and standard error are inherited;
- an unhandled failing command stops execution;
- the final exit status is returned by `srun`;
- changes to variables or directories do not affect the interactive parent shell;
- no temporary command file is created.

#### Select by tag

```sh
srun -t TAG
```

Multiple tags may be selected:

```sh
srun -t build -t local
```

Use the following to require every selected tag:

```sh
srun -t build -t local --tag-match and
```

#### Native `sclipple` operations

```sh
srun ls
```

Lists stored command files and displays their first non-empty lines.

```sh
srun show KEY [KEY ...]
```

Prints complete command-file contents without executing them.

```sh
srun search PATTERN [KEY ...]
```

Searches command-file contents.

```sh
srun rm KEY [KEY ...]
```

Removes selected command files.

```sh
srun mv OLD_KEY NEW_KEY
```

Renames a command-file key.

```sh
srun tag KEY [KEY ...] -t TAG
```

Adds tags.

```sh
srun untag KEY [KEY ...] -t TAG
```

Removes tags.

```sh
srun git GIT_ARGUMENTS...
```

Runs Git in the `srun` data store.

```sh
srun --help
srun --version
```

Prints `sclipple` help or version information.

### `scd`

#### Save the current directory

```sh
scd add KEY
```

This stores the current absolute directory path under `KEY`.

Example:

```sh
scd add project
```

#### Save another directory

```sh
scd add KEY DIRECTORY
```

Example:

```sh
scd add downloads "$HOME/Downloads"
```

The target must exist and must be a directory.

#### Change directory

```sh
scd KEY
```

The stored path is retrieved through `sclipple`. `scd` then changes the calling shell’s directory using `builtin cd`.

After a successful change, it prints the new working directory and runs `ls`.

Because `scd` is a shell function, the directory change remains active in the interactive shell.

#### Select by tag

```sh
scd -t TAG
```

Exactly one directory must be selected. If no directory or more than one directory is selected, `scd` returns an error without changing directory.

#### Edit a stored directory

```sh
scd edit KEY [KEY ...]
```

This opens the selected directory entries using the editor configured for `sclipple`.

The first line must contain the stored directory path.

#### Native `sclipple` operations

```sh
scd ls
```

Lists stored directory entries.

```sh
scd show KEY [KEY ...]
```

Prints stored paths without changing directory.

```sh
scd search PATTERN [KEY ...]
```

Searches stored directory entries.

```sh
scd rm KEY [KEY ...]
```

Removes saved entries. It does not remove the actual directories.

```sh
scd mv OLD_KEY NEW_KEY
```

Renames a saved-directory key without changing its path.

```sh
scd tag KEY [KEY ...] -t TAG
```

Adds tags to saved-directory entries.

```sh
scd untag KEY [KEY ...] -t TAG
```

Removes tags from saved-directory entries.

```sh
scd git GIT_ARGUMENTS...
```

Runs Git in the `scd` data store.

```sh
scd --help
scd --version
```

Prints `sclipple` help or version information.

### `stodo`

#### Initialize

```sh
stodo init
```

Creates the task store and its safety marker.

The default task store is:

```text
~/.local/share/sclipple-todo
```

#### Add a task

```sh
stodo add [OPTIONS] -- TITLE
```

Example:

```sh
stodo add -- Write the report
```

Specify a key:

```sh
stodo add -k report -- Write the report
```

Specify priority, due date, initial status, and tags:

```sh
stodo add \
    -k report \
    -p A \
    -d 2026-08-31 \
    -s inbox \
    -t school \
    -- Write the report
```

Defaults:

- priority: `B`
- due date: `-`
- status: `inbox`

If no key is supplied, a key is generated from the title.

#### Open tasks

```sh
stodo open [KEY ...]
```

Opens selected task files in the editor configured for `sclipple`.

#### List tasks

```sh
stodo ls [KEY ...]
```

Displays `sclipple` metadata for selected tasks.

#### Print raw task files

```sh
stodo raw [KEY ...]
```

Prints complete task-file contents.

#### Display task views

```sh
stodo agenda [KEY ...]
```

Displays every selected task except tasks with status `archived` or `trash`.

```sh
stodo inbox [KEY ...]
```

Displays tasks with status `inbox`.

```sh
stodo today [KEY ...]
```

Displays tasks with status `today`.

```sh
stodo waiting [KEY ...]
```

Displays tasks with status `waiting`.

```sh
stodo completed [KEY ...]
```

Displays tasks with status `done`.

```sh
stodo queued [KEY ...]
```

Displays tasks with status `next`.

```sh
stodo ready [KEY ...]
```

Displays `inbox`, `today`, or `next` tasks whose dependencies are complete.

```sh
stodo blocked [KEY ...]
```

Displays nonterminal tasks with missing or incomplete dependencies.

```sh
stodo overdue [KEY ...]
```

Displays nonterminal tasks whose valid ISO due date is earlier than the current local date.

```sh
stodo stats [KEY ...]
```

Prints total, ready, overdue, and per-status counts.

View output is sorted by:

1. priority;
2. due date;
3. key.

#### Change task status

```sh
stodo start SELECTOR...
```

Sets status to `today`.

```sh
stodo next SELECTOR...
```

Sets status to `next`.

```sh
stodo wait SELECTOR...
```

Sets status to `waiting`.

```sh
stodo done SELECTOR...
```

Sets status to `done` and records the completion timestamp.

```sh
stodo archive SELECTOR...
```

Sets status to `archived`.

```sh
stodo remove SELECTOR...
```

Sets status to `trash`. The task file is not deleted.

```sh
stodo restore SELECTOR...
```

Sets status to `next`.

These commands update `status_since` and append a history event.

#### Set the due date

```sh
stodo set-due VALUE SELECTOR...
```

Example:

```sh
stodo set-due 2026-08-31 report
```

Use `-` to remove the due date:

```sh
stodo set-due - report
```

#### Set priority

```sh
stodo set-priority VALUE SELECTOR...
```

Example:

```sh
stodo set-priority A report
```

#### Append a note

```sh
stodo note KEY "TEXT"
```

Example:

```sh
stodo note report "Draft completed"
```

The note is appended with the current timestamp.

#### Set dependencies

```sh
stodo depend KEY DEPENDENCY [DEPENDENCY ...]
```

Example:

```sh
stodo depend report research
```

This replaces the complete dependency list.

A dependency is complete when its status is one of:

- `done`
- `archived`
- `trash`

#### Remove dependencies

```sh
stodo undepend KEY
```

Replaces the dependency list with `-`.

#### Add tags

```sh
stodo tag TAG KEY [KEY ...]
```

Example:

```sh
stodo tag school report
```

#### Remove tags

```sh
stodo untag TAG KEY [KEY ...]
```

Example:

```sh
stodo untag school report
```

#### Permanently delete tasks

```sh
stodo purge SELECTOR...
```

This invokes `sclipple rm` and deletes the selected task notes.

Unlike `stodo remove`, this is not a status change.

#### Show event history

```sh
stodo history [KEY]
```

Without a key, all recorded events are printed. With a key, only that task’s events are printed.

#### Selectors

Commands accepting selectors may use keys:

```sh
stodo agenda report research
```

or tags:

```sh
stodo agenda -t school
```

Repeated tags use `or` by default:

```sh
stodo agenda -t school -t work
```

Require every tag with:

```sh
stodo agenda -t school -t work --tag-match and
```

Exact keys and unique key prefixes are accepted. Unknown or ambiguous prefixes are errors.

Read-only commands without selectors operate on all notes tagged `task`. Commands that modify or permanently delete tasks require an explicit key or tag selector.

#### Help

```sh
stodo --help
stodo COMMAND --help
```

The executable used for `sclipple` may be overridden:

```sh
export SCLIPPLE=/path/to/sclipple
```

## Summary

- No installation directory is fixed.
- No installation-directory environment variable is required.
- The loader and wrappers resolve their companion files from their own locations.
- `srun` and `scd` retain their fixed data directories.
- `stodo` uses its task data directory independently of the installation path.
