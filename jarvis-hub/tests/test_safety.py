"""소스 전체 정적 검사: 셸 실행 경로가 없는지."""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jarvis")


def sources():
    for dirpath, _, files in os.walk(ROOT):
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(dirpath, f)
                with open(p, encoding="utf-8") as fh:
                    yield p, fh.read()


class SafetyTest(unittest.TestCase):
    def test_no_shell_true_or_os_system(self):
        bad = re.compile(r"shell\s*=\s*True|os\.system\s*\(|os\.popen\s*\(|subprocess\.getoutput|"
                         r"subprocess\.getstatusoutput|\beval\s*\(|\bexec\s*\(|os\.exec[lv]p?e?\s*\(|os\.spawn")
        found = []
        n = 0
        for p, text in sources():
            n += 1
            for i, line in enumerate(text.splitlines(), 1):
                if bad.search(line):
                    found.append("%s:%d: %s" % (os.path.relpath(p, ROOT), i, line.strip()))
        self.assertGreater(n, 10)
        self.assertEqual(found, [])

    def test_subprocess_only_in_runner(self):
        users = [os.path.relpath(p, ROOT) for p, t in sources() if re.search(r"subprocess\.(run|Popen|call|check_)", t)]
        self.assertEqual(users, [os.path.join("handlers", "base.py")])


if __name__ == "__main__":
    unittest.main()
