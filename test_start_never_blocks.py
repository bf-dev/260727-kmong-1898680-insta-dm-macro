# -*- coding: utf-8 -*-
"""v1.5.0 회귀 테스트 - [시작] 은 계정이 달라졌다고 절대 막지 않는다 (kmong 1898680).

고객 실측 로그(2026-08-03, v1.4.0):

    05:19:26 [login_reused]            account=megenboksa  user=mightysun_09 uid=67584782851
    05:20:34 [start_blocked_uid_drift] account=megenboksa  was=67584782851 now=42105781019
    05:20:42 [login_mismatch_relogin]  account=megenboksa  live_uid=42105781019 live_user=mugenboksa
    05:21:14 [login_ok]                account=megenboksa  user=mightysun_09 uid=67584782851
    05:21:37 [start_blocked_uid_drift] account=megenboksa  was=67584782851 now=42105781019
    05:21:47 [start_blocked_uid_drift] account=megenboksa  was=67584782851 now=42105781019

읽는 법: 앱을 열면 세션이 부모 계정(mightysun_09)으로 잡히고, 고객이 크롬 창에서 인스타
자체 '계정 전환'으로 서브계정(mugenboksa)으로 바꾼 뒤 [시작] 을 누른다. v1.4.0 은 그
정상 동작을 '변조'로 보고 차단 팝업을 띄웠고, 고객이 [로그인/계정 전환] 을 누르면 세션을
지우고 재로그인 -> 다시 전환 -> 다시 차단으로 **무한 루프**가 됐다(= 프로그램 사용 불가).

여기서 못 박는 것:
  1) 계정이 달라져도 `_resolve_run_account` 는 messagebox 를 절대 띄우지 않는다.
  2) 실행은 **지금 실제로 동작 중인 계정**으로 계속된다(그 계정으로 팔로우/DM 이 나가므로).
  3) 진행상황 키가 별명 -> 계정 id 로 바뀔 때 예전 기록을 승계한다(중복 DM 방지).
"""

import json
import os
import shutil
import tempfile
import unittest

import account_binding
import config
import progress_store


class _Var:
    def __init__(self, value=""):
        self._v = value

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


class _FakeDriver:
    """지금 이 크롬 창이 '실제로' 어느 계정으로 동작 중인가를 흉내낸다.

    쿠키(ds_user_id)는 부모 계정(67584782851)에 머물러 있고, 실제 실행 계정은
    계정 전환 후의 서브계정(42105781019)이다 - 고객 상황 그대로.
    """

    current_url = "https://www.instagram.com/"

    def __init__(self, acting_id, acting_user, cookie_id="67584782851"):
        self.acting_id, self.acting_user = acting_id, acting_user
        self.cookie_id = cookie_id

    def set_script_timeout(self, *_a):
        pass

    def execute_async_script(self, *_a, **_k):
        return {"id": self.acting_id, "username": self.acting_user}

    def execute_script(self, *_a, **_k):
        return None

    def get_cookies(self):
        return [{"name": "sessionid", "value": "x"},
                {"name": "ds_user_id", "value": self.cookie_id}]

    def find_elements(self, *_a, **_k):
        return []


class _Blocked(AssertionError):
    pass


class _NoDialogs:
    """messagebox 대체품 - 하나라도 뜨면 테스트가 그 자리에서 깨진다."""

    @staticmethod
    def showerror(*a, **k):
        raise _Blocked(f"[시작] 이 차단 팝업을 띄웠습니다: {a}")

    @staticmethod
    def showwarning(*a, **k):
        raise _Blocked(f"[시작] 이 경고 팝업을 띄웠습니다: {a}")

    @staticmethod
    def showinfo(*a, **k):
        raise _Blocked(f"[시작] 이 안내 팝업을 띄웠습니다: {a}")

    @staticmethod
    def askyesno(*a, **k):
        raise _Blocked(f"[시작] 이 확인 팝업을 띄웠습니다: {a}")


