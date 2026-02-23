# tests/test_python_executor_safety.py
"""Unit test: PythonExecutor validate_code_safety — allowlist thư viện, cấm từ khóa nguy hiểm."""
import unittest

from utils.python_executor import validate_code_safety, PythonExecutor


class TestPythonExecutorSafety(unittest.TestCase):
    def test_safe_code_pandas_numpy_passes(self):
        ok, msg = validate_code_safety("import pandas as pd\nresult = pd.Series([1,2,3]).sum()")
        self.assertTrue(ok, msg or "pandas/numpy nên được phép")

    def test_forbidden_open_fails(self):
        ok, msg = validate_code_safety("result = open('/etc/passwd').read()")
        self.assertFalse(ok)
        self.assertIn("open", msg or "")

    def test_forbidden_eval_fails(self):
        ok, msg = validate_code_safety("result = eval('1+1')")
        self.assertFalse(ok)
        self.assertIn("eval", msg or "")

    def test_forbidden_exec_fails(self):
        ok, msg = validate_code_safety("exec('print(1)')\nresult = 1")
        self.assertFalse(ok)

    def test_execute_rejects_unsafe_code(self):
        val, err = PythonExecutor.execute("result = open('x').read()")
        self.assertIsNone(val)
        self.assertIsNotNone(err)
        self.assertIn("Sandbox", err or "")


if __name__ == "__main__":
    unittest.main()
