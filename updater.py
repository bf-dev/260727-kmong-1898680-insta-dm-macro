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

===== v1.4.0 스왑 실패의 진짜 원인(고객 실측 로그로 확정) =====
증상: 1.3.1 -> 1.3.2 다운로드는 매번 성공(`update_downloaded`), 재시작도 예약되는데
  (`update_restart`) 스왑은 한 번도 성공하지 못하고 `update_skip_backoff` 만 51번
  반복. 고객은 4시간 넘게 옛 버전에 묶여 있었고 화면에는 아무 표시도 없었다.
근본 원인: 스왑 스크립트를 **.bat 으로 UTF-8 인코딩해서** 썼다. cmd.exe 는 배치 파일을
  UTF-8 이 아니라 콘솔 OEM 코드페이지(한국어 윈도우 = cp949)로 읽는다. 이 고객의 exe
  경로에는 한글이 들어 있고(배포 zip 안의 파일명이 `인스타DM매크로.exe`), UTF-8 바이트가
  cp949 로 해석되면서 `copy /y "...한글경로..."` 의 대상 경로가 깨졌다. copy 는
  `>NUL 2>NUL` 로 삼켜져 조용히 실패하고, 크기 검증도 당연히 실패해 옛 exe 를 다시
  켠 뒤 마커(fail_count>=1) 때문에 6시간 백오프에 들어간다. 무한 반복.
이 버전의 수정:
  1) 스왑을 **PowerShell(.ps1, UTF-8 BOM)** 로 한다. PowerShell 5.1 은 BOM 을 보고
     유니코드로 읽으므로 한글 경로가 깨지지 않는다. powershell.exe 가 없는 경우에만
     .bat 으로 폴백하되, 그때는 OEM 코드페이지로 인코딩하고 가능하면 8.3 단축경로를
     써서 비ASCII 문자를 아예 없앤다.
  2) 대상 경로는 항상 `sys.executable` 에서 뽑는다(파일명 하드코딩 금지). 고객이 zip 을
     어디에 풀었든, exe 이름을 바꿨든 그대로 따라간다.
  3) **죽기 전에 미리 검증한다**: 새 exe 를 대상 폴더 옆에 먼저 복사해 본다. 권한/디스크/
     AV 문제면 여기서 실패하므로 앱을 죽이지 않고 그대로 살아서 원인을 로그로 올린다.
  4) 스왑 스크립트가 **결과 파일(update_result.json)** 을 남긴다. 다음 실행의 앱이 그걸
     읽어 `update_swap_ok` / `update_swap_failed`(실제 예외 문구 + 경로 + 크기)를 올린다.
     더 이상 "그냥 백오프 중"이라는 무의미한 줄만 반복되지 않는다.
  5) 스왑이 막힌 상태면 **고객 화면에 수동 다운로드 안내를 띄운다**(조용히 옛 버전에
     묶여 있는 상황을 없앤다).
