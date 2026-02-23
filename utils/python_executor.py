# utils/python_executor.py - V6 MODULE 4: PYTHON AUTONOMOUS CORE (The Calculator)
"""
Sandbox executor: run generated Pandas/NumPy code with restricted globals.
Workflow: AI writes script from extracted JSON -> System executes -> Returns exact number or result.
Trước khi execute: allowlist thư viện + kiểm tra từ khóa cấm để giảm rủi ro.
"""
import json
import re
from typing import Any, Dict, Optional, Tuple

# Restricted builtins and modules for exec()
import math
import numpy as np
import pandas as pd

# Allowlist: chỉ cho phép dùng các module này (tên xuất hiện trong code/import)
ALLOWED_MODULE_NAMES = frozenset({"math", "numpy", "pandas", "np", "pd", "json"})

# Các pattern bị cấm (regex) — không được xuất hiện trong code
_FORBIDDEN_PATTERNS = [
    (re.compile(r"\b__import__\s*\("), "__import__"),
    (re.compile(r"\bopen\s*\("), "open()"),
    (re.compile(r"\beval\s*\("), "eval()"),
    (re.compile(r"\bexec\s*\("), "exec()"),
    (re.compile(r"\bcompile\s*\("), "compile()"),
    (re.compile(r"\bbreakpoint\s*\("), "breakpoint()"),
    (re.compile(r"\binput\s*\("), "input()"),
    (re.compile(r"\bglobals\s*\("), "globals()"),
    (re.compile(r"\blocals\s*\("), "locals()"),
    (re.compile(r"\bvars\s*\("), "vars()"),
    (re.compile(r"\bgetattr\s*\("), "getattr()"),
    (re.compile(r"\bsetattr\s*\("), "setattr()"),
    (re.compile(r"\bdelattr\s*\("), "delattr()"),
    (re.compile(r"\b__builtins__\b"), "__builtins__"),
    (re.compile(r"\bos\.|import\s+os\b"), "os"),
    (re.compile(r"\bsubprocess\.|import\s+subprocess\b"), "subprocess"),
    (re.compile(r"\bsocket\.|import\s+socket\b"), "socket"),
    (re.compile(r"\bsys\.|import\s+sys\b"), "sys"),
]
# Dòng import chỉ được phép: from math/numpy/pandas/json import ... hoặc import math/numpy/pandas/json
_IMPORT_ALLOWED = re.compile(r"^\s*(?:from\s+(math|numpy|pandas|json)\s+import\s+.+|import\s+(math|numpy|pandas|json)(?:\s+as\s+\w+)?)\s*(?:#.*)?$")


def validate_code_safety(code: str) -> Tuple[bool, str]:
    """
    Kiểm tra code trước khi execute: cấm từ khóa nguy hiểm, chỉ cho phép import math/numpy/pandas/json.
    Returns (True, "") nếu an toàn, (False, "lý do") nếu không.
    """
    if not code or not code.strip():
        return False, "Code rỗng."
    code_lower = code.strip()
    for pat, name in _FORBIDDEN_PATTERNS:
        if pat.search(code):
            return False, "Code không được phép dùng: %s" % name
    # Cho phép import math, numpy, pandas, json (các dòng import riêng lẻ)
    for line in code.splitlines():
        line = line.strip()
        if line.startswith("import ") or line.startswith("from "):
            if not _IMPORT_ALLOWED.match(line) and not re.match(r"^\s*#", line):
                # Kiểm tra cho phép: from numpy import xyz, import pandas as pd, ...
                allowed_import = re.match(
                    r"^\s*(?:from\s+(math|numpy|pandas|json)\s+import\s+.+|import\s+(math|numpy|pandas|json)(?:\s+as\s+\w+)?)\s*(?:#.*)?$",
                    line,
                )
                if not allowed_import:
                    return False, "Chỉ được import math, numpy, pandas, json."
    return True, ""


# Safe builtins (no file I/O, no eval/exec, no open, no __import__)
def _safe_builtins() -> Dict[str, Any]:
    safe = {
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "float": float,
        "int": int,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "pow": pow,
        "print": print,
        "range": range,
        "round": round,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
        "True": True,
        "False": False,
        "None": None,
    }
    return safe


def _restricted_globals() -> Dict[str, Any]:
    """Globals allowed in exec(): pandas as pd, numpy as np, math, json, and safe builtins."""
    g = _safe_builtins()
    g["pd"] = pd
    g["pandas"] = pd
    g["np"] = np
    g["numpy"] = np
    g["math"] = math
    g["json"] = json
    return g


class PythonExecutor:
    """
    Sandbox executor using exec() with restricted globals.
    Use for AI-generated Pandas/NumPy scripts (e.g. from extracted JSON).
    """

    @staticmethod
    def execute(
        code: str,
        timeout_seconds: float = 10.0,
        result_variable: str = "result",
    ) -> Tuple[Optional[Any], Optional[str]]:
        """
        Execute code in sandbox. Trước khi chạy: validate_code_safety (allowlist thư viện, cấm từ khóa nguy hiểm).
        Expects code to assign final value to a variable (default `result`).
        Returns (value, None) on success, or (None, error_message) on failure.
        """
        ok, err_msg = validate_code_safety(code)
        if not ok:
            return None, "Sandbox: %s" % err_msg
        g = _restricted_globals()
        g["__builtins__"] = _safe_builtins()
        l = {}
        try:
            exec(code, g, l)
        except Exception as e:
            return None, str(e)
        out = l.get(result_variable)
        return out, None

    @staticmethod
    def execute_and_serialize(
        code: str,
        result_variable: str = "result",
        timeout_seconds: float = 10.0,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Execute code and serialize result to JSON-friendly form.
        Returns (json_string_or_simple_value, None) or (None, error_message).
        """
        val, err = PythonExecutor.execute(code, timeout_seconds=timeout_seconds, result_variable=result_variable)
        if err:
            return None, err
        if val is None:
            return "null", None
        try:
            if isinstance(val, (int, float, bool, str)):
                return json.dumps(val), None
            if isinstance(val, (list, dict)):
                return json.dumps(val, default=str), None
            if hasattr(val, "tolist"):
                return json.dumps(val.tolist()), None
            if hasattr(val, "to_dict"):
                return json.dumps(val.to_dict(), default=str), None
            return str(val), None
        except Exception as e:
            return None, "Serialize error: %s" % e
