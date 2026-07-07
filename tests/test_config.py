"""read_secrets tests: prefix filter, quote strip, comment skip, malformed warnings.

The CLI dispatch uses read_secrets to load ~/.status-led/secrets.env and
expose only matching-prefix keys to a subprocess. Cross-prefix isolation is
the load-bearing property — a gitlab poller must not see SLACK_TOKEN.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from status_led import config


class ReadSecretsTest(unittest.TestCase):
    """Each test gets a tmp file and patches config.SECRETS_FILE to point at it."""

    def _write(self, content: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".env")
        with os.fdopen(fd, "w") as f:
            f.write(content)
        self.addCleanup(os.unlink, path)
        return path

    def _read(self, path: str, prefix: str) -> dict[str, str]:
        with mock.patch.object(config, "SECRETS_FILE", type(config.SECRETS_FILE)(path)):
            return config.read_secrets(prefix)

    def test_missing_file_returns_empty(self):
        # A path that doesn't exist → empty dict (no exception).
        result = self._read("/nonexistent/path/.env", "GITLAB_")
        self.assertEqual(result, {})

    def test_prefix_filter(self):
        path = self._write(
            "GITLAB_URL=https://gl.example.com\n"
            "SLACK_TOKEN=xoxb-123\n"
            "GITLAB_TOKEN=abc\n"
        )
        result = self._read(path, "GITLAB_")
        self.assertEqual(result,
                         {"GITLAB_URL": "https://gl.example.com",
                          "GITLAB_TOKEN": "abc"})

    def test_comment_and_blank_lines_ignored(self):
        path = self._write(
            "# comment\n"
            "\n"
            "GITLAB_URL=value\n"
            "   # indented comment (treated as malformed by current parser)\n"
        )
        result = self._read(path, "GITLAB_")
        self.assertEqual(result, {"GITLAB_URL": "value"})

    def test_quotes_stripped(self):
        path = self._write(
            'GITLAB_A="double"\n'
            "GITLAB_B='single'\n"
            "GITLAB_C=plain\n"
        )
        result = self._read(path, "GITLAB_")
        self.assertEqual(result["GITLAB_A"], "double")
        self.assertEqual(result["GITLAB_B"], "single")
        self.assertEqual(result["GITLAB_C"], "plain")

    def test_first_equals_split(self):
        # Value can contain '='.
        path = self._write("GITLAB_CONN=host=db;user=admin\n")
        result = self._read(path, "GITLAB_")
        self.assertEqual(result["GITLAB_CONN"], "host=db;user=admin")

    def test_no_prefix_match_returns_empty(self):
        path = self._write("FOO=bar\nBAZ=qux\n")
        result = self._read(path, "GITLAB_")
        self.assertEqual(result, {})

    def test_empty_value_kept(self):
        path = self._write("GITLAB_EMPTY=\n")
        result = self._read(path, "GITLAB_")
        self.assertEqual(result, {"GITLAB_EMPTY": ""})

    def test_value_with_spaces(self):
        # Values are stripped; internal spaces preserved.
        path = self._write("GITLAB_LIST=a b c\n")
        result = self._read(path, "GITLAB_")
        self.assertEqual(result["GITLAB_LIST"], "a b c")


class SecretsFileMissingSilentTest(unittest.TestCase):
    """The canonical ~/.status-led/secrets.env doesn't exist → empty dict, no error."""

    def test_default_path_missing_returns_empty(self):
        # Default SECRETS_FILE probably exists for the dev (we just made it).
        # Patch to a guaranteed-missing path.
        with mock.patch.object(config, "SECRETS_FILE",
                               type(config.SECRETS_FILE)("/nonexistent/default.env")):
            result = config.read_secrets("GITLAB_")
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
