"""Generated repository tasks with visible diagnostics and post-run acceptance tests."""

from textwrap import dedent


def text(value):
    return dedent(value).lstrip()


CASES = [
    {"name": "config-precedence", "title": "配置优先级与类型转换", "allowed": ["pkg/config.py", "pkg/parsing.py"],
     "prompt": "Fix configuration loading across modules. Priority must be defaults < JSON file < APP_ environment. port is an integer in 1..65535; debug accepts boolean or case-insensitive true/false/1/0 strings, rejecting other values with ValueError. Use the supplied env mapping, never os.environ. Preserve load_config(path=None, env=None) and result keys port/debug. Do not silently ignore malformed JSON.",
     "files": {
         "pkg/parsing.py": text('''
             def parse_debug(value):
                 return bool(value)
         '''),
         "pkg/config.py": text('''
             import json
             from .parsing import parse_debug

             def load_config(path=None, env=None):
                 env = env or {}
                 config = {"port": 8000, "debug": False}
                 if "APP_PORT" in env:
                     config["port"] = env["APP_PORT"]
                 if "APP_DEBUG" in env:
                     config["debug"] = env["APP_DEBUG"]
                 if path:
                     with open(path, encoding="utf-8") as handle:
                         config.update(json.load(handle))
                 return {"port": config["port"], "debug": parse_debug(config["debug"])}
         ''')},
     "visible": text('''
         import json, tempfile, unittest
         from pathlib import Path
         from pkg.config import load_config
         class Tests(unittest.TestCase):
             def test_environment_wins(self):
                 with tempfile.TemporaryDirectory() as directory:
                     path = Path(directory) / "config.json"
                     path.write_text(json.dumps({"port": 9000, "debug": True}))
                     self.assertEqual(load_config(path, {"APP_PORT": "7777", "APP_DEBUG": "false"}), {"port": 7777, "debug": False})
     '''),
     "hidden": text('''
         import json, tempfile, unittest
         from pathlib import Path
         from pkg.config import load_config
         class Acceptance(unittest.TestCase):
             def test_defaults(self):
                 self.assertEqual(load_config(), {"port": 8000, "debug": False})
             def test_file(self):
                 with tempfile.TemporaryDirectory() as directory:
                     path = Path(directory) / "config.json"
                     path.write_text('{"port": "9000", "debug": "false"}')
                     self.assertEqual(load_config(path, {}), {"port": 9000, "debug": False})
             def test_false_strings(self):
                 for value in [False, "FALSE", "0", "false"]:
                     with self.subTest(value=value):
                         self.assertFalse(load_config(env={"APP_DEBUG": value})["debug"])
             def test_true_strings(self):
                 for value in [True, "TRUE", "1", "true"]:
                     with self.subTest(value=value):
                         self.assertTrue(load_config(env={"APP_DEBUG": value})["debug"])
             def test_invalid_debug(self):
                 with self.assertRaises(ValueError):
                     load_config(env={"APP_DEBUG": "maybe"})
             def test_port(self):
                 for value in ["0", "65536", "invalid"]:
                     with self.subTest(value=value), self.assertRaises(ValueError):
                         load_config(env={"APP_PORT": value})
             def test_bad_json(self):
                 with tempfile.TemporaryDirectory() as directory:
                     path = Path(directory) / "config.json"
                     path.write_text('{bad')
                     with self.assertRaises(ValueError):
                         load_config(path)
     ''')},
    {"name": "pagination-diagnosis", "title": "分页错误诊断与循环保护", "allowed": ["pkg/client.py"],
     "prompt": "Fix collect_items(fetch_page). Call fetch_page(None) for the first page, then follow next_cursor until it is None, preserve item order. Empty pages with a next cursor must not stop traversal. Cursor 0 and empty-string cursors are valid. Repeated cursors must raise ValueError before requesting the same cursor again. Missing items means an empty list; missing next_cursor means end. Do not catch fetch_page errors.",
     "files": {"pkg/client.py": text('''
         def collect_items(fetch_page):
             page = fetch_page(None)
             return page["items"]
     ''')},
     "visible": text('''
         import unittest
         from pkg.client import collect_items
         class Tests(unittest.TestCase):
             def test_multiple_pages(self):
                 pages = {None: {"items": [1], "next_cursor": "b"}, "b": {"items": [2], "next_cursor": None}}
                 self.assertEqual(collect_items(pages.__getitem__), [1, 2])
     '''),
     "hidden": text('''
         import unittest
         from pkg.client import collect_items
         class Acceptance(unittest.TestCase):
             def test_empty_middle(self):
                 pages = {None: {"items": [], "next_cursor": "b"}, "b": {"items": [3, 4]}}
                 self.assertEqual(collect_items(pages.__getitem__), [3, 4])
             def test_false_cursors(self):
                 for cursor in [0, ""]:
                     with self.subTest(cursor=cursor):
                         pages = {None: {"items": [1], "next_cursor": cursor}, cursor: {"items": [2]}}
                         self.assertEqual(collect_items(pages.__getitem__), [1, 2])
             def test_loop(self):
                 calls = []
                 def fetch(cursor):
                     calls.append(cursor)
                     if len(calls) > 3:
                         raise RuntimeError("Loop was not stopped")
                     return {"items": [], "next_cursor": "same"}
                 with self.assertRaises(ValueError):
                     collect_items(fetch)
                 self.assertEqual(calls, [None, "same"])
             def test_missing_keys(self):
                 self.assertEqual(collect_items(lambda _: {}), [])
             def test_errors_propagate(self):
                 def fetch(_):
                     raise OSError("network")
                 with self.assertRaises(OSError):
                     collect_items(fetch)
     ''')},
    {"name": "invoice-cli", "title": "金额计算与跨文件 CLI 功能", "allowed": ["pkg/invoice.py", "pkg/cli.py"],
     "prompt": "Implement invoice_total(items, discount='0') using decimal arithmetic: each item has price as decimal string and quantity as a positive int (bool is invalid). Reject negative price, quantity <=0, non-integer quantity, or discount outside [0,1] with ValueError. Sum all price*quantity, apply discount once, then ROUND_HALF_UP to two decimals; return a Decimal. Add pkg/cli.py so python -m pkg.cli PATH [--discount VALUE] reads a JSON list and prints only JSON {total: 'two-decimal-string'}. Invalid input returns nonzero without a traceback; valid input exits 0.",
     "files": {"pkg/invoice.py": text('''
         from decimal import Decimal
         def invoice_total(items, discount="0"):
             return Decimal(str(sum(float(item["price"]) for item in items)))
     ''')},
     "visible": text('''
         import unittest
         from decimal import Decimal
         from pkg.invoice import invoice_total
         class Tests(unittest.TestCase):
             def test_quantity_and_discount(self):
                 self.assertEqual(invoice_total([{"price": "10.00", "quantity": 3}], "0.1"), Decimal("27.00"))
     '''),
     "hidden": text('''
         import json, subprocess, sys, tempfile, unittest
         from pathlib import Path
         from decimal import Decimal
         from pkg.invoice import invoice_total
         class Acceptance(unittest.TestCase):
             def test_rounding(self):
                 self.assertEqual(invoice_total([{"price":"0.105","quantity":1}]), Decimal("0.11"))
             def test_sum_before_rounding(self):
                 self.assertEqual(invoice_total([{"price":"0.104","quantity":1}]*3), Decimal("0.31"))
             def test_empty(self):
                 self.assertEqual(invoice_total([]), Decimal("0.00"))
             def test_invalid_items(self):
                 for item in [{"price":"-1","quantity":1},{"price":"1","quantity":0},{"price":"1","quantity":1.5},{"price":"1","quantity":True}]:
                     with self.subTest(item=item), self.assertRaises(ValueError):
                         invoice_total([item])
             def test_invalid_discount(self):
                 for discount in ["-0.1", "1.01"]:
                     with self.subTest(discount=discount), self.assertRaises(ValueError):
                         invoice_total([], discount)
             def test_cli(self):
                 with tempfile.TemporaryDirectory() as directory:
                     path = Path(directory) / "items.json"
                     path.write_text('[{"price":"10.00","quantity":3}]')
                     process = subprocess.run([sys.executable,"-B","-m","pkg.cli",str(path),"--discount","0.1"],cwd=ROOT,capture_output=True,text=True)
                     self.assertEqual(process.returncode, 0, process.stderr)
                     self.assertEqual(json.loads(process.stdout), {"total":"27.00"})
             def test_invalid_cli(self):
                 with tempfile.TemporaryDirectory() as directory:
                     path = Path(directory) / "bad.json"
                     path.write_text('bad JSON')
                     process = subprocess.run([sys.executable,"-B","-m","pkg.cli",str(path)],cwd=ROOT,capture_output=True,text=True)
                     self.assertNotEqual(process.returncode, 0)
                     self.assertNotIn("Traceback", process.stderr)
     ''')},
    {"name": "context-recovery", "title": "长上下文下的约束保持", "allowed": ["pkg/ledger.py"], "prefill": True,
     "prompt": "Repair summarize(records): group case-sensitive account names, sum all integer amounts per account, keep negative and zero totals, return a dict sorted by account name. Ignore rows with amount=None; reject other non-int amounts including bool with ValueError. Account must be a nonempty string, otherwise ValueError. Do not modify tests or README. Preserve all requirements even if conversation context is compacted.",
     "files": {"pkg/ledger.py": text('''
         def summarize(records):
             return {row["account"]: row["amount"] for row in records}
     ''')},
     "visible": text('''
         import unittest
         from pkg.ledger import summarize
         class Tests(unittest.TestCase):
             def test_accumulation(self):
                 self.assertEqual(summarize([{"account":"A","amount":2},{"account":"A","amount":3}]), {"A":5})
     '''),
     "hidden": text('''
         import unittest
         from pkg.ledger import summarize
         class Acceptance(unittest.TestCase):
             def test_negative_and_zero(self):
                 self.assertEqual(summarize([{"account":"A","amount":2},{"account":"A","amount":-2},{"account":"B","amount":-4}]), {"A":0,"B":-4})
             def test_sorted(self):
                 self.assertEqual(list(summarize([{"account":"z","amount":1},{"account":"a","amount":2}])), ["a","z"])
             def test_none(self):
                 self.assertEqual(summarize([{"account":"A","amount":None}]), {})
             def test_invalid_amount(self):
                 for amount in [True, "1", 1.5]:
                     with self.subTest(amount=amount), self.assertRaises(ValueError):
                         summarize([{"account":"A","amount":amount}])
             def test_invalid_account(self):
                 for account in ["", None, 4]:
                     with self.subTest(account=account), self.assertRaises(ValueError):
                         summarize([{"account":account,"amount":1}])
             def test_empty(self):
                 self.assertEqual(summarize([]), {})
     ''')},
]
