# -*- coding: utf-8 -*-
"""계정 전환 회귀 테스트 (kmong 1898680, v1.4.0).

고객이 세 번 리포트한 문제: "A아이디에서 다른 계정별명으로 계정전환하는게 아직도 고정으로
되있고 전환이 되지 않고 있습니다". 실측 로그가 이걸 직접 증명한다:

    04:51:08 [login_reused] account=mightysun_09   user=mightysun_09
    04:55:19 [login_reused] account=mightyjimin    user=mightysun_09   <- 별명 != 실제 계정
    04:57:58 [login_ok]     account=xxtwinklebeamxx user=xxtwinklebeamxx

여기서 검증하는 것:
  1) 별명 -> 실제 계정(ds_user_id) 매핑이 저장된다.
  2) v1.6.0: 저장값과 살아 있는 계정이 다르면 **살아 있는 계정이 이기고 저장값이 고쳐진다.**
     (v1.5.0 은 여기서 'mismatch' 를 내고 브라우저를 저장값 쪽으로 되돌려 고객을 자기가
      로그인한 서브계정에서 끌어냈다 - 2026-08-04 실측)
  3) `config.profile_dir_for()` 는 어떤 별명 조합에서도 폴더가 겹치지 않는다
     (이전 세션들이 "겹친다/안 겹친다"로 엇갈렸던 부분을 실제로 확인해서 못 박는다).
"""

import json
import os
import shutil
import tempfile
import unittest

import account_binding
import config


class ProfileDirCollisionTest(unittest.TestCase):
    """별명별 크롬 프로필 폴더가 실제로 안 겹치는지 - 실측으로 결론낸다."""

    LABELS = [
        "mightysun_09", "mightyjimin", "xxtwinklebeamxx",
        "Jimin", "Jimin2", "Jimin!", "jimin",
        "A 계정", "A계정", "A-계정",
        "계정1", "계정 1", "  계정1  ",
        "🙂", "🙃", "!!!", "@@@", "",
    ]

    def test_every_distinct_label_gets_a_distinct_profile_dir(self):
        seen = {}
        for label in self.LABELS:
            key = (label or "default").strip() or "default"
            path = config.profile_dir_for(label)
            if key in seen:                      # 같은 별명으로 정규화되는 경우는 같아도 된다
                self.assertEqual(seen[key], path)
                continue
            self.assertNotIn(path, seen.values(),
                             f"별명 '{label}' 의 프로필 폴더가 다른 별명과 겹칩니다: {path}")
            seen[key] = path

    def test_labels_that_sanitize_to_the_same_string_still_differ(self):
        """'A 계정' 과 'A계정' 은 안전문자만 남기면 같아진다 - 해시 접미사로 갈라져야 한다."""
        self.assertNotEqual(config.profile_dir_for("A 계정"), config.profile_dir_for("A계정"))
        self.assertNotEqual(config.profile_dir_for("Jimin"), config.profile_dir_for("Jimin!"))

    def test_emoji_only_labels_do_not_all_collapse_to_default(self):
        self.assertNotEqual(config.profile_dir_for("🙂"), config.profile_dir_for("🙃"))
        self.assertNotEqual(config.profile_dir_for("🙂"), config.profile_dir_for("default"))


class BindingTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._old = config.ACCOUNT_BINDINGS_FILE
        config.ACCOUNT_BINDINGS_FILE = os.path.join(self.dir, "account_bindings.json")

    def tearDown(self):
        config.ACCOUNT_BINDINGS_FILE = self._old
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_first_login_binds_the_label(self):
        verdict, _ = account_binding.check("mightysun_09", "111", "mightysun_09")
        self.assertEqual(verdict, "bound")
        self.assertEqual(account_binding.get("mightysun_09")["user_id"], "111")

    def test_same_account_on_the_same_label_is_ok(self):
        account_binding.bind("mightysun_09", "111", "mightysun_09")
        verdict, _ = account_binding.check("mightysun_09", "111", "mightysun_09")
        self.assertEqual(verdict, "ok")

    def test_live_account_wins_over_a_stale_binding(self):
        """v1.6.0 의 핵심 뒤집기.

        v1.5.0 까지 이 상황은 'mismatch' 였고, 호출자는 그걸 근거로 **브라우저를 저장값 쪽으로
        되돌렸다.** 고객이 직접 로그인한 서브계정에서 부모 계정으로 끌려 나간 실제 사고
        (2026-08-04 `login_switch_attempt want=mightysun_09 ok=True`)가 그것이다.
        이제는 살아 있는 계정이 이기고, 저장값이 고쳐진다.
        """
        account_binding.bind("mugenboksa", "67584782851", "mightysun_09")   # 오염된 기록
        verdict, detail = account_binding.check("mugenboksa", "42105781019", "mugenboksa")
        self.assertEqual(verdict, "rebound")
        self.assertIn("mugenboksa", detail)
        self.assertEqual(account_binding.get("mugenboksa")["user_id"], "42105781019",
                         "살아 있는 계정으로 기록이 고쳐져야 한다")
        self.assertEqual(account_binding.get("mugenboksa")["username"], "mugenboksa")

    def test_unbound_label_holding_another_labels_session_is_recorded_not_refused(self):
        """새 별명인데 이전 별명의 세션을 그대로 물려받은 경우.

        v1.5.0 은 여기서도 'mismatch' 를 내서 강제 재로그인을 유도했다. 그런데 이 고객은 부모
        계정 하나에 서브계정이 붙어 있어 같은 계정이 두 별명에 보이는 상황이 정상이다. 중복 DM
        은 진행상황 키(acct:<uid>)가 막으므로, 기록만 하고 막지 않는다.
        """
        account_binding.bind("mightysun_09", "111", "mightysun_09")
        verdict, detail = account_binding.check("mightyjimin", "111", "mightysun_09")
        self.assertEqual(verdict, "bound")
        self.assertIn("mightysun_09", detail)
        self.assertEqual(account_binding.get("mightyjimin")["user_id"], "111")
        self.assertEqual(account_binding.run_key("111", "mightyjimin"),
                         account_binding.run_key("111", "mightysun_09"),
                         "같은 계정이면 별명이 달라도 진행상황 키가 같아야 중복 DM 이 안 간다")

    def test_no_user_id_means_unknown_not_ok(self):
        verdict, _ = account_binding.check("x", None, None)
        self.assertEqual(verdict, "unknown")

    def test_username_change_updates_the_display_name_but_keeps_the_binding(self):
        account_binding.bind("a", "111", "old_name")
        verdict, _ = account_binding.check("a", "111", "new_name")
        self.assertEqual(verdict, "ok")
        self.assertEqual(account_binding.get("a")["username"], "new_name")

    def test_unbind_forces_a_fresh_binding(self):
        account_binding.bind("a", "111", "n")
        account_binding.unbind("a")
        self.assertIsNone(account_binding.get("a"))
        self.assertEqual(account_binding.check("a", "999", "other")[0], "bound")


