# -*- coding: utf-8 -*-
"""v1.8.0 회귀 테스트 - [중지] 뒤에 [시작] 이 다시 돈다 + [시작] 은 절대 조용히 죽지 않는다.

고객 보고(2026-08-04, v1.6.0):
    "근데 보내는 중간에 중지했다가 재시작을 하고 싶어서 다시 '시작'버튼을 누르면 아무 반응이
     없어요! 그리고 진행상황 초기화하고 다시 시작을 눌러도 아무 반응이 없는 상태입니다!"

원인(재현 완료):
    `MacroEngine(threading.Thread)` 이 중지 플래그를 `self._stop = threading.Event()` 로
    들고 있었다. `_stop` 은 `threading.Thread` 의 **내부 메서드 이름**이다. 스레드가 끝나면
    CPython 의 `Thread._wait_for_tstate_lock()` 이 `self._stop()` 을 부르는데, 그 자리에
    Event 가 덮여 있어 `TypeError: 'Event' object is not callable` 이 난다.
    `is_alive()` 가 `_wait_for_tstate_lock()` 을 부르므로:

        한 번이라도 끝난 엔진에 `engine.is_alive()` 를 부르면 예외가 터진다.

    `on_start_click()` 의 첫 줄이 정확히 그 호출이었다. tkinter 는 버튼 콜백 예외를 stderr
    로만 흘리고 `--noconsole` exe 의 stderr 는 None 이라, 팝업도 로그도 없이 버튼만 죽었다.

고객 로그 그라운드 트루스(v1.1.0~v1.6.0, 5일):
    `macro_start` 앞에는 **예외 없이 항상** `process_start`(앱 재시작)가 있다. 한 프로세스
    안에서 두 번 실행된 적이 단 한 번도 없다. 고객은 매 실행마다 앱을 껐다 켜고 있었다.

여기서 못 박는 것:
  1) 끝난 엔진에 상태를 물어도 절대 예외가 나지 않는다.
  2) 시작 -> 중지 -> 시작 이 **반복해서** 다시 돈다(한 번만이 아니라).
  3) 초기화 -> 시작, 중지 -> 초기화 -> 시작 도 마찬가지.
  4) [시작] 은 어떤 경로로 끝나든 **반드시** 사유를 남긴다(화면 로그 + 사유코드).
  5) MacroEngine 이 Thread 내부 이름을 다시 덮으면 임포트 시점에 터진다.
"""

import gc
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest

import config
import excel_reader
import macro_engine
import progress_store


# ---------------------------------------------------------------- 공용 가짜 부품

class FakeDriver:
    current_url = "https://www.instagram.com/"

    def set_script_timeout(self, *a):
        pass

    def execute_async_script(self, *a, **k):
        return {"id": "42105781019", "username": "mugenboksa"}

    def execute_script(self, *a, **k):
        return None

    def get_cookies(self):
        return [{"name": "sessionid", "value": "x"},
                {"name": "ds_user_id", "value": "42105781019"}]

    def find_elements(self, *a, **k):
        return []

    def quit(self):
        pass


class SlowActions:
    """한 사람 처리에 시간이 걸린다 - 고객처럼 '진행 중'에 [중지] 를 누를 수 있게."""

    SELECTOR_MISS_DETAILS = ()
    follow_seconds = 1.5

    @classmethod
    def follow_profile(cls, session, url, log=None):
        time.sleep(cls.follow_seconds)
        return types.SimpleNamespace(ok=True, detail="followed")

    @staticmethod
    def send_dm(session, user, msg, log=None):
        return types.SimpleNamespace(ok=True, detail="sent")

    @staticmethod
    def detect_restriction(session):
        return None


def _rows(n=4):
    return [excel_reader.Row(row_no=i, username=f"u{i}",
                             url=f"https://www.instagram.com/u{i}/", message="hi")
            for i in range(1, n + 1)]


# ---------------------------------------------------- 1) 엔진 자체 (tkinter 불필요)