class _StubApp:
    """tkinter 창 없이 App 의 [시작] 직전 경로만 그대로 실행하기 위한 최소 껍데기."""

    def __init__(self, driver, label, excel_path):
        self.session = driver
        self.engine_var = _Var("browser")
        self.excel_var = _Var(excel_path)
        self.session_label = label
        self.session_user_id = None
        self.session_username = None
        self.logs = []
        self.live = []

    def _log(self, msg):
        self.logs.append(msg)

    def _set_live_account(self, ident, note=""):
        self.live.append(ident)

    def _refresh_account_choices(self, select_label=None):
        pass

    def _switch_failure_dump(self, label, detail):
        pass


class StartNeverBlocksTest(unittest.TestCase):
    def setUp(self):
        import app as app_module
        import instagram_actions as ig
        self.app_module = app_module
        self.ig = ig
        self.dir = tempfile.mkdtemp()
        self._old_bind = config.ACCOUNT_BINDINGS_FILE
        self._old_prog = config.PROGRESS_DIR
        config.ACCOUNT_BINDINGS_FILE = os.path.join(self.dir, "account_bindings.json")
        config.PROGRESS_DIR = os.path.join(self.dir, "progress")
        self._old_box = app_module.messagebox
        app_module.messagebox = _NoDialogs
        # 계정 전환 시도는 라이브 인스타가 필요하므로 여기서는 '실패'로 고정한다.
        # 전환이 실패해도 실행이 계속돼야 한다는 게 이 테스트의 핵심이다.
        self._old_switch = ig.switch_to_account
        ig.switch_to_account = lambda *a, **k: (False, "테스트: 전환 불가")
        self.excel = os.path.join(self.dir, "list.xlsx")
        with open(self.excel, "wb") as f:
            f.write(b"x" * 10)

    def tearDown(self):
        self.app_module.messagebox = self._old_box
        self.ig.switch_to_account = self._old_switch
        config.ACCOUNT_BINDINGS_FILE = self._old_bind
        config.PROGRESS_DIR = self._old_prog
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, label="megenboksa", acting=("42105781019", "mugenboksa")):
        stub = _StubApp(_FakeDriver(*acting), label, self.excel)
        run_key = self.app_module.App._resolve_run_account(stub, label)
        return stub, run_key

    def test_uid_drift_does_not_block_and_runs_as_the_acting_account(self):
        """고객 사고 그대로: 별명은 부모 계정에 묶여 있고 크롬 창은 서브계정으로 전환된 상태."""
        account_binding.bind("megenboksa", "67584782851", "mightysun_09")
        stub, run_key = self._run()                      # 팝업이 뜨면 여기서 예외
        self.assertEqual(run_key, "acct:42105781019",
                         "실제로 동작 중인 계정으로 실행되어야 한다")
        self.assertEqual(stub.session_user_id, "42105781019")
        self.assertEqual(account_binding.get("megenboksa")["username"], "mugenboksa",
                         "별명은 지금 실제로 도는 계정으로 다시 묶여야 한다")

    def test_matching_account_is_silent(self):
        account_binding.bind("mugenboksa", "42105781019", "mugenboksa")
        stub, run_key = self._run(label="mugenboksa")
        self.assertEqual(run_key, "acct:42105781019")

    def test_unknown_identity_still_starts(self):
        """계정을 아예 못 읽어도 [시작] 은 진행된다(막는 것보다 도는 게 낫다)."""
        class _Blind(_FakeDriver):
            current_url = "about:blank"

            def get_cookies(self):
                return []

        stub = _StubApp(_Blind("x", "y"), "megenboksa", self.excel)
        run_key = self.app_module.App._resolve_run_account(stub, "megenboksa")
        self.assertEqual(run_key, "megenboksa")

    def test_progress_is_carried_over_so_nobody_gets_a_second_dm(self):
        """별명 키 -> 계정 키로 옮길 때 승계가 없으면 이미 보낸 사람에게 또 보낸다."""
        account_binding.bind("megenboksa", "67584782851", "mightysun_09")
        for row in (1, 2, 3):
            progress_store.mark_done(self.excel, "megenboksa", row)
        _stub, run_key = self._run()
        self.assertEqual(progress_store.load_done_rows(self.excel, run_key), {1, 2, 3})

    def test_migration_never_overwrites_existing_account_progress(self):
        progress_store.mark_done(self.excel, "old_label", 1)
        progress_store.mark_done(self.excel, "acct:42105781019", 9)
        self.assertFalse(progress_store.migrate(self.excel, "old_label", "acct:42105781019"))
        self.assertEqual(progress_store.load_done_rows(self.excel, "acct:42105781019"), {9})


