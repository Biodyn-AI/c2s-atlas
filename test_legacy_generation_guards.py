"""The archival generators must stop before importing compute libraries or touching data."""
import ast
from contextlib import redirect_stderr
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


class LegacyGenerationGuardTests(unittest.TestCase):
    def test_default_generation_is_disabled_without_changing_files(self):
        for script in ("gen_atlas_data.py", "shrink_atlas_data.py"):
            with self.subTest(script=script):
                path = Path(__file__).with_name(script)
                tree = ast.parse(path.read_text(), filename=str(path))
                # Execute only the guard preamble. The archival body is never run,
                # even when a regression removes the guard or puts it too late.
                legacy_start = next(index for index, node in enumerate(tree.body)
                    if isinstance(node, ast.Import) and any(alias.name == "json" for alias in node.names))
                preamble = ast.Module(body=tree.body[:legacy_start], type_ignores=[])
                # Refuse to execute a changed preamble that introduces any other
                # import/call. A misplaced file operation must fail statically.
                allowed_calls = {"argparse.ArgumentParser", "_legacy_parser.add_argument",
                                 "_legacy_parser.parse_args", "_legacy_parser.error"}
                for node in ast.walk(preamble):
                    if isinstance(node, ast.Import):
                        self.assertEqual([alias.name for alias in node.names], ["argparse"])
                    self.assertNotIsInstance(node, ast.ImportFrom)
                    if isinstance(node, ast.Call):
                        self.assertIn(ast.unparse(node.func), allowed_calls)
                stderr = io.StringIO()
                with patch.object(sys, "argv", [script]), redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as stopped:
                        exec(compile(preamble, str(path), "exec"), {"__name__": "__main__"})
                self.assertEqual(stopped.exception.code, 2)
                self.assertIn("Archival legacy generator is disabled", stderr.getvalue())
                self.assertIn("export_revision_atlas.py", stderr.getvalue())
                self.assertIn("--allow-legacy-scorer", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
