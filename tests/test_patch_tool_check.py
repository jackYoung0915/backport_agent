import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from patch_tool import check_commits, parse_check_output_line


PATCH_TOOL = Path(__file__).resolve().parents[1] / "patch_tool.py"


class CheckDuplicatePatchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo = Path(self.temp_dir.name) / "repo"
        self.repo.mkdir()
        self._git("init", "-q")
        self._git("config", "user.name", "Test User")
        self._git("config", "user.email", "test@example.com")
        self.clock = 1700000000

        self.base = self._commit("base", "base")
        self._git("tag", "v1")
        self.old_duplicate = self._commit("same patch", "old")
        self.other = self._commit("other patch", "other")
        self.new_duplicate = self._commit("same patch", "new")

    def _git(self, *args, env=None):
        completed = subprocess.run(
            ["git"] + list(args),
            cwd=self.repo,
            capture_output=True,
            text=True,
            env=env,
        )
        if completed.returncode != 0:
            self.fail(
                "git {} failed: {}".format(
                    " ".join(args), completed.stderr.strip()
                )
            )
        return completed.stdout.strip()

    def _commit(self, title, content):
        self.clock += 1
        (self.repo / "patch.txt").write_text(content + "\n", encoding="utf-8")
        self._git("add", "patch.txt")
        env = os.environ.copy()
        env["GIT_AUTHOR_DATE"] = "{} +0000".format(self.clock)
        env["GIT_COMMITTER_DATE"] = "{} +0000".format(self.clock)
        self._git("commit", "-q", "-m", title, env=env)
        return self._git("rev-parse", "HEAD")

    def test_title_input_returns_all_duplicates_oldest_to_newest(self):
        result = check_commits(
            ["same patch"], repo=str(self.repo), long_hash=True,
        )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["unmatched"], 0)
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(
            [row["commit_id"] for row in result["results"]],
            [self.old_duplicate, self.new_duplicate],
        )
        for row in result["results"]:
            self.assertEqual(row["status"], "Y")
            self.assertEqual(row["match_method"], "title")
            self.assertTrue(row["git_describe"].startswith("v1-"))
            self.assertTrue(row["commit_time"])
            self.assertIsNotNone(row["lines_changed"])

    def test_hash_and_title_inputs_expand_the_same_title(self):
        cases = [
            ("title first", self.other + " same patch", "title"),
            ("hash only", self.old_duplicate, "hash"),
            ("hash fallback", self.old_duplicate + " renamed patch", "hash"),
        ]
        for name, input_line, method in cases:
            with self.subTest(name=name):
                result = check_commits(
                    [input_line], repo=str(self.repo), long_hash=True,
                )
                self.assertEqual(result["matched"], 1)
                self.assertEqual(
                    [row["commit_id"] for row in result["results"]],
                    [self.old_duplicate, self.new_duplicate],
                )
                self.assertEqual(
                    {row["match_method"] for row in result["results"]},
                    {method},
                )

    def test_sorting_and_unmatched_counts_cover_expanded_rows(self):
        inputs = ["same patch", "other patch", "missing patch"]

        grouped = check_commits(
            inputs, repo=str(self.repo), long_hash=True, sort_results=False,
        )
        self.assertEqual(
            [row["commit_id"] for row in grouped["results"]],
            [self.old_duplicate, self.new_duplicate, self.other, ""],
        )
        self.assertEqual(grouped["total"], 3)
        self.assertEqual(grouped["matched"], 2)
        self.assertEqual(grouped["unmatched"], 1)

        ordered = check_commits(
            inputs, repo=str(self.repo), long_hash=True,
        )
        self.assertEqual(
            [row["commit_id"] for row in ordered["results"]],
            [self.old_duplicate, self.other, self.new_duplicate, ""],
        )

    def test_multi_repo_rows_keep_other_candidates_as_alternates(self):
        result = check_commits(
            ["same patch"],
            long_hash=True,
            repos=[{"name": "current", "path": str(self.repo)}],
        )

        self.assertEqual(len(result["results"]), 2)
        for row, other_hash in zip(
            result["results"], [self.new_duplicate, self.old_duplicate]
        ):
            self.assertEqual(row["repo"], "current")
            self.assertEqual(
                [alt["full_hash"] for alt in row["alternate_matches"]],
                [other_hash],
            )

    def test_cli_writes_all_duplicates_in_parseable_six_column_output(self):
        input_file = Path(self.temp_dir.name) / "input.txt"
        output_file = Path(self.temp_dir.name) / "output.txt"
        input_file.write_text("same patch\n", encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                str(PATCH_TOOL),
                "-C",
                str(self.repo),
                "check",
                str(input_file),
                str(output_file),
                "--long-hash",
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = output_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        parsed = []
        for line in lines:
            self.assertEqual(len(line.split("|")), 6)
            row = parse_check_output_line(line)
            self.assertIsNotNone(row)
            parsed.append(row)
        self.assertEqual(
            [row[0] for row in parsed],
            [self.old_duplicate, self.new_duplicate],
        )
        self.assertTrue(all(row[1:] == ("same patch", "Y") for row in parsed))


if __name__ == "__main__":
    unittest.main()
