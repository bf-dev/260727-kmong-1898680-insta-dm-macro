# -*- coding: utf-8 -*-
"""업데이터 회귀 테스트 (kmong 1898680, v1.4.0).

배경: 고객은 1.3.1 -> 1.3.2 자동 업데이트가 51번 연속 실패해 4시간 넘게 옛 버전에 묶여 있었다.
스왑 스크립트를 UTF-8 .bat 으로 썼는데 cmd.exe 는 배치 파일을 OEM 코드페이지(한국어 윈도우
= cp949)로 읽기 때문에, 한글 exe 경로(`인스타DM매크로.exe`)가 깨져 copy 가 조용히 실패했다.
여기서 검증하는 것:
  1) 스왑 대상 경로가 sys.executable 에서 나온다(파일명 하드코딩 없음) - 한글 파일명 포함.
  2) PowerShell 스크립트는 UTF-8 BOM 으로 저장된다(PS 5.1 이 유니코드로 읽는 유일한 조건).
  3) .bat 폴백은 OEM 코드페이지로 저장된다(UTF-8 로 쓰면 안 된다).
  4) 실패 결과 파일이 진단으로 보고되고, 실패 사유가 마커에 남는다.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import config
import updater

KOREAN_EXE = "인스타DM매크로.exe"


class TargetPathTest(unittest.TestCase):
    def test_target_comes_from_sys_executable_not_a_hardcoded_name(self):
        """고객 디스크의 실제 파일명이 무엇이든 그걸 따라가야 한다."""
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, KOREAN_EXE)
            open(exe, "wb").write(b"x")
            with mock.patch.object(sys, "executable", exe):
                self.assertEqual(updater.target_exe_path(), os.path.realpath(exe))

    def test_target_follows_a_renamed_exe(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "고객이바꾼이름 (2).exe")
            open(exe, "wb").write(b"x")
            with mock.patch.object(sys, "executable", exe):
                self.assertTrue(updater.target_exe_path().endswith("고객이바꾼이름 (2).exe"))

    def test_no_hardcoded_exe_name_in_any_string_literal(self):
        """설명(주석/독스트링)에 이름이 나오는 건 괜찮지만, 실행되는 문자열에 있으면 안 된다."""
        import ast
        tree = ast.parse(open(updater.__file__, encoding="utf-8").read())
        docstrings = set()
        for node in ast.walk(tree):
            doc = ast.get_docstring(node, clean=False) if isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else None
            if doc:
                docstrings.add(doc)
        offenders = [n.value for n in ast.walk(tree)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)
                     and KOREAN_EXE in n.value and n.value not in docstrings]
        self.assertEqual(offenders, [], f"exe 파일명이 코드 문자열에 박혀 있음: {offenders}")


class StagingTest(unittest.TestCase):
    def test_staged_next_to_target_and_moved(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, KOREAN_EXE)
            open(target, "wb").write(b"old")
            downloaded = os.path.join(tempfile.mkdtemp(), "tmp.exe")
            open(downloaded, "wb").write(b"new-bytes")
            staged = updater.stage_new_exe(downloaded, target, "1.4.0")
            self.assertEqual(os.path.dirname(staged), d)
            self.assertTrue(os.path.isfile(staged))
            self.assertFalse(os.path.exists(downloaded))
            self.assertIn("1.4.0", os.path.basename(staged))

    def test_staging_failure_is_raised_not_swallowed(self):
        """대상 폴더에 못 쓰면(권한 등) 예외가 나야 앱이 살아서 사유를 보고할 수 있다."""
        downloaded = os.path.join(tempfile.mkdtemp(), "tmp.exe")
        open(downloaded, "wb").write(b"new-bytes")
        target = os.path.join("/nonexistent-dir-xyz", KOREAN_EXE)
        with self.assertRaises(Exception):
            updater.stage_new_exe(downloaded, target, "1.4.0")


class SwapScriptTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.target = os.path.join(self.dir, KOREAN_EXE)
        self.staged = self.target + ".update-1.4.0.tmp"
        self.result = os.path.join(self.dir, "update_result.json")
        self.state = os.path.join(self.dir, "update_state.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _ps(self):
        return updater.build_powershell_swap(
            self.target, self.staged, "1.4.0", 1234, self.result, self.state, 999)

    def test_powershell_script_carries_the_korean_target_path(self):
        body = self._ps()
        self.assertIn(self.target, body)
        self.assertIn(self.staged, body)
        self.assertIn("Start-Process -FilePath $target", body)

    def test_powershell_script_records_a_result_file(self):
        body = self._ps()
        self.assertIn("$resultPath", body)
        self.assertIn("ConvertTo-Json", body)
        self.assertIn("$result.ok = $true", body)

    def test_powershell_renames_the_locked_exe_before_replacing_it(self):
        """실행 중이던 exe 는 덮어쓸 수는 없어도 rename 은 된다 - 그 순서를 지켜야 한다."""
        body = self._ps()
        rename_at = body.index("Move-Item -LiteralPath $target -Destination $backup")
        place_at = body.index("Move-Item -LiteralPath $staged -Destination $target")
        self.assertLess(rename_at, place_at)

    def test_ps1_is_written_with_utf8_bom(self):
        with mock.patch.object(os.path, "exists", lambda p: True):
            path, mode = updater.write_swap_script(
                current_exe=self.target, staged=self.staged, latest="1.4.0", pid=1234,
                result_path=self.result, state_path=self.state, expected_size=999)
        self.addCleanup(os.unlink, path)
        self.assertEqual(mode, "ps1")
        raw = open(path, "rb").read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "PS 5.1 은 BOM 이 없으면 ANSI 로 읽는다")
        self.assertIn(self.target, raw.decode("utf-8-sig"))

    def test_bat_fallback_is_not_written_as_utf8(self):
        """v1.3.x 의 진짜 버그: .bat 을 UTF-8 로 저장했다. cmd 는 OEM 코드페이지로 읽는다."""
        with mock.patch.object(updater, "POWERSHELL", "/definitely/not/here.exe"), \
                mock.patch.object(updater, "_oem_encoding", lambda: "cp949"), \
                mock.patch.object(updater, "_short_path", lambda p: p):
            path, mode = updater.write_swap_script(
                current_exe=self.target, staged=self.staged, latest="1.4.0", pid=1234,
                result_path=self.result, state_path=self.state, expected_size=999)
        self.addCleanup(os.unlink, path)
        self.assertEqual(mode, "bat")
        raw = open(path, "rb").read()
        self.assertIn(self.target.encode("cp949"), raw)
        self.assertNotIn(KOREAN_EXE.encode("utf-8"), raw)

    def test_swap_command_uses_powershell_file_switch(self):
        cmd = updater.swap_command("/tmp/x.ps1", "ps1")
        self.assertIn("-ExecutionPolicy", cmd)
        self.assertEqual(cmd[-1], "/tmp/x.ps1")
        self.assertEqual(updater.swap_command("/tmp/x.bat", "bat")[0], "cmd.exe")


class SwapResultReportingTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._old_result = updater._RESULT_PATH
        self._old_state = updater._STATE_PATH
        self._old_appdir = config.APP_DIR
        updater._RESULT_PATH = os.path.join(self.dir, "update_result.json")
        updater._STATE_PATH = os.path.join(self.dir, "update_state.json")
        config.APP_DIR = self.dir

    def tearDown(self):
        updater._RESULT_PATH = self._old_result
        updater._STATE_PATH = self._old_state
        config.APP_DIR = self._old_appdir
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_failed_swap_is_reported_loudly_with_the_real_reason(self):
        payload = {"ok": False, "target_version": "1.4.0", "target_path": "C:/x/한글.exe",
                   "expected_size": 100, "placed_size": 0, "step": "swap_rename",
                   "error": "Access to the path is denied."}
        with open(updater._RESULT_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        sent = []
        with mock.patch.object(updater, "remote_log", lambda e, d, **k: sent.append((e, d))):
            updater._report_previous_swap()
        self.assertEqual(sent[0][0], "update_swap_failed")
        self.assertIn("Access to the path is denied.", sent[0][1])
        self.assertIn("Access to the path is denied.", updater._load_state()["last_error"])

    def test_successful_swap_is_reported_too(self):
        with open(updater._RESULT_PATH, "w", encoding="utf-8") as f:
            json.dump({"ok": True, "target_version": "1.4.0", "placed_size": 100}, f)
        sent = []
        with mock.patch.object(updater, "remote_log", lambda e, d, **k: sent.append((e, d))):
            updater._report_previous_swap()
        self.assertEqual(sent[0][0], "update_swap_ok")
        self.assertFalse(os.path.exists(updater._RESULT_PATH), "결과 파일은 읽고 나면 지워야 한다")

    def test_backoff_notice_is_logged_once_per_run_with_a_manual_link(self):
        """51번 반복되던 무의미한 백오프 줄 대신, 실행당 한 번 + 고객 화면 안내."""
        updater._save_state({"target": "1.4.0", "fail_count": 1, "last_error": "denied"})
        shown, sent = [], []
        t = updater.UpdaterThread(status_cb=shown.append)
        with mock.patch.object(updater, "remote_log", lambda e, d, **k: sent.append((e, d))):
            t._notify_update_blocked("1.4.0", exe_url="https://example/insta-1.4.0.exe")
            t._notify_update_blocked("1.4.0", exe_url="https://example/insta-1.4.0.exe")
        self.assertEqual(len(sent), 1)
        self.assertIn("denied", sent[0][1])
        self.assertEqual(len(shown), 1)
        self.assertIn("https://example/insta-1.4.0.exe", shown[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