class IdentitySingleSourceTest(unittest.TestCase):
    """'실행 계정' 은 한 함수(resolve_identity)로만 읽고, 실제 동작 계정이 이긴다."""

    def test_acting_identity_wins_over_the_ds_user_id_cookie(self):
        import instagram_actions as ig
        ident = ig.resolve_identity(_FakeDriver("42105781019", "mugenboksa",
                                                cookie_id="67584782851"))
        self.assertEqual(ident["user_id"], "42105781019")
        self.assertEqual(ident["username"], "mugenboksa")
        self.assertEqual(ident["source"], "api")

    def test_falls_back_to_the_rendered_viewer_block(self):
        import instagram_actions as ig

        class _NoApi(_FakeDriver):
            def execute_async_script(self, *_a, **_k):
                raise RuntimeError("api 차단")

            def execute_script(self, *_a, **_k):
                return {"id": "45010010845", "username": "xxtwinklebeamxx"}

        ident = ig.resolve_identity(_NoApi("x", "y"))
        self.assertEqual((ident["user_id"], ident["source"]), ("45010010845", "viewer"))

    def test_falls_back_to_the_cookie_last(self):
        import instagram_actions as ig

        class _CookieOnly(_FakeDriver):
            def execute_async_script(self, *_a, **_k):
                raise RuntimeError("api 차단")

        ident = ig.resolve_identity(_CookieOnly("x", "y", cookie_id="67584782851"))
        self.assertEqual((ident["user_id"], ident["source"]), ("67584782851", "cookie"))

    def test_same_function_backs_every_call_site(self):
        """current_user_id / current_username / current_identity 가 갈라지면 안 된다."""
        import instagram_actions as ig
        d = _FakeDriver("42105781019", "mugenboksa")
        self.assertEqual(ig.current_user_id(d), "42105781019")
        self.assertEqual(ig.current_username(d), "mugenboksa")
        self.assertEqual(ig.current_identity(d), ("42105781019", "mugenboksa"))

    def test_run_key_is_the_account_not_the_nickname(self):
        self.assertEqual(account_binding.run_key("42105781019", "megenboksa"),
                         account_binding.run_key("42105781019", "mugenboksa"))
        self.assertEqual(account_binding.run_key(None, "megenboksa"), "megenboksa")


class NicknameDropdownTest(unittest.TestCase):
    """오타 별명(mugenboksa / megenboksa)이 조용히 새 프로필을 만드는 사고를 막는 UX."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._old = config.ACCOUNT_BINDINGS_FILE
        config.ACCOUNT_BINDINGS_FILE = os.path.join(self.dir, "b.json")

    def tearDown(self):
        config.ACCOUNT_BINDINGS_FILE = self._old
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_saved_labels_are_listed_with_the_account_they_are_bound_to(self):
        account_binding.bind("megenboksa", "67584782851", "mightysun_09")
        account_binding.bind("mugenboksa", "42105781019", "mugenboksa")
        self.assertEqual(account_binding.labels(), ["megenboksa", "mugenboksa"])
        self.assertEqual(account_binding.entries()["mugenboksa"]["username"], "mugenboksa")

    def test_a_one_letter_typo_is_flagged_as_a_close_match(self):
        import difflib
        known = ["mugenboksa", "mightysun_09"]
        self.assertEqual(difflib.get_close_matches("megenboksa", known, n=1, cutoff=0.8),
                         ["mugenboksa"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