class LegacyBindingMigrationTest(unittest.TestCase):
    """업그레이드 때 v1.5.0 이전 기록의 '계정' 만 비운다(별명은 남긴다).

    고객 1898680 의 오염된 기록은 v1.4.0 이 만들었다(2026-08-03 05:17:29
    `login_ok account=mugenboksa user=mightysun_09 uid=67584782851`). 그 기록이 v1.5.0 으로
    그대로 넘어와 다섯 번째 판을 열었다. 여섯 번째 판이 안 열리려면 업그레이드 시점에 낫는다.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._old = config.ACCOUNT_BINDINGS_FILE
        config.ACCOUNT_BINDINGS_FILE = os.path.join(self.dir, "account_bindings.json")

    def tearDown(self):
        config.ACCOUNT_BINDINGS_FILE = self._old
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write_legacy(self, entries):
        with open(config.ACCOUNT_BINDINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(entries, fh)

    def test_the_customers_poisoned_entry_is_reset_and_reported(self):
        self._write_legacy({"mugenboksa": {"user_id": "67584782851",
                                           "username": "mightysun_09", "bound_at": 1}})
        reset = account_binding.migrate_legacy()
        self.assertEqual([r["label"] for r in reset], ["mugenboksa"])
        self.assertEqual(reset[0]["username"], "mightysun_09",
                         "무엇을 지웠는지 화면/로그에 남길 수 있어야 한다(조용히 지우지 않는다)")
        entry = account_binding.get("mugenboksa")
        self.assertEqual(entry["user_id"], "")
        self.assertEqual(entry["reset_from_username"], "mightysun_09")

    def test_the_label_itself_survives_the_migration(self):
        self._write_legacy({"mugenboksa": {"user_id": "1", "username": "x"}})
        account_binding.migrate_legacy()
        self.assertIn("mugenboksa", account_binding.labels(),
                      "별명까지 사라지면 고객에게는 '계정이 통째로 없어진' 것으로 보인다")

    def test_migration_is_idempotent(self):
        self._write_legacy({"a": {"user_id": "1", "username": "x"}})
        self.assertEqual(len(account_binding.migrate_legacy()), 1)
        account_binding.bind("a", "9", "live")            # 재로그인으로 다시 채움
        self.assertEqual(account_binding.migrate_legacy(), [],
                         "두 번째 실행이 새로 채워진 값을 또 지우면 안 된다")
        self.assertEqual(account_binding.get("a")["user_id"], "9")

    def test_a_v2_binding_is_left_alone(self):
        account_binding.bind("a", "111", "n")
        self.assertEqual(account_binding.migrate_legacy(), [])
        self.assertEqual(account_binding.get("a")["user_id"], "111")

    def test_forget_account_clears_the_account_but_keeps_the_label(self):
        account_binding.bind("mugenboksa", "67584782851", "mightysun_09")
        old = account_binding.forget_account("mugenboksa")
        self.assertEqual(old["username"], "mightysun_09")
        self.assertIn("mugenboksa", account_binding.labels())
        self.assertEqual(account_binding.get("mugenboksa")["user_id"], "")
        # 지운 뒤 첫 로그인은 'bound'(새로 기억함) 여야 한다 - 'rebound' 경고가 아니라.
        self.assertEqual(account_binding.check("mugenboksa", "42105781019", "mugenboksa")[0],
                         "bound")


class _CookieDriver:
    def __init__(self, cookies):
        self._cookies = cookies

    def get_cookies(self):
        return self._cookies

    def execute_script(self, *_a, **_k):
        return None

    def find_elements(self, *_a, **_k):
        return []


class LiveIdentityTest(unittest.TestCase):
    def test_user_id_comes_from_the_ds_user_id_cookie(self):
        import instagram_actions as ig
        d = _CookieDriver([{"name": "sessionid", "value": "s"},
                           {"name": "ds_user_id", "value": "45010010845"}])
        self.assertEqual(ig.current_user_id(d), "45010010845")

    def test_missing_cookie_returns_none_rather_than_guessing(self):
        import instagram_actions as ig
        self.assertIsNone(ig.current_user_id(_CookieDriver([])))


if __name__ == "__main__":
    unittest.main(verbosity=2)
