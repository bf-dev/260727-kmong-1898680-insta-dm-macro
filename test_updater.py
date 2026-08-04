# -*- coding: utf-8 -*-
"""업데이터 회귀 테스트 (kmong 1898680, v1.4.0 + v1.7.0).

v1.4.0 배경: 고객은 1.3.1 -> 1.3.2 자동 업데이트가 51번 연속 실패해 4시간 넘게 옛 버전에
묶여 있었다. 스왑 스크립트를 UTF-8 .bat 으로 썼는데 cmd.exe 는 배치 파일을 OEM 코드페이지
(한국어 윈도우 = cp949)로 읽기 때문에, 한글 exe 경로(`인스타DM매크로.exe`)가 깨져 copy 가
조용히 실패했다. 여기서 검증하는 것:
  1) 스왑 대상 경로가 sys.executable 에서 나온다(파일명 하드코딩 없음) - 한글 파일명 포함.
  2) PowerShell 스크립트는 UTF-8 BOM 으로 저장된다(PS 5.1 이 유니코드로 읽는 유일한 조건).
  3) .bat 폴백은 OEM 코드페이지로 저장된다(UTF-8 로 쓰면 안 된다).
  4) 실패 결과 파일이 진단으로 보고되고, 실패 사유가 마커에 남는다.

v1.7.0 배경: 같은 사고가 1.5.0 -> 1.6.0 에서 반복됐고, 이번에는 사유가 통째로 사라졌다
(`마지막 실패: 사유 미기록`). 원인은 다운로드 **전에** fail_count 를 올리는 코드였다:
38MB 를 받는 도중 고객이 프로그램을 닫으면 '사유 없는 실패 1회' 만 남고 6시간 백오프가
걸린다. 여기서 검증하는 것:
  5) 시도를 시작해도 하드 실패로 세지 않는다(단계만 기록).
  6) 단계 도중 종료된 시도는 '중단' 으로 사유가 남고, 백오프 없이 즉시 재시도된다.
  7) 백오프에 상한이 있고 하드 실패 횟수에 따라 단계적으로 늘어난다.
  8) 사유는 어떤 상태에서도 비어 있을 수 없다("사유 미기록" 문자열 자체가 코드에 없다).
  9) 수동 다운로드 주소가 현재 버전을 가리킨다.
"""

import json
import os
import shutil
import sys
import tempfile
import time
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