class EngineLifecycleTest(unittest.TestCase):
    """고객 버그의 **본체**. GUI 없이도 여기서 잡힌다."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._old_prog = config.PROGRESS_DIR
        config.PROGRESS_DIR = os.path.join(self.dir, "progress")
        # 사람처럼 보이려는 랜덤 대기(기본 수십 초)를 죽인다. 검증 대상은 대기가 아니라
        # '중지 -> 시작이 다시 도는가' 이다.
        self._old_delays = (config.DELAY_AFTER_FOLLOW_MIN, config.DELAY_AFTER_FOLLOW_MAX,
                            config.DELAY_BETWEEN_PEOPLE_MIN, config.DELAY_BETWEEN_PEOPLE_MAX)
        config.DELAY_AFTER_FOLLOW_MIN = config.DELAY_AFTER_FOLLOW_MAX = 0.01
        config.DELAY_BETWEEN_PEOPLE_MIN = config.DELAY_BETWEEN_PEOPLE_MAX = 0.01
        self.excel = os.path.join(self.dir, "list.xlsx")
        with open(self.excel, "wb") as f:
            f.write(b"x" * 10)

    def tearDown(self):
        config.PROGRESS_DIR = self._old_prog
        (config.DELAY_AFTER_FOLLOW_MIN, config.DELAY_AFTER_FOLLOW_MAX,
         config.DELAY_BETWEEN_PEOPLE_MIN, config.DELAY_BETWEEN_PEOPLE_MAX) = self._old_delays
        shutil.rmtree(self.dir, ignore_errors=True)

    def _engine(self, rows=None):
        return macro_engine.MacroEngine(
            FakeDriver(), rows or _rows(2), self.excel, "acct:42105781019",
            log_cb=lambda *_: None, daily_cap=50, actions=SlowActions)

    def test_asking_a_finished_engine_if_it_is_alive_does_not_raise(self):
        """v1.7.0 까지는 여기서 TypeError: 'Event' object is not callable 이 났다."""
        eng = self._engine()
        eng.start()
        eng.join(timeout=30)
        self.assertFalse(eng.is_running())
        # 진짜 원인 확인: Thread 의 내부 이름을 우리가 덮지 않았는가.
        eng.is_alive()          # 예외가 나면 그 자리에서 테스트 실패
        eng.join(timeout=1)     # join 도 같은 내부 경로를 쓴다

    def test_engine_never_shadows_a_thread_internal_name(self):
        eng = self._engine()
        clashes = [n for n in macro_engine.MacroEngine._THREAD_RESERVED
                   if n in eng.__dict__ and callable(getattr(threading.Thread, n, None))]
        self.assertEqual(clashes, [],
                         "Thread 내부 이름을 덮으면 스레드가 끝나는 순간 is_alive() 가 깨진다")

    def test_stop_mid_run_leaves_the_engine_cleanly_finished(self):
        eng = self._engine(_rows(5))
        eng.start()
        time.sleep(0.4)
        eng.stop()
        self.assertTrue(eng.stop_requested())
        eng.join(timeout=30)
        self.assertTrue(eng.finished, "run() 은 무슨 일이 있어도 finished 를 세워야 한다")
        self.assertFalse(eng.is_running())

    def test_a_crashing_engine_still_reports_finished(self):
        """엔진이 예외로 죽어도 다음 [시작] 이 '아직 도는 중' 으로 오판하면 안 된다."""
        class Boom(SlowActions):
            @staticmethod
            def follow_profile(session, url, log=None):
                raise KeyboardInterrupt("hard failure")   # except Exception 을 통과한다

        eng = macro_engine.MacroEngine(
            FakeDriver(), _rows(2), self.excel, "acct:42105781019",
            log_cb=lambda *_: None, daily_cap=50, actions=Boom)
        eng.start()
        eng.join(timeout=30)
        self.assertTrue(eng.finished)
        self.assertFalse(eng.is_running())
        eng.is_alive()

    def test_a_fresh_engine_runs_after_a_stopped_one(self):
        """중지된 실행 다음에 새 엔진을 걸면 남은 사람부터 실제로 처리된다."""
        first = self._engine(_rows(5))
        first.start()
        time.sleep(0.4)
        first.stop()
        first.join(timeout=30)
        done_after_stop = progress_store.load_done_rows(self.excel, "acct:42105781019")

        second = self._engine(_rows(5))
        second.start()
        second.join(timeout=60)
        done_after_resume = progress_store.load_done_rows(self.excel, "acct:42105781019")
        self.assertGreater(len(done_after_resume), len(done_after_stop),
                           "중지 뒤 새 실행이 남은 사람을 이어서 처리해야 한다")
        self.assertEqual(done_after_resume, {1, 2, 3, 4, 5})


# ------------------------------------------- 2) 진짜 App + 진짜 tkinter 로 버튼을 누른다

def _make_app(tmpdir, excel, rows=None):
    """진짜 `App` 을 진짜 Tk 창 위에 올린다. 버튼 핸들러를 그대로 호출하기 위해서다."""
    import tkinter as tk
    import app as app_module
    import bridge
    import instagram_actions as ig
    import updater

    ig.session_is_live = lambda d: True
    ig.resolve_identity = lambda d: {"user_id": "42105781019",
                                     "username": "mugenboksa", "source": "api"}
    ig.identity_report = lambda d: "api=x viewer=x cookie=x"
    bridge.remote_log = lambda *a, **k: None
    bridge.upload_run = lambda *a, **k: None
    updater.start_updater = lambda **k: None

    root = tk.Tk()
    root.withdraw()
    a = app_module.App(root)
    a.driver = FakeDriver()
    a.session = a.driver
    a.actions = SlowActions
    a.logged_in = True
    a.session_label = "mugenboksa"
    a.engine_var.set("browser")
    a.excel_var.set(excel)
    a.rows = rows or _rows(5)

    a.captured = []
    _orig = a._log

    def _cap(msg):
        a.captured.append(msg)
        _orig(msg)

    a._log = _cap
    return root, a


class _RecordingBox:
    """messagebox 대역. 뜬 팝업을 기록만 하고 창은 안 띄운다(테스트가 멈추지 않게)."""

    def __init__(self):
        self.shown = []

    def showerror(self, *a, **k):
        self.shown.append(("error",) + a)

    def showwarning(self, *a, **k):
        self.shown.append(("warning",) + a)

    def showinfo(self, *a, **k):
        self.shown.append(("info",) + a)

    def askyesno(self, *a, **k):
        self.shown.append(("askyesno",) + a)
        return True


class StartStopRestartTest(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.dir = tempfile.mkdtemp()
        self._old = (config.PROGRESS_DIR, config.ACCOUNT_BINDINGS_FILE, config.SETTINGS_FILE)
        config.PROGRESS_DIR = os.path.join(self.dir, "progress")
        config.ACCOUNT_BINDINGS_FILE = os.path.join(self.dir, "bindings.json")
        config.SETTINGS_FILE = os.path.join(self.dir, "settings.json")
        self.excel = os.path.join(self.dir, "list.xlsx")
        with open(self.excel, "wb") as f:
            f.write(b"x" * 10)
        self._old_box = app_module.messagebox
        self.box = _RecordingBox()
        app_module.messagebox = self.box
        # 사람 사이 대기를 없애 테스트가 빨리 끝나게 한다(로직은 그대로).
        self._old_delays = (config.DELAY_AFTER_FOLLOW_MIN, config.DELAY_AFTER_FOLLOW_MAX,
                            config.DELAY_BETWEEN_PEOPLE_MIN, config.DELAY_BETWEEN_PEOPLE_MAX)
        config.DELAY_AFTER_FOLLOW_MIN = config.DELAY_AFTER_FOLLOW_MAX = 0.01
        config.DELAY_BETWEEN_PEOPLE_MIN = config.DELAY_BETWEEN_PEOPLE_MAX = 0.01
        self.root, self.app = _make_app(self.dir, self.excel)

    def tearDown(self):
        # Tk 위젯을 참조하는 엔진 스레드가 살아 있는 채로 root 를 부수면 Tcl 이
        # "async handler deleted by the wrong thread" 로 프로세스를 죽인다(= CI 실패).
        # 반드시 먼저 멈추고 완전히 끝난 뒤에 창을 정리한다.
        try:
            if self.app.engine is not None:
                self.app.engine.stop()
                self._pump_until(self._engine_idle, timeout=90)
        except Exception:
            pass
        self.app.engine = None
        try:
            self.root.update()
            self.root.destroy()
        except Exception:
            pass
        self.root = None
        self.app = None
        gc.collect()
        self.app_module.messagebox = self._old_box
        (config.PROGRESS_DIR, config.ACCOUNT_BINDINGS_FILE, config.SETTINGS_FILE) = self._old
        (config.DELAY_AFTER_FOLLOW_MIN, config.DELAY_AFTER_FOLLOW_MAX,
         config.DELAY_BETWEEN_PEOPLE_MIN, config.DELAY_BETWEEN_PEOPLE_MAX) = self._old_delays
        shutil.rmtree(self.dir, ignore_errors=True)

    # ---- 도구
    def _pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            time.sleep(0.02)

    def _pump_until(self, cond, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            self.root.update()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def _engine_idle(self):
        return self.app.engine is None or not self.app.engine.is_running()

    def _press_start_and_expect_a_run(self, note):
        before = self.app.engine
        marker = len(self.app.captured)
        self.app.on_start_click()
        started = self._pump_until(
            lambda: self.app.engine is not None and self.app.engine is not before, timeout=60)
        self.assertTrue(started,
                        f"{note}: [시작] 이 새 실행을 걸지 못했습니다. "
                        f"로그={self.app.captured[marker:]} 사유={self.app._last_start_reason}")
        self.assertEqual(self.app._last_start_reason, "started", note)
        return self.app.engine

    # ---- 고객이 보고한 세 가지 흐름

    def test_stop_mid_run_then_start_runs_again(self):
        """고객 문장 그대로: 보내는 중간에 중지 -> 다시 [시작]."""
        first = self._press_start_and_expect_a_run("첫 실행")
        self._pump(0.5)
        self.app.on_stop_click()
        self.assertTrue(self._pump_until(self._engine_idle, timeout=60), "중지가 끝나야 한다")
        second = self._press_start_and_expect_a_run("중지 뒤 재시작")
        self.assertIsNot(second, first, "새 엔진이 만들어져야 한다(스레드는 재시작 불가)")
        self.assertTrue(self._pump_until(lambda: second.is_running() or second.finished, 30))

    def test_start_immediately_after_stop_is_queued_not_ignored(self):
        """마무리가 아직 안 끝난 순간에 눌러도 '아무 반응 없음' 이면 안 된다."""
        first = self._press_start_and_expect_a_run("첫 실행")
        self._pump(0.3)
        self.app.on_stop_click()
        marker = len(self.app.captured)
        self.app.on_start_click()               # 엔진이 아직 마무리 중일 때
        self.assertTrue(self.app.captured[marker:], "누른 즉시 화면 로그가 남아야 한다")
        ok = self._pump_until(lambda: self.app.engine is not None
                              and self.app.engine is not first, timeout=90)
        self.assertTrue(ok, f"마무리 뒤 자동으로 이어서 시작해야 한다. "
                            f"로그={self.app.captured[marker:]}")

    def test_reset_progress_then_start_runs_from_the_top(self):
        first = self._press_start_and_expect_a_run("첫 실행")
        self.assertTrue(self._pump_until(self._engine_idle, timeout=120), "첫 실행이 끝나야 한다")
        self.app.on_reset_click()               # _RecordingBox.askyesno -> True
        self._pump(0.2)
        second = self._press_start_and_expect_a_run("초기화 뒤 시작")
        self.assertTrue(self._pump_until(self._engine_idle, timeout=120))
        done = progress_store.load_done_rows(self.excel, second.account_label)
        self.assertEqual(done, {1, 2, 3, 4, 5}, "초기화 뒤에는 1행부터 전부 다시 돌아야 한다")

    def test_stop_then_reset_then_start(self):
        self._press_start_and_expect_a_run("첫 실행")
        self._pump(0.5)
        self.app.on_stop_click()
        self.assertTrue(self._pump_until(self._engine_idle, timeout=60))
        self.app.on_reset_click()
        self._pump(0.2)
        self.assertEqual(progress_store.load_done_rows(self.excel, "acct:42105781019"), set())
        third = self._press_start_and_expect_a_run("중지 -> 초기화 -> 시작")
        self.assertTrue(self._pump_until(self._engine_idle, timeout=120))
        self.assertEqual(progress_store.load_done_rows(self.excel, third.account_label),
                         {1, 2, 3, 4, 5})

    def test_start_stop_start_works_repeatedly_not_just_once(self):
        """한 번만 고쳐진 게 아니라는 증거. 고객은 하루에 몇 번씩 멈췄다 다시 시작한다."""
        seen = []
        for cycle in range(1, 4):
            eng = self._press_start_and_expect_a_run(f"{cycle}번째 실행")
            seen.append(eng)
            self._pump(0.35)
            self.app.on_stop_click()
            self.assertTrue(self._pump_until(self._engine_idle, timeout=60),
                            f"{cycle}번째 중지가 끝나야 한다")
        self.assertEqual(len(set(map(id, seen))), 3, "매번 새 엔진이어야 한다")

    # ---- 하드 요구사항: [시작] 은 절대 조용히 아무것도 안 하지 않는다

    def test_start_always_leaves_a_reason_whatever_the_state(self):
        """어떤 상태에서 눌러도 (a) 사유코드가 남고 (b) 화면 로그가 늘어난다."""
        cases = []

        # 1) 로그인 세션이 없다
        self.app.session, self.app.logged_in, self.app.driver = None, False, None
        cases.append("세션 없음")
        # 2) 엑셀 행이 없다
        # 3) 이미 실행 중이다
        # 4) 이전 엔진이 이미 끝나 있다(고객이 겪은 그 상태)
        for name in cases:
            marker = len(self.app.captured)
            self.app._last_start_reason = None
            self.app.on_start_click()
            self._pump(0.3)
            self.assertIsNotNone(self.app._last_start_reason,
                                 f"{name}: [시작] 이 사유 없이 끝났습니다(= 죽은 버튼)")
            self.assertTrue(self.app.captured[marker:],
                            f"{name}: [시작] 이 화면 로그를 하나도 남기지 않았습니다")

        # 세션은 돌려놓고 나머지 상태들을 이어서 본다
        self.app.driver = FakeDriver()
        self.app.session = self.app.driver
        self.app.logged_in = True

        # 행이 없다 (그리고 엑셀 경로도 비어 있어 불러오기도 실패한다)
        self.app.rows = []
        self.app.excel_var.set("")
        marker = len(self.app.captured)
        self.app._last_start_reason = None
        self.app.on_start_click()
        self._pump(0.3)
        self.assertIsNotNone(self.app._last_start_reason, "행 없음: 사유가 남아야 한다")
        self.assertTrue(self.app.captured[marker:], "행 없음: 화면 로그가 남아야 한다")

        # 정상 실행 -> 실행 중에 한 번 더 누른다
        self.app.excel_var.set(self.excel)
        self.app.rows = _rows(5)
        self._press_start_and_expect_a_run("정상 실행")
        marker = len(self.app.captured)
        self.app._last_start_reason = None
        self.app.on_start_click()
        self._pump(0.3)
        self.assertEqual(self.app._last_start_reason, "already_running")
        self.assertTrue(self.app.captured[marker:])

        # 실행이 끝난 뒤 다시 -> **고객이 겪은 바로 그 상태**
        self.app.on_stop_click()
        self.assertTrue(self._pump_until(self._engine_idle, timeout=60))
        marker = len(self.app.captured)
        self.app._last_start_reason = None
        self.app.on_start_click()
        self._pump(0.5)
        self.assertEqual(self.app._last_start_reason, "started",
                         "끝난 엔진이 남아 있어도 [시작] 은 다시 돌아야 한다")
        self.assertTrue(self.app.captured[marker:])

    def test_a_dead_engine_object_can_never_kill_the_start_button(self):
        """엔진 자리에 무엇이 들어 있어도 [시작] 은 반응한다(v1.7.0 은 여기서 즉사했다)."""
        class Landmine:
            def is_running(self):
                raise RuntimeError("boom")

            def stop(self):
                raise RuntimeError("boom")

        self.app.engine = Landmine()
        marker = len(self.app.captured)
        self.app._last_start_reason = None
        self.app.on_start_click()               # _safe 없이 직접 호출해도 죽으면 안 된다
        self._pump(0.5)
        self.assertTrue(self.app.captured[marker:],
                        "예외가 나도 사용자에게 보이는 흔적이 남아야 한다")

    def test_stop_with_nothing_running_still_answers_the_user(self):
        marker = len(self.app.captured)
        self.app.on_stop_click()
        self._pump(0.2)
        self.assertTrue(self.app.captured[marker:], "[중지] 도 무반응이면 안 된다")

    def test_a_raising_handler_is_surfaced_not_swallowed(self):
        """`_safe` 로 감싼 버튼은 예외를 팝업 + 로그로 끌어낸다."""
        def boom():
            raise ValueError("테스트용 폭발")

        marker = len(self.app.captured)
        self.app._safe(boom, "boom")()
        self._pump(0.2)
        self.assertTrue(any("오류" in m for m in self.app.captured[marker:]),
                        "예외가 화면 로그에 남아야 한다")
        self.assertTrue(any(s[0] == "error" for s in self.box.shown),
                        "예외가 팝업으로도 떠야 한다")


if __name__ == "__main__":
    unittest.main(verbosity=2)