"""

import json
import os
import shutil
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
# 스왑 스크립트가 남기는 결과 파일. 다음 실행이 읽어서 진단으로 올린다.
_RESULT_PATH = os.path.join(config.APP_DIR, "update_result.json")

POWERSHELL = os.path.join(
    os.getenv("SystemRoot", r"C:\Windows"),
    "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def target_exe_path():
    """스왑 대상 exe 경로. **반드시 sys.executable 에서 뽑는다.**

    고객이 실제로 실행 중인 파일이 무엇인지는 우리가 정한 이름이 아니라 프로세스가 안다.
    이 고객의 배포본은 zip 안의 `인스타DM매크로.exe` 라 한글 파일명이고, 앞으로 고객이
    이름을 바꾸거나 다른 폴더에 풀어도 여기서 그대로 따라가야 한다.
    """
    exe = getattr(sys, "executable", "") or ""
    if not exe:
        return None
    try:
        return os.path.realpath(exe)
    except Exception:
        return os.path.abspath(exe)


def _oem_encoding():
    """cmd.exe 가 .bat 을 읽을 때 쓰는 코드페이지(한국어 윈도우면 cp949)."""
    try:
        import ctypes
        cp = int(ctypes.windll.kernel32.GetOEMCP())
        if cp == 65001:
            return "utf-8"
        return f"cp{cp}"
    except Exception:
        return "utf-8"


def _short_path(path):
    """윈도우 8.3 단축 경로(전부 ASCII). 못 구하면 원본 그대로 반환."""
    try:
        import ctypes
        from ctypes import wintypes
        GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
        GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        GetShortPathNameW.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(1024)
        n = GetShortPathNameW(path, buf, 1024)
        if n and buf.value:
            return buf.value
    except Exception:
        pass
    return path


def _is_ascii(text):
    try:
        text.encode("ascii")
        return True
    except Exception:
        return False


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


def _record_error(latest, detail):
    """스왑/사전검증 실패의 '진짜 이유'를 마커에 남긴다.

    v1.3.x 는 실패해도 `update_skip_backoff` 라는 무의미한 줄만 반복해서, 고객이 4시간
    넘게 옛 버전에 묶여 있는데도 원인을 알 수 없었다. 이제 실패 사유가 마커에 남고
    백오프 로그와 다음 실행 진단에 그대로 실려 올라간다.
    """
    state = _load_state()
    if state.get("target") != latest:
        state = {"target": latest, "fail_count": int(state.get("fail_count", 0) or 0)}
    state["last_error"] = str(detail)[:500]
    state["last_error_at"] = time.time()
    _save_state(state)


def read_swap_result():
    """이전 실행에서 스왑 스크립트가 남긴 결과(없으면 None). 읽고 나면 파일은 지운다."""
    try:
        with open(_RESULT_PATH, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        return None
    try:
        os.unlink(_RESULT_PATH)
    except Exception:
        pass
    return data if isinstance(data, dict) else None


def _report_previous_swap():
    """스왑 스크립트가 남긴 결과를 진단으로 올린다(성공/실패 둘 다).

    실패했으면 사유를 마커에도 넣어서, 백오프 중 로그가 '왜' 막혔는지 같이 말하게 한다.
    """
    result = read_swap_result()
    if not result:
        return None
    ok = bool(result.get("ok"))
    detail = (f"target={result.get('target_version')} exe={result.get('target_path')} "
              f"expected={result.get('expected_size')} placed={result.get('placed_size')} "
              f"step={result.get('step')} err={str(result.get('error') or '')[:200]}")
    if ok:
        remote_log("update_swap_ok", detail, force=True)
    else:
        remote_log("update_swap_failed", detail, force=True)
        _record_error(result.get("target_version"), detail)
    return result


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
        self._blocked_notified = False   # 수동 다운로드 안내는 실행당 한 번만

    def stop(self):
        self._stop.set()

    def run(self):
        # 지난 실행에서 스왑 스크립트가 남긴 결과를 먼저 보고한다(성공/실패 모두).
        try:
            _report_previous_swap()
        except Exception:
            pass
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
            self._notify_update_blocked(latest, exe_url=exe_url)
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
            self._schedule_restart(tmp_path, latest, exe_url=exe_url)
        except Exception:
            # 스왑 예약 자체가 실패해도 앱은 계속 살아 있어야 한다.
            remote_log("update_schedule_failed", "재시작 예약 실패 - 현재 버전 유지", force=True)
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _notify_update_blocked(self, latest, exe_url=None):
        """자동 업데이트가 막혀 있다는 사실을 고객에게 '보이게' 알린다.

        v1.3.x 는 5분마다 같은 백오프 줄을 서버로만 51번 올렸다. 고객 화면에는 아무 표시도
        없었고, 그래서 4시간 넘게 옛 버전으로 계속 돌았다. 이제 화면 로그에 수동 다운로드
        주소까지 한 번 띄우고, 서버 로그도 실행당 한 번 + 실패 사유를 실어 보낸다.
        """
        if self._blocked_notified:
            return
        self._blocked_notified = True
        last_error = _load_state().get("last_error") or "사유 미기록"
        remote_log("update_skip_backoff",
                   f"{config.APP_VERSION}->{latest} 이전 스왑 실패로 백오프 중 "
                   f"(앱은 정상 동작). 마지막 실패: {last_error}",
                   force=True)
        try:
            self._status(
                f"[자동 업데이트 안내] 새 버전 {latest} 이(가) 나왔지만 이 PC 에서 자동 교체가 "
                f"막혀 있습니다. 아래 주소에서 새 파일을 직접 내려받아 실행해 주세요:\n"
                f"    {exe_url or config.MANUAL_DOWNLOAD_URL}")
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

    def _schedule_restart(self, new_exe_path, latest, exe_url=None):
        current_exe = target_exe_path() if getattr(sys, "frozen", False) else None
        if not current_exe:
            # 개발 모드(frozen 아님)에서는 스왑 재시작을 건너뛴다.
            remote_log("update_skip_dev", "frozen 아님(개발 실행) - 재시작 생략", force=True)
            return

        # (1) 죽기 전에 미리 시험한다: 대상 폴더에 새 exe 를 실제로 놓아 본다.
        # 권한 없음/디스크 부족/AV 삭제면 여기서 예외가 나고, 앱은 살아 있는 채로 진짜 사유를
        # 올릴 수 있다. v1.3.x 는 이 검증 없이 바로 죽어서 아무도 원인을 몰랐다.
        try:
            staged = stage_new_exe(new_exe_path, current_exe, latest)
        except Exception as e:
            detail = f"stage_failed target={current_exe} err={type(e).__name__}: {e}"
            remote_log("update_stage_failed", detail[:400], force=True)
            _record_error(latest, detail)
            self._notify_update_blocked(latest, exe_url=exe_url)
            try:
                os.unlink(new_exe_path)
            except Exception:
                pass
            return

        try:
            new_size = os.path.getsize(staged)
        except Exception:
            new_size = 0

        try:
            script_path, mode = write_swap_script(
                current_exe=current_exe, staged=staged, latest=latest,
                pid=os.getpid(), result_path=_RESULT_PATH, state_path=_STATE_PATH,
                expected_size=new_size)
            cmd = swap_command(script_path, mode)
        except Exception as e:
            detail = f"script_failed mode err={type(e).__name__}: {e}"
            remote_log("update_script_failed", detail[:400], force=True)
            _record_error(latest, detail)
            self._notify_update_blocked(latest, exe_url=exe_url)
            return

        remote_log("update_restart",
                   f"{config.APP_VERSION} -> {latest} 재시작 예약 "
                   f"(mode={mode} target={current_exe} size={new_size})", force=True)
        subprocess.Popen(
            cmd,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        os._exit(0)


def stage_new_exe(downloaded_path, current_exe, latest):
    """내려받은 exe 를 **대상 exe 와 같은 폴더**에 먼저 옮겨 놓는다.

    같은 볼륨이라 스왑이 move 한 번으로 끝나고(부분복사 없음), 무엇보다 '그 폴더에 쓸 수
    있는가'를 앱이 살아 있는 동안 확인할 수 있다. 실패하면 예외를 그대로 올려 호출한 쪽이
    진짜 사유를 진단으로 보낸다.
    """
    target_dir = os.path.dirname(current_exe) or "."
    base = os.path.basename(current_exe)
    staged = os.path.join(target_dir, f"{base}.update-{latest}.tmp")
    if os.path.exists(staged):
        os.unlink(staged)
    shutil.move(downloaded_path, staged)
    return staged


def _ps_quote(text):
    """PowerShell 홑따옴표 문자열로 안전하게 감싼다(따옴표는 두 번)."""
    return "'" + str(text).replace("'", "''") + "'"


def build_powershell_swap(current_exe, staged, latest, pid, result_path, state_path,
                          expected_size):
    """exe 스왑 PowerShell 스크립트 본문.

    핵심 3가지:
      - 실행 중이던 exe 는 **이름을 바꿔 옆으로 치울 수 있다**(윈도우는 실행 중 파일의
        rename 은 허용, 덮어쓰기는 거부). 그래서 rename -> move 순서면 잠금에 강하다.
        그래도 실패하면 예전 방식(덮어쓰기 복사)으로 한 번 더 시도한다.
      - 결과(성공 여부, 실제 크기, 예외 문구)를 **파일로 남긴다**. 다음 실행의 앱이 읽어
        진단으로 올린다.
      - 성공하든 실패하든 반드시 앱을 다시 켠다(고객이 프로그램을 잃지 않게).
    """
    q = _ps_quote
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"$oldPid = {int(pid)}",
        f"$target = {q(current_exe)}",
        f"$staged = {q(staged)}",
        f"$resultPath = {q(result_path)}",
        f"$statePath = {q(state_path)}",
        f"$expected = {int(expected_size)}",
        f"$backup = $target + '.old-{latest}'",
        "$result = @{ ok = $false; target_version = " + q(latest) + "; target_path = $target;"
        " expected_size = $expected; placed_size = 0; step = 'start'; error = '' }",
        "function Save-Result {",
        "  try {",
        "    $dir = Split-Path -Parent $resultPath",
        "    if (-not (Test-Path -LiteralPath $dir)) "
        "{ New-Item -ItemType Directory -Force -Path $dir | Out-Null }",
        "    ($result | ConvertTo-Json -Compress) | "
        "Set-Content -LiteralPath $resultPath -Encoding UTF8",
        "  } catch { }",
        "}",
        "$result.step = 'wait_pid'",
        "for ($i = 0; $i -lt 120; $i++) {",
        "  if (-not (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) { break }",
        "  Start-Sleep -Milliseconds 500",
        "}",
        "Start-Sleep -Seconds 1",
        "$result.step = 'swap_rename'",
        "try {",
        "  if (Test-Path -LiteralPath $backup) "
        "{ Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue }",
        "  Move-Item -LiteralPath $target -Destination $backup -Force",
        "  Move-Item -LiteralPath $staged -Destination $target -Force",
        "} catch {",
        "  $result.error = $_.Exception.Message",
        "  $result.step = 'swap_copy_fallback'",
        "  try {",
        "    if ((Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $target)) "
        "{ Move-Item -LiteralPath $backup -Destination $target -Force }",
        "    Copy-Item -LiteralPath $staged -Destination $target -Force",
        "    $result.error = ''",
        "  } catch { $result.error = $result.error + ' | ' + $_.Exception.Message }",
        "}",
        "$result.step = 'verify'",
        "try { $result.placed_size = (Get-Item -LiteralPath $target).Length } catch "
        "{ $result.error = $result.error + ' | ' + $_.Exception.Message }",
        "if ($result.placed_size -eq $expected) {",
        "  $result.ok = $true",
        "  Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue",
        "} elseif (-not $result.error) { $result.error = 'size mismatch after swap' }",
        "Save-Result",
        "Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue",
        "Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue",
        "if (Test-Path -LiteralPath $target) { Start-Process -FilePath $target }",
        "Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue",
        "",
    ])


def build_batch_swap(current_exe, staged, latest, pid, result_path, state_path,
                     expected_size):
    """powershell.exe 가 없는 PC 를 위한 .bat 폴백.

    **cmd.exe 는 배치 파일을 UTF-8 이 아니라 OEM 코드페이지로 읽는다.** 그래서 여기 들어가는
    경로는 가능하면 8.3 단축경로로 바꿔 비ASCII 를 없애고(`write_swap_script` 가 처리),
    파일도 OEM 코드페이지로 저장한다. v1.3.x 가 이걸 안 해서 한글 exe 경로가 깨졌다.
    """
    lines = [
        "@echo off",
        ":wait",
        f'tasklist /FI "PID eq {int(pid)}" 2>NUL | find "{int(pid)}" >NUL',
        "if not errorlevel 1 (",
        "  timeout /t 1 /nobreak >NUL",
        "  goto wait",
        ")",
        "timeout /t 2 /nobreak >NUL",
        f'move /y "{current_exe}" "{current_exe}.old-{latest}" >NUL 2>NUL',
        f'move /y "{staged}" "{current_exe}" >NUL 2>NUL',
        f'if not exist "{current_exe}" copy /y "{staged}" "{current_exe}" >NUL 2>NUL',
        'set "NEWSIZE=0"',
        f'for %%A in ("{current_exe}") do set "NEWSIZE=%%~zA"',
        f'if "%NEWSIZE%"=="{int(expected_size)}" (',
        f'  del "{state_path}" >NUL 2>NUL',
        f'  echo {{"ok":true,"target_version":"{latest}","step":"bat","placed_size":%NEWSIZE%,'
        f'"expected_size":{int(expected_size)},"error":""}}> "{result_path}"',
        ") else (",
        f'  echo {{"ok":false,"target_version":"{latest}","step":"bat","placed_size":%NEWSIZE%,'
        f'"expected_size":{int(expected_size)},"error":"bat swap size mismatch"}}> "{result_path}"',
        f'  if exist "{current_exe}.old-{latest}" move /y "{current_exe}.old-{latest}" '
        f'"{current_exe}" >NUL 2>NUL',
        ")",
        f'del "{current_exe}.old-{latest}" >NUL 2>NUL',
        f'del "{staged}" >NUL 2>NUL',
        f'if exist "{current_exe}" start "" "{current_exe}"',
        'del "%~f0"',
        "",
    ]
    return "\r\n".join(lines)


def write_swap_script(current_exe, staged, latest, pid, result_path, state_path,
                      expected_size):
    """스왑 스크립트를 디스크에 쓰고 (경로, 모드) 반환. 모드는 'ps1' 또는 'bat'.

    기본은 PowerShell(.ps1, **UTF-8 BOM**) - PowerShell 5.1 은 BOM 을 보고 유니코드로 읽어서
    한글 경로가 깨지지 않는다. powershell.exe 가 없을 때만 .bat 으로 폴백한다.
    """
    if os.path.exists(POWERSHELL):
        body = build_powershell_swap(current_exe, staged, latest, pid, result_path,
                                     state_path, expected_size)
        fd, path = tempfile.mkstemp(suffix=".ps1")
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(body)
        return path, "ps1"

    # .bat 폴백: 비ASCII 를 최대한 없애기 위해 8.3 단축경로를 먼저 시도한다.
    paths = {}
    for key, value in (("current_exe", current_exe), ("staged", staged),
                       ("result_path", result_path), ("state_path", state_path)):
        short = _short_path(value)
        paths[key] = short if _is_ascii(short) else value
    body = build_batch_swap(paths["current_exe"], paths["staged"], latest, pid,
                            paths["result_path"], paths["state_path"], expected_size)
    encoding = _oem_encoding()
    fd, path = tempfile.mkstemp(suffix=".bat")
    with os.fdopen(fd, "wb") as f:
        f.write(body.encode(encoding, errors="replace"))
    return path, "bat"


def swap_command(script_path, mode):
    if mode == "ps1":
        return [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-File", script_path]
    return ["cmd.exe", "/c", script_path]


def start_updater(stop_running_loop=None, status_cb=None):
    """업데이터 스레드를 시작하고 핸들을 돌려준다. frozen 이 아니어도 폴링은 돈다
    (로그로 새 버전 존재를 알 수 있게)."""
    t = UpdaterThread(stop_running_loop=stop_running_loop, status_cb=status_cb)
    t.start()
    return t
