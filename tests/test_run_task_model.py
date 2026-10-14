"""Focused tests for the single-cell Hermes process boundary."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_task_model


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "fixtures" / "fin-01"


def _fake_hermes_install(root: Path) -> Path:
    """A PATH entry whose 'hermes' launcher sits next to a fake python that echoes its argv."""
    bin_dir = root / "bin"
    bin_dir.mkdir()
    launcher = bin_dir / "hermes"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    python = bin_dir / "python3"
    python.write_text(
        "#!/bin/sh\n"
        f'"{sys.executable}" -c \'import json, sys; print(json.dumps({{"argv": sys.argv[1:]}}))\' "$@"\n',
        encoding="utf-8",
    )
    python.chmod(python.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def _run_cell(temp_dir: Path, run_id: str, env: dict[str, str], *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, str(ROOT / "scripts" / "run_task_model.py"), "--task", "FIN-01",
            "--fixture", str(PACKAGE / "input.json"), "--prompt", str(PACKAGE / "prompt.txt"),
            "--output-root", str(temp_dir / "evidence"), "--run-id", run_id,
            "--model-requested", "test/model", *extra,
        ],
        cwd=ROOT, check=False, capture_output=True, text=True, env=env,
    )


class RunTaskModelTests(unittest.TestCase):
    def test_default_command_skips_shell_wrapper_and_uses_python_backed_hermes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            wrapper_directory = root / "wrapper-bin"
            real_directory = root / "real-bin"
            wrapper_directory.mkdir()
            real_directory.mkdir()

            wrapper = wrapper_directory / "hermes"
            wrapper.write_text(
                "#!/usr/bin/env bash\nexec /path/to/real/hermes \"$@\"\n",
                encoding="utf-8",
            )
            wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)

            real_launcher = real_directory / "hermes"
            real_launcher.write_text("#!/bin/sh\n", encoding="utf-8")
            real_launcher.chmod(real_launcher.stat().st_mode | stat.S_IXUSR)
            hermes_python = real_directory / "python3"
            hermes_python.write_text("", encoding="utf-8")
            hermes_python.chmod(hermes_python.stat().st_mode | stat.S_IXUSR)

            path = os.pathsep.join((str(wrapper_directory), str(real_directory)))
            with patch.dict(os.environ, {"PATH": path}, clear=False):
                command = run_task_model._default_agent_command(root / "home")

            self.assertEqual(command[0], str(hermes_python.resolve()))
            self.assertNotIn("bash", Path(command[0]).name)
            self.assertEqual(command[1], str(run_task_model.ROOT / "scripts" / "hermes_no_tools.py"))
            self.assertEqual(command[2:], ["--hermes-home", str(root / "home")])

    def test_hermes_home_comes_from_flag_or_environment_and_is_never_recorded(self) -> None:
        for source in ("flag", "environment"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                temp_dir = Path(directory)
                bin_dir = _fake_hermes_install(temp_dir)
                hermes_home = temp_dir / "credentials-home"
                hermes_home.mkdir()
                env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
                env.pop("STAFFBENCH_HERMES_HOME", None)
                extra: list[str] = []
                if source == "flag":
                    extra = ["--hermes-home", str(hermes_home)]
                else:
                    env["STAFFBENCH_HERMES_HOME"] = str(hermes_home)
                result = _run_cell(temp_dir, f"home-{source}", env, *extra)
                self.assertNotEqual(result.returncode, 2, result.stderr)
                evidence = temp_dir / "evidence" / f"home-{source}"
                argv = json.loads((evidence / "raw-output.txt").read_text(encoding="utf-8"))["argv"]
                self.assertEqual(argv[argv.index("--hermes-home") + 1], str(hermes_home))
                self.assertFalse({"--role", "--profile"} & set(argv))
                for path in sorted(evidence.iterdir()):
                    if path.name == "raw-output.txt":
                        continue
                    with self.subTest(file=path.name):
                        self.assertNotIn(str(hermes_home), path.read_text(encoding="utf-8"))
                trial = json.loads((evidence / "trial.json").read_text(encoding="utf-8"))
                self.assertIn("<hermes-home>", trial["command"])

    def test_missing_hermes_home_is_refused_before_any_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            env = {key: value for key, value in os.environ.items() if key != "STAFFBENCH_HERMES_HOME"}
            result = _run_cell(temp_dir, "no-home", env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("STAFFBENCH_HERMES_HOME", result.stderr)
            self.assertFalse((temp_dir / "evidence").exists())

    def test_custom_agent_command_does_not_need_a_hermes_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            agent = temp_dir / "agent.py"
            agent.write_text("#!/usr/bin/env python3\nprint('{}')\n", encoding="utf-8")
            agent.chmod(0o755)
            env = {key: value for key, value in os.environ.items() if key != "STAFFBENCH_HERMES_HOME"}
            result = _run_cell(temp_dir, "custom", env, "--agent-command", str(agent))
            self.assertEqual(result.returncode, 1, result.stderr)
            record = json.loads((temp_dir / "evidence" / "custom" / "run-record.json").read_text(encoding="utf-8"))
            self.assertEqual(record["failure_class"], "unverified-isolation")


if __name__ == "__main__":
    unittest.main()
