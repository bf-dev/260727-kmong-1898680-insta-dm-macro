# -*- coding: utf-8 -*-
"""자동 업데이트 - 실행 중에도 안전하게 새 버전으로 재시작.

이 프로그램은 계정별 크롬 프로필(로그인 쿠키)을 디스크에 유지하므로, 재시작 후
세션 스냅샷을 따로 저장/복구할 필요가 없다(프로필이 그대로 살아있다). 그래서
표준 하우스 업데이터에서 쿠키 스냅샷 부분만 빼고, exe 스왑+재시작 로직만 쓴다.

===== v1.0.11 재시작-루프 방지(핵심 수정) =====
증상(고객 진단): exe 가 켜지자마자 몇 초 만에 꺼지고 다시 켜지는 무한 반복.
근본 원인: 옛 exe 의 업데이터가 '켜지자마자 즉시' 새 버전을 내려받아 .bat 으로
  exe 를 덮어쓰고 os._exit(0) 로 죽는다. 그런데 고객 PC 의 Windows Defender 가
  '서명 안 된 새 exe'를 바이러스로 오탐/격리하거나 파일 잠금이 걸려 copy /y 가
  '조용히' 실패한다(리다이렉트 >NUL). .bat 은 그래도 옛 exe 를 다시 켜고, 옛 exe 는
  또 즉시 업데이트를 시도 -> 다운로드 -> 종료 -> copy 실패 -> 재실행 ... 무한 루프.
  = 프로그램이 '자꾸 꺼지는' 정확한 이유.

이 버전이 그 루프를 끊는 3가지 방어:
  1) 즉시 검사 금지: 앱이 실제로 뜨고 한 사이클 정상 동작할 시간을 준 뒤에야 첫
     업데이트 검사를 한다(FIRST_CHECK_DELAY). 업데이트가 막혀도 앱은 계속 돈다.
  2) 스왑 성공 검증 + 실패 시 옛 버전 정상 재실행: .bat 이 copy 후 새 exe 크기를
     확인한다. 성공해야만 새 exe 를 켠다. 실패(Defender 격리/파일 잠금/권한)면 옛
     exe 를 그대로 다시 켜되, 우리가 남긴 '실패 마커'를 읽어 앱이 이번 실행에서는
     업데이트를 재시도하지 않는다(=루프 차단).
  3) 시도-1회 + 긴 백오프: 같은 목표 버전으로의 스왑 시도를 디스크에 기록한다.
     같은 목표 버전을 이미 시도했고 여전히 옛 버전이면(스왑이 막힘) 최소
     RETRY_BACKOFF_SECONDS 동안 다시 시도하지 않는다. 매 실행마다 32MB 를 받고
     죽는 tight-loop 이 사라진다.

파일명 규약(Cloudflare 캐시 교훈): 새 빌드는 항상 버전 접미사 파일명으로만 배포하고
version 파일의 exeUrl 이 그 새 경로를 가리키게 한다. 이미 서빙한 파일명을 덮어쓰면
엣지 캐시가 옛 바이트를 내보내 재시작 루프가 생긴다. _check_once 는 exeUrl 을 매번
동적으로 읽으므로 릴리스마다 코드 변경이 필요 없다.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time

import requests

import config

try:
    from bridge import remote_log
except Exception:  # bridge 가 없어도 업데이터는 죽지 않는다.
    def remote_log(*_a, **_k):
        pass

MIN_EXE_BYTES = 5_000_000  # 정상 onefile exe 는 20MB+; 손상 다운로드만 걸러낸다.

# 앱이 뜨자마자 업데이트를 시도하지 않는다. 먼저 정상 동작할 시간을 준다.
FIRST_CHECK_DELAY = 90              # 초. 앱 기동 후 첫 업데이트 검사까지 지연.
# 같은 목표 버전으로의 스왑이 실패(Defender/파일 잠금)했을 때 재시도 백오프.
RETRY_BACKOFF_SECONDS = 6 * 3600    # 6시간. 그 전에는 같은 버전 재시도 안 함.

# 스왑 시도/실패 상태를 남기는 마커 파일. 옛 PID 가 죽어도 새 PID 가 읽는다.
_STATE_PATH = os.path.join(config.APP_DIR, "update_state.json")


def _version_tuple(v):
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except Exception:
        return (0,)


def _load_state():
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    try:
        os.makedirs(config.APP_DIR, exist_ok=True)
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


def _should_attempt(latest):
    """이번 실행에서 latest 로의 스왑을 시도해도 되는지.

    같은 목표 버전을 최근에 시도했는데 여전히 옛 버전이 돌고 있다면(=스왑이 막힘)
    RETRY_BACKOFF_SECONDS 동안 재시도하지 않는다. 이게 tight restart-loop 차단.
    """
    state = _load_state()
    if state.get("target") == latest:
        last = float(state.get("last_attempt", 0) or 0)
        fails = int(state.get("fail_count", 0) or 0)
        # 한 번이라도 시도했고 아직 옛 버전이면(=현재 실행이 latest 가 아니면) 백오프.
        if fails >= 1 and (time.time() - last) < RETRY_BACKOFF_SECONDS:
            return False
    return True


def _record_attempt(latest):
    state = _load_state()
    if state.get("target") == latest:
        state["fail_count"] = int(state.get("fail_count", 0) or 0) + 1
    else:
        state = {"target": latest, "fail_count": 1}
    state["last_attempt"] = time.time()
    _save_state(state)


def _clear_state_if_current():
    """현재 실행 버전이 목표 버전 이상이면 스왑이 성공한 것 -> 마커 초기화."""
    state = _load_state()
    tgt = state.get("target")
    if tgt and _version_tuple(config.APP_VERSION) >= _version_tuple(tgt):
        _save_state({})


class UpdaterThread(threading.Thread):
    """백그라운드 업데이트 감시 스레드. 앱을 절대 막거나 죽이지 않는다."""

    def __init__(self, stop_running_loop=None, status_cb=None):
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self._stop_running_loop = stop_running_loop or (lambda: None)
        self._status = status_cb or (lambda *_: None)

    def stop(self):
        self._stop.set()

    def run(self):
        # 지난 실행에서 스왑이 성공했으면(=이제 최신 버전) 마커를 정리한다.
        try:
            _clear_state_if_current()
        except Exception:
            pass
        # 즉시 검사하지 않는다: 앱이 먼저 정상 동작하도록 지연.
        if self._stop.wait(FIRST_CHECK_DELAY):
            return
        while not self._stop.is_set():
            try:
                self._check_once()
            except Exception:
                pass  # 업데이트는 부가 기능. 절대 앱을 죽이면 안 된다.
            self._stop.wait(config.UPDATE_CHECK_SECONDS)

    def _check_once(self):
        try:
            resp = requests.get(config.VERSION_URL, timeout=8,
                                headers={"Cache-Control": "no-cache"})
            if resp.status_code != 200:
                return
            data = resp.json()
        except Exception:
            return
        latest = str(data.get("version", "")).strip()
        exe_url = data.get("exeUrl")
        if not latest or not exe_url:
            return
        if _version_tuple(latest) <= _version_tuple(config.APP_VERSION):
            return

        # 루프 차단: 같은 목표를 이미 시도했고 여전히 옛 버전이면 백오프.
        if not _should_attempt(latest):
            remote_log("update_skip_backoff",
                       f"{config.APP_VERSION}->{latest} 이전 스왑 실패, 백오프 중(정상 동작 유지)",
                       force=True)
            return

        # frozen(=배포된 exe)이 아니면 스왑 자체가 무의미하므로 다운로드도 하지 않는다.
        if not getattr(sys, "frozen", False):
            remote_log("update_skip_dev", "frozen 아님(개발 실행) - 스왑 생략", force=True)
            return

        # 이번 시도를 '기록'해 둔다(다운로드/스왑이 실패해도 백오프가 걸리도록).
        _record_attempt(latest)

        tmp_path = self._download_verified(exe_url)
        if not tmp_path:
            # 다운로드 실패(AV 차단/네트워크). 앱은 계속 돈다. 백오프는 이미 기록됨.
            return

        try:
            self._status(f"새 버전({latest})을 내려받았습니다. 곧 재시작합니다...")
            remote_log("update_downloaded",
                       f"{config.APP_VERSION} -> {latest} (exe={exe_url})", force=True)
            try:
                self._stop_running_loop()
            except Exception:
                pass
            self._schedule_restart(tmp_path, latest)
        except Exception:
            # 스왑 예약 자체가 실패해도 앱은 계속 살아 있어야 한다.
            remote_log("update_schedule_failed", "재시작 예약 실패 - 현재 버전 유지", force=True)
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _download_verified(self, exe_url):
        """새 exe 를 임시 경로에 완전히 내려받고 크기 검증. 성공 시 경로, 실패 시 None."""
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".exe")
            os.close(fd)
            total = 0
            with requests.get(exe_url, timeout=120, stream=True,
                              headers={"Cache-Control": "no-cache"}) as r:
                if r.status_code != 200:
                    os.unlink(tmp_path)
                    return None
                expected = r.headers.get("Content-Length")
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        if chunk:
                            f.write(chunk)
                            total += len(chunk)
            if expected and expected.isdigit() and total != int(expected):
                os.unlink(tmp_path)
                remote_log("update_download_incomplete",
                           f"받음={total} 기대={expected}", force=True)
                return None
            if total < MIN_EXE_BYTES:
                os.unlink(tmp_path)
                remote_log("update_too_small", f"받음={total} < {MIN_EXE_BYTES}", force=True)
                return None
            return tmp_path
        except Exception as e:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            remote_log("update_download_failed", str(e)[:300], force=True)
            return None

    def _schedule_restart(self, new_exe_path, latest):
        current_exe = sys.executable if getattr(sys, "frozen", False) else None
        if not current_exe:
            # 개발 모드(frozen 아님)에서는 스왑 재시작을 건너뛴다.
            remote_log("update_skip_dev", "frozen 아님(개발 실행) - 재시작 생략", force=True)
            return
        current_pid = os.getpid()
        new_size = 0
        try:
            new_size = os.path.getsize(new_exe_path)
        except Exception:
            pass

        # .bat 동작(재시작-루프 방지가 핵심):
        #  1) 옛 PID 가 완전히 죽을 때까지 대기.
        #  2) copy /y 로 새 exe 를 옛 경로에 덮어쓴다.
        #  3) 덮어쓴 파일 크기가 기대 크기와 같은지 확인한다(Defender 격리/부분복사 방지).
        #     - 같으면(성공): 새 exe 를 켜고 마커 파일(update_state.json)을 지워 다음 실행이
        #       깨끗하게 시작하도록 한다. 임시파일 삭제.
        #     - 다르면(실패=Defender 가 새 exe 를 격리/삭제했거나 copy 가 막힘): 옛 exe 를
        #       그대로 다시 켠다. 이때 마커는 그대로 남으므로(fail_count>=1) 새로 뜬 옛 exe 는
        #       _should_attempt 백오프에 걸려 즉시 재다운로드/재스왑하지 않는다 => 루프 차단.
        #  4) .bat 자기 삭제.
        # (한 줄에 여러 명령을 붙이지 않고 배치 라벨/블록으로 명확히 분기한다.)
        script = (
            "@echo off\r\n"
            ":wait\r\n"
            f'tasklist /FI "PID eq {current_pid}" 2>NUL | find "{current_pid}" >NUL\r\n'
            "if not errorlevel 1 (\r\n"
            "  timeout /t 1 /nobreak >NUL\r\n"
            "  goto wait\r\n"
            ")\r\n"
            "timeout /t 2 /nobreak >NUL\r\n"
            f'copy /y "{new_exe_path}" "{current_exe}" >NUL 2>NUL\r\n'
            # 덮어쓴 exe 의 크기를 확인해 스왑 성공 여부를 판정.
            f'set "NEWSIZE=0"\r\n'
            f'for %%A in ("{current_exe}") do set "NEWSIZE=%%~zA"\r\n'
            f'if "%NEWSIZE%"=="{new_size}" (\r\n'
            f'  del "{_STATE_PATH}" >NUL 2>NUL\r\n'
            f'  start "" "{current_exe}"\r\n'
            ") else (\r\n"
            # 스왑 실패: 옛 exe 를 그대로 다시 켠다(마커는 남겨 백오프가 걸리게).
            f'  if exist "{current_exe}" start "" "{current_exe}"\r\n'
            ")\r\n"
            f'del "{new_exe_path}" >NUL 2>NUL\r\n'
            'del "%~f0"\r\n'
        )
        fd, bat_path = tempfile.mkstemp(suffix=".bat")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        remote_log("update_restart", f"{config.APP_VERSION} -> {latest} 재시작 예약", force=True)
        subprocess.Popen(
            ["cmd.exe", "/c", bat_path],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        os._exit(0)


def start_updater(stop_running_loop=None, status_cb=None):
    """업데이터 스레드를 시작하고 핸들을 돌려준다. frozen 이 아니어도 폴링은 돈다
    (로그로 새 버전 존재를 알 수 있게)."""
    t = UpdaterThread(stop_running_loop=stop_running_loop, status_cb=status_cb)
    t.start()
    return t
