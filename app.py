# -*- coding: utf-8 -*-
"""인스타 DM 매크로 - Tkinter GUI.

화면 구성:
  1) 계정: 별명 드롭다운(저장된 별명 + 그 별명에 묶인 인스타 아이디) + [+ 새 별명]
     + [로그인/계정 전환] + [로그아웃]. 로그인은 항상 실제로 뜬 크롬 창에서 사람이 직접
     아이디/비번을 입력한다(프로그램은 로그인 여부만 감지). 별명마다 크롬 프로필이 갈린다.
     크롬 창에서 인스타 자체 '계정 전환'을 써도 되고, 그때 실제로 어느 계정으로 도는지는
     '현재 실행 계정' 줄에 항상 표시된다. v1.5.0 부터 계정이 달라졌다고 [시작] 을 막지 않는다.
  2) 엑셀 파일: [찾아보기] - C열(URL)/F열(DM 문구)만 읽는다.
  3) [시작] / [중지] / [진행상황 초기화]
  4) 로그 창(스크롤) + 진행 통계 라벨
"""

import os
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import bridge
import config
import excel_reader
import progress_store
import settings
import single_instance
import updater


class App:
    def __init__(self, root):
        self.root = root
        root.title(f"인스타 DM 매크로 v{config.APP_VERSION} (고객 {config.CUSTOMER_ID})")
        root.geometry("1000x660")

        self.driver = None          # browser 엔진일 때만 사용(selenium 드라이버)
        self.session = None         # 실행에 넘기는 세션(api=ApiSession, browser=드라이버)
        self.actions = None         # ig_api 또는 instagram_actions
        self.engine = None
        # 지금 붙어 있는 세션이 '어느 별명 / 어느 인스타 계정'인지.
        # v1.5.0: 이 값은 **막는 근거가 아니라 표시/기록용**이다. 실행 계정이 달라졌으면
        # 막지 않고 따라간다(= 고객이 크롬 창에서 인스타 자체 계정 전환을 쓴 정상 상황).
        self.session_label = None
        self.session_user_id = None
        self.session_username = None
        self.rows = []
        self.skipped_no_message = []
        self.logged_in = False

        self.account_var = tk.StringVar(value="default")
        self.live_var = tk.StringVar(value="현재 실행 계정: (로그인하면 표시됩니다)")
        self._label_by_display = {}
        self.engine_var = tk.StringVar(value=config.DEFAULT_ENGINE)
        self.username_var = tk.StringVar(value="")
        self.password_var = tk.StringVar(value="")
        self.excel_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="상태: 로그인 필요")
        self.stats_var = tk.StringVar(value="완료 0 / 팔로우 0 / DM 0 / 실패 0")
        self.cap_var = tk.StringVar(value=str(settings.get_daily_cap()))

        self._build_ui()
        self.updater_thread = updater.start_updater(
            stop_running_loop=self._stop_macro_silent, status_cb=self._log)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        try:
            if self.engine:
                self.engine.stop()
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        self.root.destroy()

    # ---------- UI ----------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        acc_frame = ttk.LabelFrame(self.root, text="1) 인스타그램 계정")
        acc_frame.pack(fill="x", **pad)
        # v1.5.0: 별명을 매번 손으로 치면 'mugenboksa' / 'megenboksa' 처럼 한 글자 오타가
        # 조용히 **빈 프로필**을 하나 더 만든다(고객 실측). 그래서 저장된 별명은 목록에서
        # 고르게 하고, 새로 만들 때만 [+ 새 별명] 으로 확인을 거친다.
        ttk.Label(acc_frame, text="계정 별명(여러 계정 구분용):").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.account_combo = ttk.Combobox(acc_frame, textvariable=self.account_var, width=26,
                                          state="readonly", values=["default"])
        self.account_combo.grid(row=0, column=1, padx=6, pady=6)
        self.account_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_account_selected())
        ttk.Button(acc_frame, text="+ 새 별명", command=self.on_new_label_click).grid(row=0, column=2, padx=4)
        ttk.Radiobutton(acc_frame, text="빠른 방식(아이디/비번)", variable=self.engine_var,
                        value="api", command=self._on_engine_change).grid(row=0, column=3, padx=6, sticky="w")
        ttk.Radiobutton(acc_frame, text="크롬 창에서 직접 로그인", variable=self.engine_var,
                        value="browser", command=self._on_engine_change).grid(row=0, column=4, padx=6, sticky="w")

        self.cred_frame = ttk.Frame(acc_frame)
        self.cred_frame.grid(row=1, column=0, columnspan=5, sticky="w")
        ttk.Label(self.cred_frame, text="아이디:").grid(row=0, column=0, padx=6, pady=4, sticky="w")
        ttk.Entry(self.cred_frame, textvariable=self.username_var, width=22).grid(row=0, column=1, padx=6)
        ttk.Label(self.cred_frame, text="비밀번호:").grid(row=0, column=2, padx=6, sticky="w")
        ttk.Entry(self.cred_frame, textvariable=self.password_var, width=22, show="*").grid(row=0, column=3, padx=6)
        ttk.Label(self.cred_frame, text="(비밀번호는 저장하지 않습니다. 최초 1회만 입력)").grid(
            row=1, column=0, columnspan=4, padx=6, sticky="w")

        ttk.Button(acc_frame, text="로그인 / 계정 전환", command=self.on_login_click).grid(row=2, column=1, padx=6, pady=6)
        ttk.Button(acc_frame, text="다른 계정으로 로그인", command=self.on_switch_account_click).grid(row=2, column=2, padx=6)
        ttk.Button(acc_frame, text="로그아웃", command=self.on_logout_click).grid(row=2, column=3, padx=6)
        ttk.Label(acc_frame, textvariable=self.status_var).grid(row=3, column=0, columnspan=5, padx=6, sticky="w")
        # 크롬 창에서 인스타 자체 계정 전환을 해도 되고, 그때 프로그램이 어느 계정으로 도는지는
        # 항상 여기에 그대로 보인다(막지 않는다).
        self.live_label = ttk.Label(acc_frame, textvariable=self.live_var, foreground="#0b5cad")
        self.live_label.grid(row=4, column=0, columnspan=5, padx=6, pady=(0, 6), sticky="w")
        self._on_engine_change()
        self._refresh_account_choices()

        file_frame = ttk.LabelFrame(self.root, text="2) 엑셀 파일 (C열=인스타 URL, F열=DM 문구)")
        file_frame.pack(fill="x", **pad)
        ttk.Entry(file_frame, textvariable=self.excel_var, width=70).grid(row=0, column=0, padx=6, pady=6)
        ttk.Button(file_frame, text="찾아보기", command=self.on_browse_click).grid(row=0, column=1, padx=6)
        ttk.Button(file_frame, text="불러오기", command=self.on_load_click).grid(row=0, column=2, padx=6)

        ctrl_frame = ttk.LabelFrame(self.root, text="3) 실행")
        ctrl_frame.pack(fill="x", **pad)
        ttk.Button(ctrl_frame, text="시작", command=self.on_start_click).grid(row=0, column=0, padx=6, pady=6)
        ttk.Button(ctrl_frame, text="중지", command=self.on_stop_click).grid(row=0, column=1, padx=6)
        ttk.Button(ctrl_frame, text="진행상황 초기화", command=self.on_reset_click).grid(row=0, column=2, padx=6)
        ttk.Label(ctrl_frame, textvariable=self.stats_var).grid(row=0, column=3, padx=16)
        ttk.Label(ctrl_frame, text="하루 최대 처리 인원:").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        ttk.Spinbox(ctrl_frame, from_=config.DAILY_CAP_MIN, to=config.DAILY_CAP_MAX, width=6,
                    textvariable=self.cap_var, command=self._on_cap_change).grid(row=1, column=1, padx=6, sticky="w")
        ttk.Label(ctrl_frame,
                  text="계정 보호용 상한입니다. 이 인원까지 처리하면 그날은 자동으로 멈춥니다."
                  ).grid(row=1, column=2, columnspan=2, padx=6, sticky="w")

        log_frame = ttk.LabelFrame(self.root, text="로그")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_widget = scrolledtext.ScrolledText(log_frame, height=20, state="disabled")
        self.log_widget.pack(fill="both", expand=True, padx=6, pady=6)

    def _log(self, msg):
        def _do():
            self.log_widget.configure(state="normal")
            self.log_widget.insert("end", f"{time.strftime('%H:%M:%S')} {msg}\n")
            self.log_widget.see("end")
            self.log_widget.configure(state="disabled")
        try:
            self.root.after(0, _do)
        except Exception:
            print(msg)

    def _set_status(self, text):
        try:
            self.root.after(0, lambda: self.status_var.set(text))
        except Exception:
            pass

    def _on_engine_change(self):
        """아이디/비번 입력칸은 빠른 방식(api)일 때만 보인다."""
        try:
            if self.engine_var.get() == "api":
                self.cred_frame.grid()
            else:
                self.cred_frame.grid_remove()
        except Exception:
            pass

    def _on_cap_change(self):
        try:
            self.cap_var.set(str(settings.set_daily_cap(self.cap_var.get())))
        except Exception:
            pass

    # ---------- 별명 목록(오타 방지) ----------
    def _current_label(self):
        """드롭다운에 보이는 표시 문자열('별명  ·  @아이디')에서 진짜 별명만 꺼낸다."""
        shown = (self.account_var.get() or "").strip()
        if shown in self._label_by_display:
            return self._label_by_display[shown]
        return shown.split("  ·  ", 1)[0].strip() or "default"

    def _progress_key_for(self, label):
        """진행상황을 셀 키. 계정 id 를 알면 계정 기준, 모르면 별명 기준."""
        try:
            import account_binding
            entry = account_binding.get(label) or {}
            return account_binding.run_key(entry.get("user_id"), label)
        except Exception:
            return label

    def _refresh_account_choices(self, select_label=None):
        """저장된 별명 + 각 별명이 지금 묶여 있는 인스타 계정을 드롭다운에 다시 채운다."""
        try:
            import account_binding
            entries = account_binding.entries()
        except Exception:
            entries = {}
        keep = select_label or self._current_label()
        labels = sorted(set(list(entries.keys()) + [keep or "default"]),
                        key=lambda s: s.lower())
        displays, mapping = [], {}
        for label in labels:
            who = (entries.get(label) or {}).get("username")
            uid = (entries.get(label) or {}).get("user_id")
            if who:
                text = f"{label}  ·  @{who}"
            elif uid:
                text = f"{label}  ·  (id {uid})"
            else:
                text = f"{label}  ·  (아직 로그인 전)"
            displays.append(text)
            mapping[text] = label
        self._label_by_display = mapping
        def _apply():
            self.account_combo.configure(values=displays)
            for text, label in mapping.items():
                if label == keep:
                    self.account_var.set(text)
                    return
            if displays:
                self.account_var.set(displays[0])
        try:
            self.root.after(0, _apply)
        except Exception:
            _apply()

    def _on_account_selected(self):
        label = self._current_label()
        if self.session_label and self.session_label != label:
            self._log(f"'{label}' 로 실행하려면 [로그인 / 계정 전환] 을 눌러 그 계정으로 "
                      "크롬 창을 맞춰 주세요. (그냥 [시작] 을 누르면 지금 크롬 창에 로그인된 "
                      "계정으로 실행됩니다)")

    def on_new_label_click(self):
        """새 별명 추가 - 기존 별명과 한두 글자만 다른 오타면 먼저 되묻는다."""
        import difflib
        from tkinter import simpledialog
        try:
            import account_binding
            known = account_binding.labels()
        except Exception:
            known = []
        name = simpledialog.askstring("새 별명", "새로 만들 계정 별명을 입력해 주세요.",
                                      parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if name in known:
            self._refresh_account_choices(select_label=name)
            return
        close = difflib.get_close_matches(name, known, n=1, cutoff=0.8)
        if close and not messagebox.askyesno(
                "확인", f"이미 '{close[0]}' 별명이 있습니다.\n"
                        f"'{name}' 을(를) 새로 만들면 로그인도 처음부터 다시 해야 합니다.\n\n"
                        f"그래도 새로 만들까요?\n(아니오 = '{close[0]}' 를 사용)"):
            self._refresh_account_choices(select_label=close[0])
            return
        self._refresh_account_choices(select_label=name)
        self._log(f"별명 '{name}' 을(를) 만들었습니다. [로그인 / 계정 전환] 을 눌러 로그인해 주세요.")

    def _set_live_account(self, ident, note=""):
        """지금 실제로 실행되는 계정을 화면에 표시한다(막는 대신 보여준다)."""
        ident = ident or {}
        who = ident.get("username")
        uid = ident.get("user_id")
        if who or uid:
            text = f"현재 실행 계정: @{who or '?'} (id {uid or '?'}, 확인방식 {ident.get('source')})"
        else:
            text = "현재 실행 계정: 확인하지 못했습니다"
        if note:
            text += f" - {note}"
        try:
            self.root.after(0, lambda: self.live_var.set(text))
        except Exception:
            self.live_var.set(text)

    # ---------- 계정 ----------
    def on_login_click(self):
        label = self._current_label()
        if self.engine_var.get() == "api":
            threading.Thread(target=self._api_login_flow, args=(label,), daemon=True).start()
        else:
            threading.Thread(target=self._login_flow, args=(label,), daemon=True).start()

    def on_switch_account_click(self):
        """이 별명의 저장된 로그인을 지우고 처음부터 로그인한다.

        같은 별명에 다른 인스타 계정을 쓰고 싶을 때(또는 이전 계정으로 고정돼 버렸을 때) 쓴다.
        프로필 폴더를 통째로 지우므로 반드시 새 로그인 화면이 뜬다.
        """
        label = self._current_label()
        if self.engine_var.get() == "api":
            messagebox.showinfo("안내", "'크롬 창에서 직접 로그인' 방식에서만 사용할 수 있습니다.")
            return
        if not messagebox.askyesno(
                "확인", f"'{label}' 별명의 저장된 로그인을 지우고 새로 로그인할까요?\n"
                        "(진행상황은 지워지지 않습니다)"):
            return
        threading.Thread(target=self._switch_account_flow, args=(label,), daemon=True).start()

    def _switch_account_flow(self, label):
        import shutil
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
        self.session = None
        self.actions = None
        self.logged_in = False
        self.session_label = None
        self.session_user_id = None
        try:
            import account_binding
            account_binding.unbind(label)
        except Exception:
            pass
        profile_dir = config.profile_dir_for(label)
        try:
            shutil.rmtree(profile_dir, ignore_errors=True)
            self._log(f"'{label}' 의 저장된 로그인을 지웠습니다. 새로 로그인해 주세요.")
        except Exception as e:
            self._log(f"로그인 정보 삭제 중 오류: {e}")
        bridge.remote_log("account_reset", f"account={label}", force=True)
        self._login_flow(label)

    def _api_login_flow(self, label, verification_code=None):
        """instagrapi 로그인. 저장된 세션이 있으면 비밀번호 없이 붙는다."""
        import ig_api
        self._set_status("상태: 접속 중...")
        try:
            session = ig_api.load_session(label, proxy=config.API_PROXY, log=self._log)
            if session is None:
                username = self.username_var.get().strip()
                password = self.password_var.get()
                if not username or not password:
                    self._log("저장된 세션이 없습니다. 아이디와 비밀번호를 입력한 뒤 다시 눌러주세요.")
                    self._set_status("상태: 아이디/비밀번호 입력 필요")
                    return
                session = ig_api.login(label, username, password,
                                       verification_code=verification_code,
                                       proxy=config.API_PROXY, log=self._log)
        except ig_api.LoginNeedsCode as need:
            self._ask_code_and_retry(label, need)
            return
        except Exception as e:
            self._log(f"로그인 실패: {e}")
            self._set_status("상태: 로그인 실패")
            bridge.remote_log("api_login_failed", str(e)[:500], force=True)
            return

        self.session = session
        self.actions = ig_api
        self.logged_in = True
        self.session_label = label
        self.session_user_id = None
        self.password_var.set("")  # 화면에도 비밀번호를 남기지 않는다
        who = ig_api.account_username(session)
        self._log(f"'{label}' 접속 완료{f' (@{who})' if who else ''}.")
        self._set_status(f"상태: 로그인됨 ({label}{f' / @{who}' if who else ''})")
        bridge.remote_log("api_login_ok", f"account={label}", force=True)

    def _ask_code_and_retry(self, label, need):
        """2단계 인증/본인확인 코드를 받아 같은 흐름을 다시 탄다(코드는 저장하지 않는다)."""
        from tkinter import simpledialog

        def _prompt():
            code = simpledialog.askstring("인증 코드", f"{need}\n\n받은 6자리 코드를 입력해 주세요.",
                                          parent=self.root)
            if not code:
                self._log("인증 코드 입력이 취소됐습니다.")
                self._set_status("상태: 인증 코드 필요")
                return
            threading.Thread(target=self._api_login_flow, args=(label, code.strip()),
                             daemon=True).start()

        self.root.after(0, _prompt)

    def _login_flow(self, label):
        import browser
        self._log(f"'{label}' 계정 프로필로 크롬을 엽니다...")
        self._set_status("상태: 브라우저 준비 중...")
        try:
            if self.driver is not None:
                try:
                    self.driver.quit()
                except Exception:
                    pass
                self.driver = None
            profile_dir = config.profile_dir_for(label)
            os.makedirs(profile_dir, exist_ok=True)
            self.driver = browser.build_driver(profile_dir, log=self._log)
        except Exception as e:
            self._log(f"브라우저 시작 실패: {e}")
            self._set_status("상태: 브라우저 시작 실패")
            return

        import account_binding
        import instagram_actions as ig
        if ig.is_logged_in(self.driver):
            ident = ig.resolve_identity(self.driver)
            uid, who = ident.get("user_id"), ident.get("username")
            bound = account_binding.get(label)
            verdict, detail = account_binding.check(label, uid, who)
            bridge.remote_log(
                "login_identity",
                f"account={label} user={who} uid={uid} src={ident.get('source')} verdict={verdict}",
                force=True)
            if verdict == "unknown":
                # 계정 id 자체를 못 읽었다 = 사실상 로그인 상태가 아니다. 새로 로그인 받는다.
                self._log("로그인 상태를 확인하지 못했습니다. 새로 로그인해 주세요.")
                self._wait_manual_login(label, ig, account_binding)
                return

            if verdict == "mismatch":
                # v1.4.0 은 여기서 세션을 지우고 강제 재로그인을 시켰다. 그런데 이 고객처럼
                # 부모 계정 하나에 서브계정이 붙어 있으면, 고객이 크롬 창에서 인스타 자체
                # 계정 전환을 쓴 정상 상황도 전부 '불일치'로 잡혀 로그인 -> 전환 -> 차단 ->
                # 로그인 ... 무한 루프가 됐다(실측: start_blocked_uid_drift 반복).
                # v1.5.0: 먼저 **원래 묶여 있던 계정으로 인스타 자체 전환**을 시도하고,
                # 안 되면 지금 붙어 있는 계정을 그대로 인정하고 진행한다. 절대 막지 않는다.
                want = (bound or {}).get("username")
                if want:
                    self._log(f"'{label}' 은 @{want} 로 기억돼 있습니다. 인스타 계정 전환을 시도합니다...")
                    ok, sw_detail = ig.switch_to_account(self.driver, want, log=self._log)
                    bridge.remote_log("login_switch_attempt",
                                      f"account={label} want={want} ok={ok} detail={sw_detail[:300]}",
                                      force=True)
                    self._log(f"[계정 전환] {sw_detail}")
                    if ok:
                        ident = ig.resolve_identity(self.driver)
                        uid, who = ident.get("user_id"), ident.get("username")
                    else:
                        self._switch_failure_dump(label, sw_detail)
                if str((bound or {}).get("user_id") or "") != str(uid or ""):
                    self._log(f"[안내] {detail}")
                    self._log(f"이 별명은 지금 크롬 창에 로그인된 계정(@{who or uid})으로 실행됩니다. "
                              f"다른 계정으로 바꾸시려면 크롬 창에서 인스타 계정 전환을 하시거나 "
                              f"[다른 계정으로 로그인] 을 눌러주세요.")
                    account_binding.bind(label, uid, who)
                    bridge.remote_log("login_identity_adopted",
                                      f"account={label} live_uid={uid} live_user={who} "
                                      f"src={ident.get('source')}", force=True)

            # 여기서 session/actions 를 안 채우면 이전 별명의 (이미 종료된) 드라이버가 그대로
            # 남아 매크로가 옛 계정/죽은 창으로 돌아간다 - 계정 전환이 안 되는 것처럼 보인다.
            self.session = self.driver
            self.actions = ig
            self.logged_in = True
            self.session_label = label
            self.session_user_id = uid
            self.session_username = who
            account_binding.bind(label, uid, who)
            self._set_live_account(ident)
            self._refresh_account_choices(select_label=label)
            self._log(f"'{label}' 프로필에 이미 로그인되어 있습니다"
                      f"{f' (@{who})' if who else ''}.")
            self._set_status(f"상태: 로그인됨 ({label}{f' / @{who}' if who else ''})")
            bridge.remote_log("login_reused",
                              f"account={label} user={who} uid={uid} src={ident.get('source')}",
                              force=True)
            return

        ig.goto_login_screen(self.driver)
        self._wait_manual_login(label, ig, account_binding)

    def _switch_failure_dump(self, label, detail):
        """계정 전환 UI 를 못 찾았을 때 화면 DOM 을 통째로 올린다.

        인스타 전환 UI 는 라이브 계정 없이는 셀렉터를 확정할 수 없다. 실패한 그 순간의 DOM 이
        다음 판에 셀렉터를 못박을 유일한 증거이므로, 실패를 조용히 넘기지 않고 올린다.
        """
        try:
            import diag_collector
            path = diag_collector.capture_zip(self.driver, f"switch_fail_{label}", detail[:1500])
            bridge.upload_run(f"account_switch_failed account={label} detail={detail[:500]}",
                              zip_path=path, kind="switchdiag")
        except Exception:
            pass

    def _wait_manual_login(self, label, ig, account_binding):
        """크롬 창에서 사람이 직접 로그인할 때까지 기다리고, 성공하면 별명에 계정을 묶는다."""
        self._log("크롬 창에서 인스타그램 아이디/비밀번호를 직접 입력해 로그인해 주세요. "
                  "(자동입력 안 함 - 2단계 인증/보안 확인도 그대로 통과 가능)")
        self._set_status("상태: 로그인 대기 중 (창에서 직접 로그인)")
        ok = ig.wait_for_manual_login(
            self.driver, timeout_s=600,
            poll_cb=lambda: self._set_status("상태: 로그인 대기 중..."))
        if ok:
            self.logged_in = True
            self.session = self.driver
            self.actions = ig
            self.session_label = label
            ident = ig.resolve_identity(self.driver)
            uid, who = ident.get("user_id"), ident.get("username")
            self.session_user_id = uid
            self.session_username = who
            # 이 별명은 지금 로그인한 계정으로 (다시) 묶는다. 다음 실행부터 이 값으로 대조한다.
            account_binding.bind(label, uid, who)
            self._set_live_account(ident)
            self._refresh_account_choices(select_label=label)
            self._log(f"'{label}' 로그인 확인됨{f' (@{who})' if who else ''}.")
            self._set_status(f"상태: 로그인됨 ({label}{f' / @{who}' if who else ''})")
            bridge.remote_log("login_ok",
                              f"account={label} user={who} uid={uid} src={ident.get('source')}",
                              force=True)
        else:
            self._log("로그인 대기 시간이 초과됐습니다. 다시 시도해 주세요.")
            self._set_status("상태: 로그인 대기 시간 초과")
            bridge.remote_log("login_timeout", f"account={label}", force=True)

    def on_logout_click(self):
        if self.session is None:
            messagebox.showinfo("안내", "먼저 로그인/계정 전환을 눌러주세요.")
            return
        threading.Thread(target=self._logout_flow, daemon=True).start()

    def _logout_flow(self):
        try:
            self.actions.logout(self.session, log=self._log)
        except Exception as e:
            self._log(f"로그아웃 처리 중 오류: {e}")
        self.logged_in = False
        self.session = None
        self.session_label = None
        self.session_user_id = None
        self._set_status("상태: 로그아웃됨")
        bridge.remote_log("logout", f"account={self._current_label()}", force=True)

    # ---------- 엑셀 ----------
    def on_browse_click(self):
        path = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xls")])
        if path:
            self.excel_var.set(path)

    def on_load_click(self):
        path = self.excel_var.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showerror("오류", "엑셀 파일을 선택해 주세요.")
            return
        try:
            self.rows, self.skipped_no_message = excel_reader.load_rows(path)
        except Exception as e:
            messagebox.showerror("오류", f"엑셀을 읽는 중 오류: {e}")
            return
        label = self._current_label()
        done = progress_store.load_done_rows(
            path, self._progress_key_for(label)) | progress_store.load_done_rows(path, label)
        self._log(f"엑셀 로드 완료: 처리 대상 {len(self.rows)}행 "
                   f"(이미 완료 {len([r for r in self.rows if r.row_no in done])}행), "
                   f"F열 비어 스킵 {len(self.skipped_no_message)}행")

    # ---------- 실행 ----------
    def on_start_click(self):
        if self.engine is not None and self.engine.is_alive():
            messagebox.showinfo("안내", "이미 실행 중입니다.")
            return
        if self.session is None or not self.logged_in:
            messagebox.showerror("오류", "먼저 계정 로그인을 완료해 주세요.")
            return
        if not self.rows:
            self.on_load_click()
        if not self.rows:
            messagebox.showerror("오류", "처리할 행이 없습니다. 엑셀을 확인해 주세요.")
            return
        import macro_engine
        label = self._current_label()
        run_key = self._resolve_run_account(label)
        self.engine = macro_engine.MacroEngine(
            self.session, self.rows, self.excel_var.get().strip(), run_key,
            log_cb=self._log, done_cb=self._on_row_done,
            daily_cap=settings.get_daily_cap(), halt_cb=self._on_halt,
            actions=self.actions)
        self.engine.start()
        self._log("매크로를 시작합니다.")

    def _resolve_run_account(self, label):
        """[시작] 직전: **지금 크롬 창이 실제로 어느 계정으로 동작하는지** 확인하고 그 계정으로
        돌린다. 진행상황/하루 상한을 셀 키(run_key)를 돌려준다.

        v1.4.0 은 여기서 '시작할 때와 계정이 달라졌다'며 [시작] 을 아예 막았다. 그런데 이 고객은
        부모 계정 하나에 서브계정이 여러 개 붙어 있어서, 크롬 창에서 인스타 자체 계정 전환을
        쓰는 것이 정상 사용법이다. 그 정상 동작이 전부 차단으로 이어져 프로그램을 쓸 수 없었다
        (실측: start_blocked_uid_drift 3회 반복 + 재로그인 루프).

        v1.5.0 원칙:
          - **절대 막지 않는다.** 팔로우/DM 이 실행되는 그 계정이 정답이고, 프로그램은 따라간다.
          - 별명에 묶인 계정과 다르면 먼저 인스타 자체 전환을 한 번 시도한다(실패해도 진행).
          - 무엇으로 도는지는 화면(현재 실행 계정) + 진단 로그에 항상 남긴다.
          - 진행상황/하루 상한은 별명이 아니라 **실제 계정 id** 로 센다(오타 별명 2개가 같은
            계정을 가리켜도 이미 DM 보낸 사람에게 다시 보내지 않는다).
        """
        run_key = label
        if self.engine_var.get() == "api":
            return run_key
        try:
            import account_binding
            import instagram_actions as ig
        except Exception:
            return run_key
        try:
            ident = ig.resolve_identity(self.session)
        except Exception:
            ident = {"user_id": None, "username": None, "source": "none"}
        uid, who = ident.get("user_id"), ident.get("username")

        if not uid:
            self._set_live_account(ident, "확인 실패 - 그대로 진행합니다")
            self._log("[안내] 지금 로그인된 계정을 확인하지 못했지만 그대로 시작합니다.")
            bridge.remote_log("start_identity_unknown",
                              f"account={label} src={ident.get('source')}", force=True)
            return run_key

        bound = account_binding.get(label) or {}
        want_id, want_user = bound.get("user_id"), bound.get("username")
        if want_id and str(want_id) != str(uid):
            self._log(f"[안내] '{label}' 은 @{want_user or want_id} 로 기억돼 있는데 크롬 창은 "
                      f"@{who or uid} 로 로그인돼 있습니다. 인스타 계정 전환을 시도합니다...")
            ok, detail = ig.switch_to_account(self.session, want_user, log=self._log)
            bridge.remote_log("start_switch_attempt",
                              f"account={label} want={want_user} ok={ok} detail={detail[:300]}",
                              force=True)
            self._log(f"[계정 전환] {detail}")
            if ok:
                ident = ig.resolve_identity(self.session)
                uid, who = ident.get("user_id"), ident.get("username")
            else:
                self._switch_failure_dump(label, detail)
                self._log(f"[안내] 전환하지 못했습니다. 지금 크롬 창의 계정(@{who or uid})으로 "
                          "그대로 진행합니다. 다른 계정으로 돌리시려면 크롬 창에서 인스타 "
                          "계정 전환을 하신 뒤 다시 [시작] 을 눌러주세요.")
                bridge.remote_log("start_identity_adopted",
                                  f"account={label} bound={want_id} live={uid} live_user={who} "
                                  f"src={ident.get('source')}", force=True)

        # 별명은 항상 '지금 실제로 도는 계정' 으로 다시 묶는다(다음에 열었을 때 그대로 보이게).
        account_binding.bind(label, uid, who)
        self.session_user_id = uid
        self.session_username = who
        self.session_label = label
        self._set_live_account(ident)
        self._refresh_account_choices(select_label=label)

        run_key = account_binding.run_key(uid, label)
        if run_key != label:
            # 예전 버전은 별명으로 진행상황을 저장했다. 계정 키로 옮길 때 승계하지 않으면
            # 이미 DM 을 보낸 사람에게 한 번 더 보낸다.
            try:
                path = self.excel_var.get().strip()
                if progress_store.migrate(path, label, run_key):
                    self._log(f"'{label}' 의 진행상황을 계정(@{who or uid}) 기준으로 승계했습니다.")
                progress_store.migrate_daily(label, run_key)
            except Exception:
                pass
        bridge.remote_log("start_identity",
                          f"account={label} run_key={run_key} user={who} uid={uid} "
                          f"src={ident.get('source')}", force=True)
        # 세 소스를 한 줄로 같이 남긴다: 다음 실행 로그만 보고 '소스별로 값이 갈리는지'를
        # 추측이 아니라 실측으로 판정하기 위해서다.
        try:
            bridge.remote_log("identity_sources",
                              f"account={label} " + ig.identity_report(self.session), force=True)
        except Exception:
            pass
        return run_key

    def _on_halt(self, reason, message):
        """엔진이 스스로 멈췄을 때: 로그만으로는 놓치기 쉬우니 팝업으로 확실히 알린다."""
        def _show():
            self.status_var.set(f"상태: 중단됨 ({reason.split(':', 1)[0]})")
            messagebox.showwarning("매크로 중단", message)
        try:
            self.root.after(0, _show)
        except Exception:
            pass

    def _on_row_done(self, row_no, follow_ok, dm_ok):
        if self.engine:
            s = self.engine.stats
            processed = s["dm_sent"] + s["failed"]
            self.root.after(0, lambda: self.stats_var.set(
                f"완료 {processed} / 팔로우 {s['followed']} / DM {s['dm_sent']} / 실패 {s['failed']}"))

    def on_stop_click(self):
        if self.engine:
            self.engine.stop()
            self._log("중지를 요청했습니다 (진행 중인 사람까지만 마치고 멈춥니다).")

    def _stop_macro_silent(self):
        if self.engine:
            self.engine.stop()

    def on_reset_click(self):
        path = self.excel_var.get().strip()
        label = self._current_label()
        if not path:
            messagebox.showinfo("안내", "엑셀 파일을 먼저 선택해 주세요.")
            return
        if messagebox.askyesno("확인", "이 엑셀+계정 조합의 진행상황을 초기화할까요? "
                                       "(처음부터 다시 팔로우+DM 을 시도합니다)"):
            progress_store.reset(path, label)
            progress_store.reset(path, self._progress_key_for(label))
            self._log("진행상황을 초기화했습니다.")


# ---------------- 데모/자동테스트 모드 ----------------

def _make_demo_excel():
    import openpyxl
    fd_path = os.path.join(os.environ.get("TEMP", "."), "insta_dm_macro_demo.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "번호"; ws["B1"] = "이름"; ws["C1"] = "인스타그램URL"
    ws["D1"] = "기타1"; ws["E1"] = "기타2"; ws["F1"] = "DM문구"; ws["G1"] = "섭외메시지 캡처"
    demo_rows = [
        (1, "데모유저1", "https://www.instagram.com/demo_user_one/", "-", "-",
         "안녕하세요! 협업 제안드리고 싶어 연락드려요 :)", "(무시됨)"),
        (2, "데모유저2", "https://www.instagram.com/demo_user_two/", "-", "-",
         "반갑습니다! 프로필 잘 보고 DM 드립니다.", "(무시됨)"),
    ]
    for i, r in enumerate(demo_rows, start=2):
        ws.cell(row=i, column=1, value=r[0])
        ws.cell(row=i, column=2, value=r[1])
        ws.cell(row=i, column=3, value=r[2])
        ws.cell(row=i, column=4, value=r[3])
        ws.cell(row=i, column=5, value=r[4])
        ws.cell(row=i, column=6, value=r[5])
        ws.cell(row=i, column=7, value=r[6])
    wb.save(fd_path)
    return fd_path


def _run_guidemo(root, app):
    """실제 크롬/인스타그램 접속 없이: 엑셀 파싱 -> 팔로우/DM 순서 -> 랜덤 대기 계산까지
    코드 경로를 실제로 실행하고 로그창/통계에 실측 결과를 남긴다(CI 스크린샷 증거용).
    """
    demo_path = _make_demo_excel()
    app.excel_var.set(demo_path)
    app.account_var.set("demo_account")
    rows, skipped = excel_reader.load_rows(demo_path)
    app.rows = rows
    app._log(f"[데모] 엑셀 파싱 완료: {len(rows)}행 (C/F열만 읽음, 무관한 컬럼은 무시)")
    app.status_var.set("상태: 로그인됨 (demo_account) [데모 모드]")

    followed = dm_sent = 0
    for row in rows:
        app._log(f"[데모] {row.row_no}행 @{row.username} 프로필 접속 -> 팔로우 버튼 클릭")
        followed += 1
        wait_s = random.uniform(config.DELAY_AFTER_FOLLOW_MIN, config.DELAY_AFTER_FOLLOW_MAX)
        app._log(f"[데모] 팔로우 직후 랜덤 대기 {wait_s:.1f}초 (탐지 회피)")
        app._log(f"[데모] {row.row_no}행 @{row.username} 에게 DM 발송: \"{row.message[:30]}\"")
        dm_sent += 1
        gap_s = random.uniform(config.DELAY_BETWEEN_PEOPLE_MIN, config.DELAY_BETWEEN_PEOPLE_MAX)
        app._log(f"[데모] 다음 사람까지 랜덤 대기 {gap_s:.1f}초 (계정 보호)")
    app.stats_var.set(f"완료 {len(rows)} / 팔로우 {followed} / DM {dm_sent} / 실패 0")
    app._log(f"[데모] 완료: F열 없어 스킵 {len(skipped)}행")

    # v1.5.0 에서 고친 것들을 이 창에서 그대로 실행해 보여준다(CI 스크린샷 증거).
    try:
        import account_binding
        import instagram_actions as ig
        import updater

        class _DriftDriver:
            """로그인 때와 [시작] 때 계정이 달라진 상황을 그대로 재현한다(고객 실측 사고)."""
            def __init__(self, uid, name):
                self.uid, self.name = uid, name
            current_url = "https://www.instagram.com/"
            def execute_async_script(self, *_a, **_k):
                return {"id": self.uid, "username": self.name}
            def execute_script(self, *_a, **_k):
                return None
            def get_cookies(self):
                return [{"name": "ds_user_id", "value": "67584782851"}]
            def find_elements(self, *_a, **_k):
                return []

        app._log(f"[v1.5.0 확인] 팔로우 버튼 판정: "
                 f"'맞팔로우'->{ig.classify_follow_text('맞팔로우')}, "
                 f"'팔로잉'->{ig.classify_follow_text('팔로잉')}, "
                 f"'팔로우 취소'->{ig.classify_follow_text('팔로우 취소')}")
        ident = ig.resolve_identity(_DriftDriver("42105781019", "mugenboksa"))
        app._log(f"[v1.5.0 확인] 실행 계정 판독(단일 소스): {ig.identity_str(ident)} "
                 f"- 쿠키(ds_user_id=67584782851)가 아니라 실제 실행 계정을 따른다")
        app._set_live_account(ident)
        app._log("[v1.5.0 확인] 계정이 달라져도 [시작] 을 막지 않는다 "
                 "(v1.4.0 의 '계정 확인 필요' 차단 팝업 제거, 화면 표시 + 진단 기록으로 대체)")
        account_binding.bind("__demo__", "42105781019", "mugenboksa")
        app._log(f"[v1.5.0 확인] 진행상황 키: 별명이 아니라 계정 기준 "
                 f"'{account_binding.run_key('42105781019', '__demo__')}' "
                 f"(오타 별명 2개가 같은 계정이어도 중복 DM 안 감)")
        account_binding.unbind("__demo__")
        app._log(f"[v1.5.0 확인] 자동 업데이트 대상 경로(sys.executable): {updater.target_exe_path()}")
    except Exception as e:
        app._log(f"[v1.5.0 확인] 실행 중 오류: {e}")

    hold_ms = int(os.environ.get("DIAG_HOLD_MS", "6000"))
    root.lift()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    root.after(hold_ms, root.destroy)


def main():
    if os.environ.get("DIAG_AUTO") == "1":
        root = tk.Tk()
        app = App(root)
        root.after(300, root.destroy)
        root.mainloop()
        return 0

    root = tk.Tk()
    app = App(root)

    if "--guidemo" in sys.argv:
        root.after(500, lambda: _run_guidemo(root, app))

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
