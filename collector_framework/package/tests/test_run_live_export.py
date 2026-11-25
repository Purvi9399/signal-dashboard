"""CLI regression: --upload requires --live."""

from __future__ import annotations

import unittest

from package.cli import main


class RunUploadFlagTests(unittest.TestCase):
    def test_upload_without_live_exits_nonzero(self) -> None:
        code = main(["run", "--upload"])
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
