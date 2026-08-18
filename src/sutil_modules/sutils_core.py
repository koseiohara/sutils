#!/usr/bin/env python3

import argparse
import os
import shlex
import struct
import subprocess
import sys
from pathlib import Path


SCLIPPLE = os.environ.get("SCLIPPLE", "sclipple")
SRUN_DIR = Path.home() / ".local/share/sclipple-run"
SRUN_EXTENSION = "sh"
SCD_DIR = Path.home() / ".local/share/sclipple-cd"
SELF = str(Path(__file__).resolve())
CAPTURE_MAGIC = b"SUTILS-CAPTURE-V1\0"
SCD_RESULT_MARKER = "__SCD_RESULT_V1__"


def error(message):
    print(f"sutils: {message}", file=sys.stderr)


def fixed_options_are_valid(arguments, *, extension_is_fixed=False):
    for argument in arguments:
        if argument == "git":
            break
        if argument == "--directory" or argument.startswith("--directory="):
            error("the sclipple directory is fixed")
            return False
        if extension_is_fixed and (
            argument == "--extension" or argument.startswith("--extension=")
        ):
            error(f"the srun extension is fixed to {SRUN_EXTENSION}")
            return False
    return True


def callback_command(name, *args):
    words = [sys.executable, SELF, name, *map(str, args)]
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
        raise ValueError("invalid response from capture callback")

    position = len(CAPTURE_MAGIC)
    if len(data) < position + 4:
        raise ValueError("truncated capture response")

    count = struct.unpack_from("!I", data, position)[0]
    position += 4
    records = []

    for _ in range(count):
        if len(data) < position + 8:
            raise ValueError("truncated capture response")

        size = struct.unpack_from("!Q", data, position)[0]
        position += 8
        end = position + size

        if end > len(data):
            raise ValueError("truncated capture response")

        records.append(data[position:end])
        position = end

    if position != len(data):
        raise ValueError("unexpected data after capture response")

    return records


def run_sclipple(storage, arguments, *, editor=None, extension=None, pass_fds=()):
    command = [SCLIPPLE, f"--directory={storage}"]
    if extension is not None:
        command.append(f"--extension={extension}")
    if editor is not None:
        command.append(f"--editor={editor}")
    command.extend(arguments)
    return subprocess.run(command, pass_fds=pass_fds).returncode


def run_sclipple_capture(storage, arguments, *, extension=None):
    read_fd, write_fd = os.pipe()
    editor = callback_command("__capture", write_fd)

    try:
        command = [SCLIPPLE, f"--directory={storage}"]
        if extension is not None:
            command.append(f"--extension={extension}")
        command.extend((f"--editor={editor}", *arguments))
        process = subprocess.Popen(command, pass_fds=(write_fd,))
    except Exception:
        os.close(read_fd)
        os.close(write_fd)
        raise

    os.close(write_fd)
    try:
        with os.fdopen(read_fd, "rb", closefd=True) as source:
            captured = source.read()
    finally:
        status = process.wait()

    return status, decode_capture(captured)


def run_script(shell, records):
    script = b"\n".join(records)
    if records:
        script += b"\n"

    # The shell reads the whole brace group from its standard input before it
    # executes the first command. That command restores the user's original
    # stdin, so commands in stored notes can still read interactively. This
    # avoids temporary files and does not depend on /dev/fd being mounted.
    original_stdin = os.dup(0)
    wrapped_script = (
        b"{\nexec 0<&"
        + str(original_stdin).encode("ascii")
        + b"\n"
        + script
        + b"exit $?\n"
        + b"}\n"
    )

    try:
        process = subprocess.Popen(
            [shell, "-e", "-s"],
            stdin=subprocess.PIPE,
            pass_fds=(original_stdin,),
        )
    except FileNotFoundError:
        os.close(original_stdin)
        error(f"shell not found: {shell}")
        return 127
    except Exception:
        os.close(original_stdin)
        raise

    os.close(original_stdin)
    try:
        process.stdin.write(wrapped_script)
        process.stdin.close()
    except BrokenPipeError:
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass
    return process.wait()


