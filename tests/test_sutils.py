#!/usr/bin/env python3

import ast
import os
import pty
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
SHELLS = ("bash", "zsh")
WRAPPER_FILES = ("sutils", "srun", "scd", "stodo")


class TestEnvironment:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sutils-tests-")
        self.base = Path(self.temporary.name)
        self.root = self.base / "parent with spaces" / "renamed tools"
        self.home = self.base / "home with spaces"
        self.target = self.base / "target with spaces"
        self.todo = self.base / "todo data"

        self.root.mkdir(parents=True)
        self.home.mkdir()
        self.target.mkdir()
        self.todo.parent.mkdir(exist_ok=True)

        for name in (*WRAPPER_FILES, "README.md"):
            source = PROJECT_ROOT / name if name == "README.md" else SOURCE_ROOT / name
            shutil.copy2(source, self.root / name)
        shutil.copytree(SOURCE_ROOT / "sutil_modules", self.root / "sutil_modules")

        # Commands that intentionally open an editor must remain noninteractive
        # in CI. This is an ordinary sclipple configuration, not a wrapper hook.
        (self.home / ".sclipplerc").write_text(
            "editor = true\nextension = txt\n",
            encoding="utf-8",
        )

        sclipple = os.environ.get("SCLIPPLE") or shutil.which("sclipple")
        if not sclipple:
            raise unittest.SkipTest("set SCLIPPLE or install sclipple")

        self.env = os.environ.copy()
        self.env.update(
            {
                "HOME": str(self.home),
                "SCLIPPLE": str(Path(sclipple).resolve()),
                "STODO_DIR": str(self.todo),
                # The wrappers intentionally resolve their own locations with
                # cd -P.  macOS exposes temporary directories through /var,
                # while their physical path begins with /private/var.  Compare
                # against the same physical path representation.
                "SUTILS_TEST_ROOT": str(self.root.resolve()),
                "SUTILS_TEST_TARGET": str(self.target),
                # A stale legacy value must have no effect.
                "SUTILS_ROOT": "/definitely/not/the/install/directory",
            }
        )

    def close(self):
        self.temporary.cleanup()

    def run(self, shell, body, *, input_text=None, timeout=10):
        script = 'source "$SUTILS_TEST_ROOT/sutils" || exit\n' + body
        return subprocess.run(
            [shell, "-c", script],
            cwd=self.base,
            env=self.env,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )

    def run_individual(self, shell, wrapper, body, *, timeout=10):
        script = f'source "$SUTILS_TEST_ROOT/{wrapper}" || exit\n' + body
        return subprocess.run(
            [shell, "-c", script],
            cwd=self.base,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )

    def note(self, store, key, contents, *, extension="txt"):
        path = self.home / ".local" / "share" / store / "notes" / f"{key}.{extension}"
        path.write_text(contents, encoding="utf-8")


