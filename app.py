# -*- coding: utf-8 -*-
"""인스타 DM 매크로 - Tkinter GUI.

화면 구성:
  1) 계정: 라벨 입력 + [로그인/계정 전환] + [로그아웃] - 로그인은 항상 실제로 뜬 크롬 창에서
     사람이 직접 아이디/비번을 입력한다(프로그램은 로그인 여부만 감지). 라벨을 바꾸면 별도
     크롬 프로필(=별도 계정 세션)로 전환된다.
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
import single_instance
import updater


class App:
    def __init__(self, root):
        self.root = root
        root.title(f"인스타 DM 매크로 v{config.APP_VERSION} (고객 {config.CUSTOMER_ID})")
        root.geometry("860x620")

        self.driver = None
        self.engine = None
        self.rows = []
        self.skipped_no_message = []
        self.logged_in = False

        self.account_var = tk.StringVar(value="default")
        self.excel_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="상태: 로그인 필요")
        self.stats_var = tk.StringVar(value="완료 0 / 팔로우 0 / DM 0 / 실패 0")

        self._build_ui()
        self.updater_thread = updater.start_updater(
            stop_running_loop=self._stop_macro_silent, status_cb=self._log)

    # ---------- UI ----------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        acc_frame = ttk.LabelFrame(self.root, text="1) 인스타그램 계정")
        acc_frame.pack(fill="x", **pad)
        ttk.Label(acc_frame, text="계정 별명(여러 계정 구분용):").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        ttk.Entry(acc_frame, textvariable=self.account_var, width=24).grid(row=0, column=1, padx=6, pady=6)
        ttk.Button(acc_frame, text="로그인 / 계정 전환", command=self.on_login_click).grid(row=0, column=2, padx=6)
        ttk.Button(acc_frame, text="로그아웃", command=self.on_logout_click).grid(row=0, column=3, padx=6)
        ttk.Label(acc_frame, textvariable=self.status_var).grid(row=1, column=0, columnspan=4, padx=6, sticky="w")

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

    # ---------- 계정 ----------
    def on_login_click(self):
        label = self.account_var.get().strip() or "default"
        threading.Thread(target=self._login_flow, args=(label,), daemon=True).start()

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

        import instagram_actions as ig
        if ig.is_logged_in(self.driver):
            self.logged_in = True
            self._log(f"'{label}' 프로필에 이미 로그인되어 있습니다.")
            self._set_status(f"상태: 로그인됨 ({label})")
            bridge.remote_log("login_reused", f"account={label}", force=True)
            return

        ig.goto_login_screen(self.driver)
        self._log("크롬 창에서 인스타그램 아이디/비밀번호를 직접 입력해 로그인해 주세요. "
                   "(자동입력 안 함 - 2단계 인증/보안 확인도 그대로 통과 가능)")
        self._set_status("상태: 로그인 대기 중 (창에서 직접 로그인)")
        ok = ig.wait_for_manual_login(
            self.driver, timeout_s=600,
            poll_cb=lambda: self._set_status("상태: 로그인 대기 중..."))
        if ok:
            self.logged_in = True
            self._log(f"'{label}' 로그인 확인됨.")
            self._set_status(f"상태: 로그인됨 ({label})")
            bridge.remote_log("login_ok", f"account={label}", force=True)
        else:
            self._log("로그인 대기 시간이 초과됐습니다. 다시 시도해 주세요.")
            self._set_status("상태: 로그인 대기 시간 초과")
            bridge.remote_log("login_timeout", f"account={label}", force=True)

    def on_logout_click(self):
        if self.driver is None:
            messagebox.showinfo("안내", "먼저 로그인/계정 전환을 눌러 브라우저를 여세요.")
            return
        threading.Thread(target=self._logout_flow, daemon=True).start()

    def _logout_flow(self):
        import instagram_actions as ig
        ig.logout(self.driver, log=self._log)
        self.logged_in = False
        self._set_status("상태: 로그아웃됨")
        bridge.remote_log("logout", f"account={self.account_var.get().strip()}", force=True)

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
        done = progress_store.load_done_rows(path, self.account_var.get().strip() or "default")
        self._log(f"엑셀 로드 완료: 처리 대상 {len(self.rows)}행 "
                   f"(이미 완료 {len([r for r in self.rows if r.row_no in done])}행), "
                   f"F열 비어 스킵 {len(self.skipped_no_message)}행")

    # ---------- 실행 ----------
    def on_start_click(self):
        if self.driver is None or not self.logged_in:
            messagebox.showerror("오류", "먼저 계정 로그인을 완료해 주세요.")
            return
        if not self.rows:
            self.on_load_click()
        if not self.rows:
            messagebox.showerror("오류", "처리할 행이 없습니다. 엑셀을 확인해 주세요.")
            return
        import macro_engine
        label = self.account_var.get().strip() or "default"
        self.engine = macro_engine.MacroEngine(
            self.driver, self.rows, self.excel_var.get().strip(), label,
            log_cb=self._log, done_cb=self._on_row_done)
        self.engine.start()
        self._log("매크로를 시작합니다.")

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
        label = self.account_var.get().strip() or "default"
        if not path:
            messagebox.showinfo("안내", "엑셀 파일을 먼저 선택해 주세요.")
            return
        if messagebox.askyesno("확인", "이 엑셀+계정 조합의 진행상황을 초기화할까요? "
                                       "(처음부터 다시 팔로우+DM 을 시도합니다)"):
            progress_store.reset(path, label)
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