def srun(arguments, shell):
    SRUN_DIR.mkdir(parents=True, exist_ok=True)

    if not fixed_options_are_valid(arguments, extension_is_fixed=True):
        return 2

    if arguments[:1] == ["edit"]:
        if len(arguments) == 1:
            error("usage: srun edit KEY [KEY ...]")
            return 2
        return run_sclipple(
            SRUN_DIR,
            arguments[1:],
            extension=SRUN_EXTENSION,
        )

    status, records = run_sclipple_capture(
        SRUN_DIR,
        arguments,
        extension=SRUN_EXTENSION,
    )
    if status != 0:
        return status

    if records is None:
        return 0

    return run_script(shell, records)


def write_directory_callback(target, files):
    if len(files) != 1:
        raise ValueError("directory writer expects exactly one note file")
    Path(files[0]).write_text(target + "\n", encoding="utf-8")


def scd_add(arguments):
    if len(arguments) < 2:
        error("usage: scd add KEY [DIRECTORY]")
        return 2

    key = arguments[1]
    target_input = arguments[2] if len(arguments) >= 3 else os.getcwd()
    target = os.path.abspath(os.path.expanduser(target_input))

    if not os.path.isdir(target):
        error(f"not a directory: {target_input}")
        return 1

    status = run_sclipple(SCD_DIR, ["add", key])
    if status != 0:
        return status

    editor = callback_command("__write_directory", target)
    return run_sclipple(SCD_DIR, [key], editor=editor)


def decode_directory(record):
    try:
        text = record.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("stored directory is not valid UTF-8") from exc

    lines = text.splitlines()
    if not lines or not lines[0]:
        raise ValueError("stored directory is empty")
    return lines[0]


def write_scd_result(fd, directory):
    data = f"{SCD_RESULT_MARKER}\n{directory}".encode("utf-8")
    if fd is None:
        sys.stdout.buffer.write(data + b"\n")
        sys.stdout.buffer.flush()
        return

    os.write(fd, data)


def scd(arguments, result_fd):
    SCD_DIR.mkdir(parents=True, exist_ok=True)

    if not fixed_options_are_valid(arguments):
        return 2

    if arguments[:1] == ["add"]:
        return scd_add(arguments)

    if arguments[:1] == ["edit"]:
        if len(arguments) == 1:
            error("usage: scd edit KEY [KEY ...]")
            return 2
        return run_sclipple(SCD_DIR, arguments[1:])

    status, records = run_sclipple_capture(SCD_DIR, arguments)
    if status != 0:
        return status

    if records is None:
        return 0

    directories = [decode_directory(record) for record in records]
    if len(directories) != 1:
        error("multiple directories selected:")
        for directory in directories:
            print(directory, file=sys.stderr)
        return 1

    write_scd_result(result_fd, directories[0])
    return 0


def parse_arguments(argv):
    parser = argparse.ArgumentParser(prog="sutils-core")
    subparsers = parser.add_subparsers(dest="utility", required=True)

    run_parser = subparsers.add_parser("srun")
    run_parser.add_argument("--shell", required=True)
    run_parser.add_argument("arguments", nargs=argparse.REMAINDER)

    cd_parser = subparsers.add_parser("scd")
    cd_parser.add_argument("--result-fd", type=int)
    cd_parser.add_argument("arguments", nargs=argparse.REMAINDER)

    parsed = parser.parse_args(argv)
    if parsed.arguments[:1] == ["--"]:
        parsed.arguments = parsed.arguments[1:]
    return parsed


def main(argv):
    if argv[:1] == ["__capture"]:
        capture_callback(int(argv[1]), argv[2:])
        return 0

    if argv[:1] == ["__write_directory"]:
        write_directory_callback(argv[1], argv[2:])
        return 0

    parsed = parse_arguments(argv)
    if parsed.utility == "srun":
        return srun(parsed.arguments, parsed.shell)
    return scd(parsed.arguments, parsed.result_fd)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, ValueError) as exc:
        error(str(exc))
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)