class SutilsTests(unittest.TestCase):
    def make_environment(self):
        environment = TestEnvironment()
        self.addCleanup(environment.close)
        return environment

    def assert_success(self, result):
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_static_syntax_and_standard_library_only(self):
        for shell in SHELLS:
            executable = shutil.which(shell)
            self.assertIsNotNone(executable, f"missing shell: {shell}")
            result = subprocess.run(
                [executable, "-n", *(str(SOURCE_ROOT / name) for name in WRAPPER_FILES)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assert_success(result)

        forbidden = (
            "SUTILS_ROOT",
            "$HOME/local/sutils",
            "~/local/sutils",
            "/any/directory/sutils",
        )
        for path in (
            *(SOURCE_ROOT / name for name in WRAPPER_FILES),
            PROJECT_ROOT / "README.md",
        ):
            contents = path.read_text(encoding="utf-8")
            for value in forbidden:
                self.assertNotIn(value, contents, f"{value!r} found in {path}")

        stdlib = {
            "argparse",
            "datetime",
            "os",
            "pathlib",
            "re",
            "shlex",
            "struct",
            "subprocess",
            "sys",
            "tempfile",
        }
        for path in (SOURCE_ROOT / "sutil_modules").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".", 1)[0])
            self.assertFalse(imports - stdlib, f"non-standard imports in {path}: {imports - stdlib}")

    def test_loader_and_individual_wrappers_are_relocatable(self):
        environment = self.make_environment()
        for shell in SHELLS:
            with self.subTest(shell=shell, mode="loader"):
                result = environment.run(
                    shell,
                    r'''
if test "$_SUTILS_SRUN_ROOT" != "$SUTILS_TEST_ROOT"; then
    printf 'srun root mismatch: expected <%s>, actual <%s>\n' \
        "$SUTILS_TEST_ROOT" "$_SUTILS_SRUN_ROOT" >&2
    exit 11
fi
if test "$_SUTILS_SCD_ROOT" != "$SUTILS_TEST_ROOT"; then
    printf 'scd root mismatch: expected <%s>, actual <%s>\n' \
        "$SUTILS_TEST_ROOT" "$_SUTILS_SCD_ROOT" >&2
    exit 12
fi
if test "$_SUTILS_STODO_ROOT" != "$SUTILS_TEST_ROOT"; then
    printf 'stodo root mismatch: expected <%s>, actual <%s>\n' \
        "$SUTILS_TEST_ROOT" "$_SUTILS_STODO_ROOT" >&2
    exit 13
fi
cd / || exit 14
type srun >/dev/null || exit 15
type scd >/dev/null || exit 16
type stodo >/dev/null || exit 17
stodo --help >/dev/null || exit 18
''',
                )
                self.assert_success(result)

            for wrapper in ("srun", "scd", "stodo"):
                with self.subTest(shell=shell, mode="individual", wrapper=wrapper):
                    result = environment.run_individual(
                        shell,
                        wrapper,
                        f'cd / || exit\ntype {wrapper} >/dev/null\n',
                    )
                    self.assert_success(result)

    def test_srun(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                environment = self.make_environment()

                result = environment.run(
                    shell,
                    "srun add first second input fail tail example >/dev/null\n",
                )
                self.assert_success(result)

                notes = environment.home / ".local/share/sclipple-run/notes"
                for key in ("first", "second", "input", "fail", "tail", "example"):
                    self.assertTrue(notes.joinpath(f"{key}.sh").is_file())
                    self.assertFalse(notes.joinpath(f"{key}.txt").exists())

                result = environment.run(shell, "srun edit first second >/dev/null\n")
                self.assert_success(result)

                environment.note(
                    "sclipple-run",
                    "first",
                    "# Set shared state\nvalue=shared\nprintf 'one:%s\\n' \"$value\"\n",
                    extension="sh",
                )
                environment.note(
                    "sclipple-run",
                    "second",
                    "printf 'two:%s\\n' \"$value\"\n",
                    extension="sh",
                )
                environment.note(
                    "sclipple-run",
                    "input",
                    "IFS= read -r input_line\nprintf 'input:%s\\n' \"$input_line\"\n",
                    extension="sh",
                )
                environment.note(
                    "sclipple-run",
                    "fail",
                    "printf 'before-failure\\n'\nfalse\n",
                    extension="sh",
                )
                environment.note(
                    "sclipple-run",
                    "tail",
                    "printf 'must-not-run\\n'\n",
                    extension="sh",
                )
                environment.note(
                    "sclipple-run",
                    "example",
                    "printf 'example:%s\\n' \"$PWD\"\n",
                    extension="sh",
                )

                result = environment.run(shell, "srun first second\n")
                self.assert_success(result)
                self.assertEqual(result.stdout, "one:shared\ntwo:shared\n")

                result = environment.run(shell, "srun input\n", input_text="hello\n")
                self.assert_success(result)
                self.assertEqual(result.stdout, "input:hello\n")

                result = environment.run(shell, "srun fail tail\n")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "before-failure\n")

                result = environment.run(shell, "srun show example\n")
                self.assert_success(result)
                self.assertIn("printf 'example:%s", result.stdout)
                self.assertNotIn(f"example:{environment.base}", result.stdout)

                result = environment.run(
                    shell,
                    r'''
srun search 'shared state' first >/dev/null || exit
srun tag first -t checked >/dev/null || exit
srun ls -t checked >/dev/null || exit
srun untag first -t checked >/dev/null || exit
srun add disposable >/dev/null || exit
srun mv disposable renamed >/dev/null || exit
srun rm renamed >/dev/null || exit
srun git init -q || exit
srun git status --short >/dev/null || exit
''',
                )
                self.assert_success(result)

                result = environment.run(shell, "srun --help >/dev/null\n")
                self.assert_success(result)

                result = environment.run(shell, "srun --directory=/tmp ls >/dev/null\n")
                self.assertEqual(result.returncode, 2)

                result = environment.run(shell, "srun --extension=txt ls >/dev/null\n")
                self.assertEqual(result.returncode, 2)

                # A pseudo-terminal keeps stdin open. This catches a regression
                # where the execution shell waited for another command forever.
                master, slave = pty.openpty()
                process = subprocess.Popen(
                    [
                        shell,
                        "-c",
                        'source "$SUTILS_TEST_ROOT/sutils"; '
                        'srun example; printf "returned:%s\\n" "$?"',
                    ],
                    cwd=environment.base,
                    env=environment.env,
                    stdin=slave,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                os.close(slave)
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                    self.fail("srun did not return after executing the command file")
                finally:
                    os.close(master)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertIn("returned:0", stdout)

    def test_scd(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                environment = self.make_environment()

                result = environment.run(
                    shell,
                    'scd add alpha "$SUTILS_TEST_TARGET" >/dev/null\n',
                )
                self.assert_success(result)

                result = environment.run(shell, "scd edit alpha >/dev/null\n")
                self.assert_success(result)

                result = environment.run(
                    shell,
                    r'''
before=$PWD
scd alpha >/dev/null || exit
test "$PWD" = "$SUTILS_TEST_TARGET" || exit 21
cd "$before" || exit
scd ls >/dev/null || exit
test "$PWD" = "$before" || exit 22
''',
                )
                self.assert_success(result)

                result = environment.run(shell, "scd show alpha\n")
                self.assert_success(result)
                self.assertIn(str(environment.target), result.stdout)

                result = environment.run(
                    shell,
                    r'''
scd add beta "$SUTILS_TEST_TARGET" >/dev/null || exit
scd tag alpha beta -t pair >/dev/null || exit
before=$PWD
scd -t pair >/dev/null 2>&1
result=$?
test "$result" -eq 1 || exit 23
test "$PWD" = "$before" || exit 24
scd search 'target with spaces' alpha >/dev/null || exit
scd untag beta -t pair >/dev/null || exit
scd mv beta gamma >/dev/null || exit
scd rm gamma >/dev/null || exit
scd git init -q || exit
scd git status --short >/dev/null || exit
test -d "$SUTILS_TEST_TARGET" || exit 25
''',
                )
                self.assert_success(result)

                result = environment.run(shell, "scd --help >/dev/null\n")
                self.assert_success(result)

                result = environment.run(shell, "scd --directory=/tmp ls >/dev/null\n")
                self.assertEqual(result.returncode, 2)

    def test_stodo(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                environment = self.make_environment()

                result = environment.run(shell, "stodo init >/dev/null\n")
                self.assert_success(result)

                result = environment.run(
                    shell,
                    r'''
stodo add -k research -p B -- Research sources >/dev/null || exit
stodo add -k report -p A -d 2099-12-31 -t school -- Write report >/dev/null || exit
stodo open report >/dev/null || exit
''',
                )
                self.assert_success(result)

                result = environment.run(shell, "stodo inbox research\n")
                self.assert_success(result)
                self.assertIn("research", result.stdout)

                result = environment.run(shell, "stodo depend report research\n")
                self.assert_success(result)
                result = environment.run(shell, "stodo blocked report\n")
                self.assert_success(result)
                self.assertIn("report", result.stdout)

                self.assert_success(environment.run(shell, "stodo done research\n"))
                result = environment.run(shell, "stodo ready report\n")
                self.assert_success(result)
                self.assertIn("report", result.stdout)

                commands = (
                    "stodo start report",
                    "stodo today report",
                    "stodo next report",
                    "stodo queued report",
                    "stodo wait report",
                    "stodo waiting report",
                    "stodo set-priority C report",
                    "stodo set-due 2000-01-01 report",
                    "stodo overdue report",
                    'stodo note report "Draft completed"',
                    "stodo tag urgent report",
                    "stodo ls -t urgent",
                    "stodo untag urgent report",
                    "stodo undepend report",
                    "stodo agenda report",
                    "stodo stats report",
                    "stodo done report",
                    "stodo completed report",
                    "stodo remove report",
                    "stodo restore report",
                    "stodo archive report",
                    "stodo raw report",
                    "stodo history report",
                )
                for command in commands:
                    with self.subTest(shell=shell, command=command):
                        result = environment.run(shell, command + "\n")
                        self.assert_success(result)

                result = environment.run(shell, "stodo raw report\n")
                self.assert_success(result)
                self.assertIn("status: archived", result.stdout)
                self.assertIn("Draft completed", result.stdout)

                result = environment.run(shell, "stodo purge report >/dev/null\n")
                self.assert_success(result)
                result = environment.run(shell, "stodo raw report >/dev/null 2>&1\n")
                self.assertNotEqual(result.returncode, 0)

                help_commands = (
                    "init add open ls raw agenda inbox today waiting completed queued "
                    "ready blocked overdue stats start next wait done archive remove "
                    "restore set-due set-priority note depend undepend tag untag purge history"
                ).split()
                for command in help_commands:
                    result = environment.run(shell, f"stodo {command} --help >/dev/null\n")
                    self.assert_success(result)


if __name__ == "__main__":
    unittest.main()
