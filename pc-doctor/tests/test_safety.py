"""정적 안전성 검사: 셸 실행 금지, Windows 호출은 system 모듈에만."""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "pc_doctor")


def sources():
    for fn in sorted(os.listdir(PKG)):
        if fn.endswith(".py"):
            with open(os.path.join(PKG, fn), encoding="utf-8") as f:
                yield fn, f.read()


class SafetyTests(unittest.TestCase):
    def test_no_shell_true_anywhere(self):
        for fn, src in sources():
            self.assertIsNone(re.search(r"shell\s*=\s*True", src), fn)
            self.assertNotIn("os.system(", src, fn)
            self.assertNotIn("os.popen(", src, fn)
            self.assertNotIn("Invoke-Expression", src, fn)

    def test_subprocess_and_winreg_only_in_system_module(self):
        for fn, src in sources():
            if fn == "system.py":
                continue
            self.assertNotRegex(src, r"\bimport subprocess\b|\bsubprocess\.", fn)
            self.assertNotRegex(src, r"\bimport winreg\b|\bctypes\b", fn)

    def test_powershell_scripts_are_constants(self):
        from pc_doctor import system

        for key, script in system.PS_SCRIPTS.items():
            self.assertIsInstance(script, str)
            # 문자열 포매팅 자리표시자 없음 = 사용자 입력이 끼어들 자리가 없음
            self.assertNotRegex(script, r"%[sd]|\{0\}|\$\{?args", key)
            # 상태를 바꾸는 cmdlet 금지 (조회 전용)
            for bad in ("Set-", "Remove-", "Stop-", "Start-", "New-Item", "Disable-", "Enable-", "Install-"):
                self.assertNotIn(bad, script, "%s: %s" % (key, bad))

    def test_run_rejects_string_commands(self):
        from pc_doctor.system import WindowsSystem

        with self.assertRaises(TypeError):
            WindowsSystem().run("powershell -Command whoami")

    def test_readme_documents_never_touched(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        for word in ("Defender", "UAC", "방화벽", "dry-run", "백업"):
            self.assertIn(word, readme)


if __name__ == "__main__":
    unittest.main()