class _StateTestCase(unittest.TestCase):
    """마커/결과 파일을 임시 폴더로 돌려놓는 공통 셋업."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._old = (updater._RESULT_PATH, updater._STATE_PATH, updater._TRAIL_PATH,
                     config.APP_DIR)
        updater._RESULT_PATH = os.path.join(self.dir, "update_result.json")
        updater._STATE_PATH = os.path.join(self.dir, "update_state.json")
        updater._TRAIL_PATH = os.path.join(self.dir, "update_trail.log")
        config.APP_DIR = self.dir
        self.sent = []

    def tearDown(self):
        (updater._RESULT_PATH, updater._STATE_PATH, updater._TRAIL_PATH,
         config.APP_DIR) = self._old
        shutil.rmtree(self.dir, ignore_errors=True)

    def capture(self):
        return mock.patch.object(updater, "remote_log",
                                 lambda e, d="", **k: self.sent.append((e, d)))


class NoReasonIsUnreachableTest(_StateTestCase):
    """v1.7.0 의 핵심: '사유 미기록' 이 나올 수 있는 경로가 없어야 한다."""

    def test_the_literal_string_is_gone_from_the_source(self):
        src = open(updater.__file__, encoding="utf-8").read()
        # 주석/독스트링에서 사고를 설명하는 건 괜찮지만, 사유로 **찍히는** 문자열이면 안 된다.
        import ast
        tree = ast.parse(src)
        docs = set()
        for node in ast.walk(tree):
            doc = ast.get_docstring(node, clean=False) if isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else None
            if doc:
                docs.add(doc)
        offenders = [n.value for n in ast.walk(tree)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)
                     and "사유 미기록" in n.value and n.value not in docs]
        self.assertEqual(offenders, [], f"'사유 미기록' 이 아직 코드 문자열에 있다: {offenders}")

    def test_reason_is_never_empty_even_with_a_completely_empty_marker(self):
        updater._save_state({})
        reason = updater.failure_reason("1.7.0")
        self.assertTrue(reason.strip())
        self.assertIn("실측", reason)
        self.assertIn("exe=", reason)

    def test_reason_is_never_empty_when_the_marker_has_a_target_but_no_error(self):
        """1.5.0->1.6.0 고객 실측 상태 그대로: target + fail_count 만 있고 사유가 없다."""
        updater._save_state({"target": "1.6.0", "fail_count": 1, "last_attempt": time.time()})
        reason = updater.failure_reason("1.6.0")
        self.assertNotIn("사유 미기록", reason)
        self.assertIn("기록된 예외 없음", reason)
        self.assertIn("하드실패=1", reason)

    def test_recorded_failure_always_stores_something_even_for_an_empty_detail(self):
        updater._record_failure("1.7.0", "", hard=True)
        self.assertTrue(updater._load_state()["last_error"].strip())

    def test_blocked_notice_carries_the_reason_to_the_server_and_the_banner(self):
        updater._save_state({"target": "1.7.0", "fail_count": 2,
                             "last_error": "Access to the path is denied.",
                             "last_attempt": time.time()})
        shown, banner = [], []
        t = updater.UpdaterThread(status_cb=shown.append,
                                  blocked_cb=lambda *a: banner.append(a))
        with self.capture():
            t._notify_update_blocked("1.7.0", exe_url="https://example/x.zip")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], "update_skip_backoff")
        self.assertIn("Access to the path is denied.", self.sent[0][1])
        self.assertIn("실측", self.sent[0][1])
        self.assertEqual(len(banner), 1)
        self.assertEqual(banner[0][0], "1.7.0")
        self.assertIn("https://example/x.zip", banner[0][2])
        self.assertIn("https://example/x.zip", shown[0])


class AttemptIsNotAFailureTest(_StateTestCase):
    """다운로드를 시작한 것만으로 '실패' 로 세면 안 된다 (v1.6.0 의 정확한 버그)."""

    def test_starting_an_attempt_does_not_increment_fail_count(self):
        updater._set_phase("1.7.0", "download", exe_url="https://example/x.exe")
        state = updater._load_state()
        self.assertEqual(state["fail_count"], 0)
        self.assertEqual(state["phase"], "download")
        self.assertEqual(state["attempts"], 1)
        self.assertTrue(updater._should_attempt("1.7.0"), "실패가 없는데 백오프가 걸렸다")

    def test_process_killed_mid_download_is_interrupted_not_a_hard_failure(self):
        """고객 PC 에서 실제로 일어난 일: 38MB 받는 중에 프로그램이 닫혔다."""
        updater._set_phase("1.7.0", "download", exe_url="https://example/x.exe")
        with self.capture():
            info = updater._reconcile_interrupted_attempt()
        self.assertIsNotNone(info)
        self.assertFalse(info["hard"])
        state = updater._load_state()
        self.assertEqual(state["fail_count"], 0)
        self.assertEqual(state["interrupted"], 1)
        self.assertIn("중단됨", state["last_error"])
        self.assertIn("download", state["last_error"])
        self.assertTrue(updater._should_attempt("1.7.0"),
                        "중단은 실패가 아니므로 곧바로 다시 시도해야 한다")
        self.assertEqual(self.sent[0][0], "update_attempt_interrupted")

    def test_swap_script_that_left_no_result_is_a_hard_failure_with_a_probe(self):
        updater._set_phase("1.7.0", "swap_launched")
        with self.capture():
            info = updater._reconcile_interrupted_attempt()
        self.assertTrue(info["hard"])
        state = updater._load_state()
        self.assertEqual(state["fail_count"], 1)
        self.assertIn("결과 파일을 남기지 않고", state["last_error"])
        self.assertIn("exe=", state["last_error"])
        self.assertEqual(self.sent[0][0], "update_swap_no_result")

    def test_a_terminal_phase_is_not_reconciled_twice(self):
        updater._set_phase("1.7.0", "download")
        with self.capture():
            updater._reconcile_interrupted_attempt()
            second = updater._reconcile_interrupted_attempt()
        self.assertIsNone(second, "이미 정리한 단계를 또 중단으로 세면 안 된다")
        self.assertEqual(updater._load_state()["interrupted"], 1)


class BoundedBackoffTest(_StateTestCase):
    def test_backoff_grows_but_is_capped(self):
        steps = [updater._backoff_for(n) for n in range(1, 9)]
        self.assertEqual(steps[0], updater.BACKOFF_STEPS[0])
        self.assertTrue(all(b <= updater.BACKOFF_STEPS[-1] for b in steps))
        self.assertEqual(steps[-1], updater.BACKOFF_STEPS[-1])
        self.assertEqual(sorted(steps), steps, "백오프는 단조 증가해야 한다")
        self.assertLessEqual(updater.BACKOFF_STEPS[0], 30 * 60,
                             "첫 재시도는 30분 안에 와야 한다(하루 종일 묶이면 안 된다)")

    def test_first_hard_failure_retries_after_the_first_step_not_six_hours(self):
        updater._save_state({"target": "1.7.0", "fail_count": 1, "last_error": "x",
                             "last_attempt": time.time() - updater.BACKOFF_STEPS[0] - 5})
        self.assertTrue(updater._should_attempt("1.7.0"))

    def test_backoff_holds_inside_the_window(self):
        updater._save_state({"target": "1.7.0", "fail_count": 1, "last_error": "x",
                             "last_attempt": time.time()})
        self.assertFalse(updater._should_attempt("1.7.0"))

    def test_manual_force_ignores_the_backoff(self):
        updater._save_state({"target": "1.7.0", "fail_count": 99, "last_error": "x",
                             "last_attempt": time.time()})
        self.assertFalse(updater._should_attempt("1.7.0"))
        self.assertTrue(updater._should_attempt("1.7.0", force=True),
                        "[지금 업데이트] 는 백오프를 무시해야 한다")

    def test_giving_up_is_bounded_and_reported(self):
        updater._save_state({"target": "1.7.0", "fail_count": updater.MAX_HARD_FAILURES,
                             "last_error": "x", "last_attempt": 0})
        self.assertFalse(updater._should_attempt("1.7.0"))
        self.assertTrue(updater.gave_up("1.7.0"))

    def test_a_new_target_version_resets_the_backoff(self):
        updater._save_state({"target": "1.6.0", "fail_count": 9, "last_attempt": time.time()})
        self.assertTrue(updater._should_attempt("1.7.0"),
                        "새 버전이 나오면 옛 목표의 백오프에 묶이면 안 된다")


class ManualDownloadUrlTest(unittest.TestCase):
    def test_manual_url_points_at_the_current_version(self):
        """v1.6.0 까지 여기가 1.5.0 zip 에 박혀 있었다."""
        self.assertIn(config.APP_VERSION.replace(".", ""), config.MANUAL_DOWNLOAD_URL)
        self.assertTrue(config.MANUAL_DOWNLOAD_URL.startswith(config.STATIC_BASE))


class SwapScriptResilienceTest(unittest.TestCase):
    def _ps(self):
        return updater.build_powershell_swap(
            r"C:\한글 폴더\인스타DM매크로.exe",
            r"C:\한글 폴더\인스타DM매크로.exe.update-1.7.0.tmp",
            "1.7.0", 1234, r"C:\state\update_result.json",
            r"C:\state\update_state.json", 999)

    def test_result_is_saved_in_a_finally_block(self):
        """어떤 예외가 나도 결과 파일은 남아야 한다. 안 남으면 다음 실행이 눈이 먼다."""
        body = self._ps()
        self.assertIn("} finally {", body)
        finally_at = body.index("} finally {")
        self.assertIn("Save-Result", body[finally_at:])

    def test_swap_retries_on_a_locked_file(self):
        body = self._ps()
        self.assertIn(f"$maxTries = {updater.SWAP_RETRIES}", body)
        self.assertIn("for ($t = 1; $t -le $maxTries; $t++)", body)
        self.assertGreaterEqual(updater.SWAP_RETRIES, 10)

    def test_app_is_relaunched_even_after_a_fatal_error(self):
        body = self._ps()
        finally_at = body.index("} finally {")
        self.assertIn("Start-Process -FilePath $target", body[finally_at:])

    def test_backup_is_restored_if_the_target_vanished(self):
        body = self._ps()
        finally_at = body.index("} finally {")
        self.assertIn("Move-Item -LiteralPath $backup -Destination $target",
                      body[finally_at:])


class CheckNowTest(_StateTestCase):
    """[지금 업데이트] 버튼: 결과를 콜백으로 돌려주고 GUI 를 막지 않는다."""

    def _thread(self):
        return updater.UpdaterThread(status_cb=lambda *_: None)

    def test_up_to_date_is_reported_back(self):
        t = self._thread()
        with mock.patch.object(t, "_fetch_version",
                               lambda: {"version": "0.0.1", "exeUrl": "https://x/a.exe"}), \
                self.capture():
            res = t._check_once(force=True, manual=True)
        self.assertEqual(res["status"], "up_to_date")

    def test_version_fetch_failure_returns_a_reason_not_silence(self):
        t = self._thread()

        def _boom():
            raise RuntimeError("boom")
        with mock.patch.object(t, "_fetch_version", _boom), self.capture():
            res = t._check_once(force=True, manual=True)
        self.assertEqual(res["status"], "error")
        self.assertIn("boom", res["detail"])
        self.assertTrue(res["download_url"])

    def test_dev_run_is_reported_instead_of_pretending_to_update(self):
        t = self._thread()
        with mock.patch.object(t, "_fetch_version",
                               lambda: {"version": "99.0.0", "exeUrl": "https://x/a.exe",
                                        "zipUrl": "https://x/a.zip"}), \
                mock.patch.object(sys, "frozen", False, create=True), self.capture():
            res = t._check_once(force=True, manual=True)
        self.assertEqual(res["status"], "dev")
        self.assertEqual(res["download_url"], "https://x/a.zip")

    def test_download_failure_records_a_hard_reason_and_surfaces_it(self):
        t = self._thread()
        banner = []
        t._blocked_cb = lambda *a: banner.append(a)
        with mock.patch.object(t, "_fetch_version",
                               lambda: {"version": "99.0.0", "exeUrl": "https://x/a.exe",
                                        "zipUrl": "https://x/a.zip"}), \
                mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(t, "_download_verified",
                                  lambda url: (None, "HTTP 404 (https://x/a.exe)")), \
                self.capture():
            res = t._check_once(force=True, manual=True)
        self.assertEqual(res["status"], "failed")
        self.assertIn("404", res["detail"])
        state = updater._load_state()
        self.assertEqual(state["fail_count"], 1)
        self.assertIn("404", state["last_error"])
        self.assertEqual(len(banner), 1)

    def test_blocked_state_is_returned_with_a_reason_for_the_dialog(self):
        updater._save_state({"target": "99.0.0", "fail_count": 3, "last_error": "denied",
                             "last_attempt": time.time()})
        t = self._thread()
        with mock.patch.object(t, "_fetch_version",
                               lambda: {"version": "99.0.0", "exeUrl": "https://x/a.exe",
                                        "zipUrl": "https://x/a.zip"}), \
                mock.patch.object(sys, "frozen", True, create=True), self.capture():
            res = t._check_once(force=False)
        self.assertEqual(res["status"], "blocked")
        self.assertIn("denied", res["detail"])
        self.assertEqual(res["download_url"], "https://x/a.zip")


class TrailTest(_StateTestCase):
    def test_phase_transitions_land_on_disk_for_the_next_run(self):
        updater._set_phase("1.7.0", "download")
        updater._set_phase("1.7.0", "stage")
        trail = updater.read_trail()
        self.assertIn("phase=download", trail)
        self.assertIn("phase=stage", trail)
        self.assertIn("pid=", trail)
        self.assertIn(f"v{config.APP_VERSION}", trail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
