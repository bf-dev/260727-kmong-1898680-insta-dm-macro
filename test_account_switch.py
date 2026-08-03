# -*- coding: utf-8 -*-
"""계정 전환 회귀 테스트 (kmong 1898680, v1.4.0).

고객이 세 번 리포트한 문제: "A아이디에서 다른 계정별명으로 계정전환하는게 아직도 고정으로
되있고 전환이 되지 않고 있습니다". 실측 로그가 이걸 직접 증명한다:

    04:51:08 [login_reused] account=mightysun_09   user=mightysun_09
    04:55:19 [login_reused] account=mightyjimin    user=mightysun_09   <- 별명 != 실제 계정
    04:57:58 [login_ok]     account=xxtwinklebeamxx user=xxtwinklebeamxx

여기서 검증하는 것:
  1) 별명 -> 실제 계정(ds_user_id) 매핑이 저장되고, 재사용 시 불일치면 강제 재로그인 신호가 난다.
  2) 아직 매핑이 없어도, 그 계정이 이미 '다른 별명' 것이면 불일치로 본다(= 이전 세션 물려받음).
  3) `config.profile_dir_for()` 는 어떤 별명 조합에서도 폴더가 겹치지 않는다
     (이전 세션들이 "겹친다/안 겹친다"로 엇갈렸던 부분을 실제로 확인해서 못 박는다).
"""

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

    def test_wrong_account_on_a_bound_label_is_a_mismatch(self):
        """고객 로그의 정확한 사고: 별명 mightyjimin 인데 세션은 mightysun_09."""
        account_binding.bind("mightyjimin", "222", "mightyjimin")
        verdict, detail = account_binding.check("mightyjimin", "111", "mightysun_09")
        self.assertEqual(verdict, "mismatch")
        self.assertIn("mightysun_09", detail)

    def test_unbound_label_holding_another_labels_session_is_a_mismatch(self):
        """새 별명인데 이전 별명의 세션을 그대로 물려받은 경우 - 여기가 진짜 위험 지점."""
        account_binding.bind("mightysun_09", "111", "mightysun_09")
        verdict, detail = account_binding.check("mightyjimin", "111", "mightysun_09")
        self.assertEqual(verdict, "mismatch")
        self.assertIn("mightysun_09", detail)
        self.assertIsNone(account_binding.get("mightyjimin"),
                          "불일치일 때 새 별명을 그 계정으로 묶어버리면 안 된다")

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
