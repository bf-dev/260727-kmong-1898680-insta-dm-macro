# -*- coding: utf-8 -*-
"""자동 업데이트 - 실행 중에도 안전하게 새 버전으로 재시작.

이 프로그램은 계정별 크롬 프로필(로그인 쿠키)을 디스크에 유지하므로, 재시작 후
세션 스냅샷을 따로 저장/복구할 필요가 없다(프로필이 그대로 살아있다). 그래서
표준 하우스 업데이터에서 쿠키 스냅샷 부분만 빼고, exe 스왑+재시작 로직만 쓴다.

===== v1.0.11 재시작-루프 방지 =====
옛 exe 가 켜지자마자 새 버전을 받아 덮어쓰고 죽는데 copy 가 조용히 실패해서 무한 재시작.
방어: (1) 기동 후 FIRST_CHECK_DELAY 만큼 기다렸다 첫 검사, (2) 스왑 성공을 크기로 검증,
(3) 같은 목표 버전 재시도에 백오프.

===== v1.4.0 스왑 스크립트 인코딩 수정 =====
스왑 스크립트를 UTF-8 .bat 으로 썼는데 cmd.exe 는 배치 파일을 OEM 코드페이지(한국어
윈도우 = cp949)로 읽는다. 고객의 exe 경로에 한글이 들어 있어(`인스타DM매크로.exe`)
대상 경로가 깨졌다. -> PowerShell(.ps1, UTF-8 BOM) + rename-then-move + 사전 staging
검증 + 결과 파일(update_result.json).

===== v1.7.0 "사유 미기록" 을 없앤다 (이 파일의 현재 핵심) =====
증상(고객 1898680 실측, 2026-08-04 05:57:40Z):

    [update_skip_backoff] 1.5.0->1.6.0 이전 스왑 실패로 백오프 중 (앱은 정상 동작).
                          마지막 실패: 사유 미기록

서버에 올라온 1.5.0->1.6.0 관련 진단 줄은 **이 한 줄이 전부**다. `update_downloaded`,
`update_restart`, `update_stage_failed`, `update_script_failed`, `update_swap_failed`,
`update_download_failed` 중 어느 것도 없다. 즉 디스크의 마커에는 "실패 1회" 가 적혔는데
그 실패를 낸 코드 경로는 로그를 한 줄도 남기지 못했다.

그런 상태를 만들 수 있는 v1.6.0 코드 경로는 하나뿐이다:

    _record_attempt(latest)      <- fail_count=1 을 **다운로드 전에** 먼저 적는다
    _download_verified(exe_url)  <- 38MB. 이 도중에 프로세스가 사라지면 끝.

고객이 다운로드 중에 프로그램을 닫거나, PC 가 절전으로 들어가거나, 네트워크가 끊긴 채
창을 닫으면 그 뒤의 remote_log 는 아예 실행되지 않는다(리포터는 데몬 스레드다). 남는 건
"이유 없는 실패 1회" 뿐이고, 다음 실행부터 6시간 백오프가 걸린다. 실패한 적이 없는데
실패로 기록되고, 왜 실패했는지도 없다. 이게 "사유 미기록" 의 정체다.

v1.7.0 이 바꾼 것:

  1) **단계(phase)를 먼저 적고, 실패는 사유가 있을 때만 적는다.** 시도를 시작할 때는
     fail_count 를 올리지 않고 `phase`(download/stage/script/swap_launched)만 적는다.
     다음 실행이 종료 상태가 아닌 phase 를 발견하면 = 그 단계에서 프로세스가 사라진 것.
     그것 자체가 사유다(`중단됨: 다운로드 단계에서 프로그램 종료`). 그리고 **중단은
     하드 실패가 아니므로 백오프를 걸지 않고 즉시 재시도한다.**
  2) **사유는 절대 비어 있을 수 없다.** 기록된 예외가 없으면 그 자리에서 환경을 실제로
     찔러 본다(_environment_probe): 대상 경로, 폴더 쓰기 가능 여부, exe 이름 변경 가능
     여부(스왑이 쓰는 바로 그 동작), 남아 있는 staged 파일, 여유 디스크, PowerShell 존재,
     관리자 여부, 같은 이름으로 떠 있는 프로세스 수. "사유 미기록" 은 도달 불가능하다.
  3) **백오프에 상한을 둔다.** 15분 -> 1시간 -> 3시간 -> 6시간(최대)로 오르고,
     MAX_HARD_FAILURES 번 하드 실패하면 자동 스왑을 포기하되 **매 실행마다** 고객 화면과
     서버에 알린다. 조용히 옛 버전에 묶이는 상태는 더 이상 없다.
  4) **[지금 업데이트]** - 고객이 직접 강제로 돌릴 수 있다(백오프 무시). 실패하면 사유를
     그대로 화면에 띄우고 수동 다운로드 주소를 준다.
  5) **스왑 스크립트가 무슨 일이 있어도 결과 파일을 남긴다**(try/finally). 그리고 파일
     잠금(AV 스캔/늦게 죽는 프로세스)에 대비해 rename+move 를 최대 SWAP_RETRIES 회
     재시도한다. 스크립트가 결과를 안 남기고 사라졌으면 그것도 하드 실패 사유로 적는다.

파일명 규약(Cloudflare 캐시 교훈): 새 빌드는 항상 버전 접미사 파일명으로만 배포하고
version 파일의 exeUrl 이 그 새 경로를 가리키게 한다. 이미 서빙한 파일명을 덮어쓰면
엣지 캐시가 옛 바이트를 내보내 재시작 루프가 생긴다.
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

# 하드 실패(=진짜 사유가 기록된 실패) 횟수별 백오프. 무한이 아니라 상한이 있다.
BACKOFF_STEPS = (15 * 60, 60 * 60, 3 * 3600, 6 * 3600)
# 이 횟수를 넘게 하드 실패하면 자동 스왑을 포기한다(대신 매 실행 고객에게 알린다).
MAX_HARD_FAILURES = 6
# 한 세션을 하루 종일 켜 두는 고객도 있다. 막힌 상태를 이 간격으로 다시 알린다.
BLOCKED_RENOTIFY_SECONDS = 6 * 3600
# 옛 버전 호환(스크립트/테스트가 참조): 하드 실패의 최대 백오프.
RETRY_BACKOFF_SECONDS = BACKOFF_STEPS[-1]

# 스왑 스크립트가 파일 잠금에 부딪혔을 때 재시도하는 횟수/간격(초).
SWAP_RETRIES = 30
SWAP_RETRY_SLEEP = 1

# 스왑 시도/실패 상태를 남기는 마커 파일. 옛 PID 가 죽어도 새 PID 가 읽는다.
_STATE_PATH = os.path.join(config.APP_DIR, "update_state.json")
# 스왑 스크립트가 남기는 결과 파일. 다음 실행이 읽어서 진단으로 올린다.
_RESULT_PATH = os.path.join(config.APP_DIR, "update_result.json")

# 단계 진행을 append 로 적는 로컬 흔적. 프로세스가 죽어도 남고, 다음 실행이 올린다.
_TRAIL_PATH = os.path.join(config.APP_DIR, "update_trail.log")
_TRAIL_MAX_BYTES = 200_000

# 종료(terminal) 가 아닌 단계들. 다음 실행에서 이 상태로 발견되면 '중단' 이다.
_LIVE_PHASES = ("download", "stage", "script", "swap_launched")

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


# ---------------------------------------------------------------- 상태 마커


def _load_state():
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state):
    try:
        os.makedirs(config.APP_DIR, exist_ok=True)
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass


def _trail(line):
    """단계 진행을 로컬 파일에 남긴다. 프로세스가 갑자기 죽어도 이건 디스크에 있다."""
    try:
        os.makedirs(config.APP_DIR, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(_TRAIL_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] v{config.APP_VERSION} pid={os.getpid()} {line}\n")
        if os.path.getsize(_TRAIL_PATH) > _TRAIL_MAX_BYTES:
            with open(_TRAIL_PATH, "r", encoding="utf-8", errors="replace") as f:
                kept = f.readlines()[-400:]
            with open(_TRAIL_PATH, "w", encoding="utf-8") as f:
                f.writelines(kept)
    except Exception:
        pass


def read_trail(max_chars=3000):
    """로컬 단계 기록의 뒷부분. 진단 업로드에 실어 보낸다."""
    try:
        with open(_TRAIL_PATH, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        return text[-max_chars:]
    except Exception:
        return ""


def _set_phase(latest, phase, exe_url=None, extra=None):
    """이번 시도가 지금 어느 단계인지 **디스크에** 적는다.

    fail_count 는 건드리지 않는다. 실패는 사유가 있을 때만 센다(_record_failure).
    프로세스가 이 단계에서 사라지면 다음 실행이 phase 를 보고 '중단' 으로 판정한다.
    """
    state = _load_state()
    if state.get("target") != latest:
        state = {"target": latest, "fail_count": 0, "interrupted": 0, "attempts": 0}
    state["phase"] = phase
    state["phase_at"] = time.time()
    state["last_attempt"] = time.time()
    if exe_url:
        state["exe_url"] = exe_url
    if phase == "download":
        state["attempts"] = int(state.get("attempts", 0) or 0) + 1
    if extra:
        state.update(extra)
    _save_state(state)
    _trail(f"phase={phase} target={latest} {extra or ''}")
    return state


def _clear_phase(latest=None):
    state = _load_state()
    if latest and state.get("target") != latest:
        return
    state["phase"] = "idle"
    state["phase_at"] = time.time()
    _save_state(state)


def _record_failure(latest, detail, hard=True):
    """스왑/사전검증 실패의 '진짜 이유'를 마커에 남긴다.

    hard=True 만 백오프를 만든다. 중단(프로세스가 사라짐)은 사유는 남기되 하드 실패가
    아니므로 다음 실행에서 곧바로 재시도한다.
    """
    state = _load_state()
    if state.get("target") != latest:
        state = {"target": latest, "fail_count": 0, "interrupted": 0, "attempts": 0}
    detail = str(detail or "").strip() or "사유 문자열이 비어 있었음(코드 결함)"
    state["last_error"] = detail[:1200]
    state["last_error_at"] = time.time()
    state["last_error_hard"] = bool(hard)
    if hard:
        state["fail_count"] = int(state.get("fail_count", 0) or 0) + 1
    else:
        state["interrupted"] = int(state.get("interrupted", 0) or 0) + 1
    state["phase"] = "idle"
    state["phase_at"] = time.time()
    _save_state(state)
    _trail(f"{'FAIL' if hard else 'INTERRUPTED'} target={latest} {detail[:400]}")
    return state


# 하위 호환(옛 이름). 하드 실패로 기록한다.
def _record_error(latest, detail):
    return _record_failure(latest, detail, hard=True)


def _backoff_for(fail_count):
    """하드 실패 횟수별 백오프(초). 상한이 있고 무한이 아니다."""
    if fail_count <= 0:
        return 0
    return BACKOFF_STEPS[min(fail_count, len(BACKOFF_STEPS)) - 1]


def _should_attempt(latest, force=False):
    """이번 검사에서 latest 로의 스왑을 시도해도 되는지.

    - 하드 실패 0회(=중단만 있었음)면 곧바로 재시도한다. v1.6.0 은 여기서 막혀
      "실패한 적 없는 실패" 때문에 6시간 조용히 옛 버전에 묶여 있었다.
    - 하드 실패가 쌓이면 15분 -> 1시간 -> 3시간 -> 6시간(최대)로 늘어난다.
    - MAX_HARD_FAILURES 를 넘으면 자동 스왑을 포기한다([지금 업데이트]는 여전히 가능).
    """
    if force:
        return True
    state = _load_state()
    if state.get("target") != latest:
        return True
    fails = int(state.get("fail_count", 0) or 0)
    if fails <= 0:
        return True
    if fails >= MAX_HARD_FAILURES:
        return False
    last = float(state.get("last_attempt", 0) or 0)
    return (time.time() - last) >= _backoff_for(fails)


def gave_up(latest):
    state = _load_state()
    return (state.get("target") == latest
            and int(state.get("fail_count", 0) or 0) >= MAX_HARD_FAILURES)


# ------------------------------------------------------- 사유는 비어 있을 수 없다


def _probe_target_writable(target_dir):
    probe = os.path.join(target_dir, f".upd-probe-{os.getpid()}")
    try:
        with open(probe, "wb") as f:
            f.write(b"1")
        os.unlink(probe)
        return "yes"
    except Exception as e:
        return f"no({type(e).__name__}:{str(e)[:80]})"


def _probe_rename(target):
    """스왑이 실제로 쓰는 동작(실행 중 exe 의 이름 변경)이 되는지 그 자리에서 확인한다.

    윈도우는 실행 중 파일의 덮어쓰기는 거부하지만 rename 은 허용한다. 이게 막혀 있다면
    (AV 실시간 감시, 폴더 권한, 다른 프로세스의 핸들) 그것이 곧 스왑 실패의 사유다.
    """
    if not getattr(sys, "frozen", False):
        # 개발 실행에서는 sys.executable 이 파이썬 인터프리터다. 남의 파일을 건드리지 않는다.
        return "skip(개발 실행)"
    if not target or not os.path.isfile(target):
        return "skip(대상 파일 없음)"
    probe = target + ".renameprobe"
    try:
        os.rename(target, probe)
    except Exception as e:
        return f"no({type(e).__name__}:{str(e)[:80]})"
    try:
        os.rename(probe, target)
        return "yes"
    except Exception as e:
        return f"yes-but-restore-failed({type(e).__name__}:{str(e)[:60]})"


def _probe_same_name_processes(target):
    """같은 exe 이름으로 몇 개나 떠 있는지(2개 이상이면 스왑을 방해할 수 있다)."""
    if os.name != "nt" or not target:
        return "n/a"
    try:
        base = os.path.basename(target)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {base}", "/NH"],
            capture_output=True, timeout=10, creationflags=flags)
        text = out.stdout.decode(_oem_encoding(), errors="replace")
        return str(sum(1 for line in text.splitlines() if base.lower() in line.lower()))
    except Exception as e:
        return f"err({type(e).__name__})"


def _environment_probe():
    """지금 이 PC 에서 스왑이 왜 막히는지 **실측**한다. 절대 예외를 밖으로 내지 않는다.

    기록된 예외가 없을 때도 여기서 나온 값이 사유가 된다. 그래서 "사유 미기록" 은
    구조적으로 나올 수 없다.
    """
    bits = []
    try:
        target = target_exe_path()
        bits.append(f"exe={target}")
        bits.append(f"frozen={bool(getattr(sys, 'frozen', False))}")
        target_dir = os.path.dirname(target) if target else ""
        if target_dir and getattr(sys, "frozen", False):
            bits.append(f"dir_writable={_probe_target_writable(target_dir)}")
            try:
                usage = shutil.disk_usage(target_dir)
                bits.append(f"free_mb={usage.free // (1024 * 1024)}")
            except Exception as e:
                bits.append(f"free_mb=err({type(e).__name__})")
            try:
                leftovers = [n for n in os.listdir(target_dir) if ".update-" in n]
                bits.append(f"staged_leftover={leftovers[:4] or 'none'}")
            except Exception as e:
                bits.append(f"staged_leftover=err({type(e).__name__})")
        bits.append(f"rename_ok={_probe_rename(target)}")
        bits.append(f"same_name_procs={_probe_same_name_processes(target)}")
        bits.append(f"powershell={'yes' if os.path.exists(POWERSHELL) else 'no'}")
        try:
            import ctypes
            bits.append(f"admin={bool(ctypes.windll.shell32.IsUserAnAdmin())}")
        except Exception:
            bits.append("admin=n/a")
        bits.append(f"result_file={'있음' if os.path.exists(_RESULT_PATH) else '없음'}")
    except Exception as e:  # 진단이 앱을 죽이면 안 된다.
        bits.append(f"probe_error={type(e).__name__}:{str(e)[:80]}")
    return " ".join(bits) or "probe=결과 없음"


def failure_reason(latest):
    """백오프/차단 사유. **어떤 경우에도 빈 문자열을 돌려주지 않는다.**

    v1.6.0 은 마커에 last_error 가 없으면 그대로 "사유 미기록" 을 찍었다. 그게 이 사고의
    핵심이었다. 이제 기록이 없으면 그 자리에서 환경을 찔러 사유를 만들어 낸다.
    """
    state = _load_state()
    recorded = str(state.get("last_error") or "").strip()
    counters = (f"시도={state.get('attempts', 0)} 하드실패={state.get('fail_count', 0)} "
                f"중단={state.get('interrupted', 0)} 단계={state.get('phase') or '없음'}")
    if recorded:
        head = recorded
    else:
        head = ("기록된 예외 없음(마커에 사유가 없다) - 지금 실측한 환경으로 대신 보고: "
                + counters)
    probe = _environment_probe()
    reason = f"{head} | {counters} | 실측: {probe}"
    return reason.strip() or f"사유 산출 실패(코드 결함) | {counters}"


# ------------------------------------------------------------ 결과/중단 회수


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
              f"step={result.get('step')} tries={result.get('tries')} "
              f"err={str(result.get('error') or '')[:300]} "
              f"log={str(result.get('log') or '')[:300]}")
    if ok:
        remote_log("update_swap_ok", detail, force=True)
        _trail(f"SWAP OK {detail[:300]}")
    else:
        remote_log("update_swap_failed", detail, force=True)
        _record_failure(result.get("target_version"), f"스왑 스크립트 실패: {detail}", hard=True)
    return result


def _reconcile_interrupted_attempt():
    """지난 실행이 어느 단계에서 사라졌는지 확인하고 **사유로 기록한다.**

    v1.6.0 의 정확한 구멍: 다운로드(38MB) 도중에 프로그램이 닫히면 fail_count 만 남고
    사유는 안 남았다. 그걸 다음 실행이 '이유 없는 실패' 로 읽어 6시간 백오프에 들어갔다.
    이제는:
      - download/stage/script 에서 사라짐 = 중단(하드 실패 아님) -> 백오프 없이 재시도.
      - swap_launched 인데 결과 파일이 없음 = 스왑 스크립트가 결과도 못 남기고 죽음
        -> 진짜 하드 실패로 기록하고 실측 사유를 붙인다.
    """
    state = _load_state()
    phase = state.get("phase")
    target = state.get("target")
    if not target or phase not in _LIVE_PHASES:
        return None
    age = int(max(0, time.time() - float(state.get("phase_at", 0) or 0)))
    if phase == "swap_launched":
        detail = (f"스왑 스크립트가 결과 파일을 남기지 않고 끝났습니다"
                  f"(단계={phase}, {age}초 전). 실측: {_environment_probe()}")
        _record_failure(target, detail, hard=True)
        remote_log("update_swap_no_result", f"{config.APP_VERSION}->{target} {detail}"[:1500],
                   force=True)
        return {"phase": phase, "hard": True, "detail": detail}
    detail = (f"중단됨: '{phase}' 단계에서 프로그램이 종료됐습니다(프로그램 닫힘/절전/"
              f"네트워크 끊김, {age}초 전). 실패가 아니므로 백오프 없이 다시 시도합니다.")
    _record_failure(target, detail, hard=False)
    remote_log("update_attempt_interrupted", f"{config.APP_VERSION}->{target} {detail}"[:1500],
               force=True)
    return {"phase": phase, "hard": False, "detail": detail}


def _clear_state_if_current():
    """현재 실행 버전이 목표 버전 이상이면 스왑이 성공한 것 -> 마커 초기화."""
    state = _load_state()
    tgt = state.get("target")
    if tgt and _version_tuple(config.APP_VERSION) >= _version_tuple(tgt):
        _save_state({})
        _trail(f"state cleared (now running {config.APP_VERSION} >= {tgt})")


# ------------------------------------------------------------------- 스레드


class UpdaterThread(threading.Thread):
    """백그라운드 업데이트 감시 스레드. 앱을 절대 막거나 죽이지 않는다."""

    def __init__(self, stop_running_loop=None, status_cb=None, blocked_cb=None,
                 pre_swap_cb=None):
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self._stop_running_loop = stop_running_loop or (lambda: None)
        self._status = status_cb or (lambda *_: None)
        # 화면 배너용 콜백: blocked_cb(latest, reason, download_url)
        self._blocked_cb = blocked_cb or (lambda *_a, **_k: None)
        # 스왑 직전에 크롬/드라이버를 정리할 기회(파일 잠금/경합 방지).
        self._pre_swap_cb = pre_swap_cb or (lambda: None)
        self._blocked_notified_at = 0.0
        self._busy = threading.Lock()

    def stop(self):
        self._stop.set()

    def run(self):
        # 1) 지난 실행에서 스왑 스크립트가 남긴 결과를 먼저 보고한다(성공/실패 모두).
        try:
            _report_previous_swap()
        except Exception:
            pass
        # 2) 결과 파일이 없는데 단계만 남아 있으면 = 중단. 사유로 남긴다.
        try:
            _reconcile_interrupted_attempt()
        except Exception:
            pass
        # 3) 지난 실행에서 스왑이 성공했으면(=이제 최신 버전) 마커를 정리한다.
        try:
            _clear_state_if_current()
        except Exception:
            pass
        # 4) 남아 있는 로컬 단계 기록을 올린다(고객에게 스크린샷을 요구하지 않기 위해).
        try:
            trail = read_trail()
            if trail:
                remote_log("update_trail", trail[-1500:], force=True)
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

    # -------------------------------------------------- 고객이 직접 누르는 경로

    def check_now(self, done_cb=None):
        """[지금 업데이트] - 백오프를 무시하고 즉시 검사/교체를 시도한다.

        결과를 done_cb(dict) 로 돌려준다. 절대 GUI 스레드를 막지 않는다.
        """
        def _work():
            try:
                result = self._check_once(force=True, manual=True)
            except Exception as e:
                result = {"status": "error",
                          "detail": f"{type(e).__name__}: {e}",
                          "download_url": self.download_url()}
            try:
                if done_cb:
                    done_cb(result)
            except Exception:
                pass
        threading.Thread(target=_work, daemon=True).start()

    def download_url(self, exe_url=None):
        state = _load_state()
        return exe_url or state.get("exe_url") or config.MANUAL_DOWNLOAD_URL

    # ------------------------------------------------------------- 본 검사

    def _fetch_version(self):
        resp = requests.get(config.VERSION_URL, timeout=8,
                            headers={"Cache-Control": "no-cache"})
        if resp.status_code != 200:
            raise RuntimeError(f"version.json HTTP {resp.status_code}")
        return resp.json()

    def _check_once(self, force=False, manual=False):
        if not self._busy.acquire(blocking=False):
            return {"status": "busy", "detail": "이미 업데이트를 확인하고 있습니다."}
        try:
            return self._check_once_locked(force=force, manual=manual)
        finally:
            self._busy.release()

    def _check_once_locked(self, force=False, manual=False):
        try:
            data = self._fetch_version()
        except Exception as e:
            detail = f"버전 정보를 읽지 못했습니다: {type(e).__name__}: {str(e)[:200]}"
            if manual:
                remote_log("update_manual_check_failed", detail, force=True)
            return {"status": "error", "detail": detail,
                    "download_url": self.download_url()}

        latest = str(data.get("version", "")).strip()
        exe_url = data.get("exeUrl")
        zip_url = data.get("zipUrl")
        if not latest or not exe_url:
            return {"status": "error", "detail": "버전 정보가 비어 있습니다.",
                    "download_url": self.download_url()}
        if _version_tuple(latest) <= _version_tuple(config.APP_VERSION):
            if manual:
                remote_log("update_manual_up_to_date",
                           f"이미 최신 {config.APP_VERSION} (서버 {latest})", force=True)
            return {"status": "up_to_date", "version": config.APP_VERSION,
                    "latest": latest, "detail": f"이미 최신 버전입니다 (v{config.APP_VERSION})."}

        manual_url = zip_url or exe_url

        # 루프 차단: 하드 실패가 쌓였으면 백오프. 단, [지금 업데이트]는 무시하고 진행한다.
        if not _should_attempt(latest, force=force):
            reason = failure_reason(latest)
            self._notify_update_blocked(latest, exe_url=manual_url, reason=reason)
            return {"status": "blocked", "latest": latest, "detail": reason,
                    "download_url": manual_url}

        # frozen(=배포된 exe)이 아니면 스왑 자체가 무의미하므로 다운로드도 하지 않는다.
        if not getattr(sys, "frozen", False):
            remote_log("update_skip_dev", "frozen 아님(개발 실행) - 스왑 생략", force=True)
            return {"status": "dev", "latest": latest,
                    "detail": "개발 실행(exe 아님)이라 자동 교체를 건너뜁니다.",
                    "download_url": manual_url}

        # **여기부터가 v1.6.0 과 다른 지점**: 실패로 세지 않고 '단계' 만 적는다.
        _set_phase(latest, "download", exe_url=exe_url, extra={"zip_url": zip_url})
        self._status(f"새 버전 {latest} 을(를) 내려받는 중입니다...")

        tmp_path, dl_error = self._download_verified(exe_url)
        if not tmp_path:
            detail = f"다운로드 실패: {dl_error}"
            _record_failure(latest, detail, hard=True)
            self._notify_update_blocked(latest, exe_url=manual_url, reason=detail)
            return {"status": "failed", "latest": latest, "detail": detail,
                    "download_url": manual_url}

        try:
            remote_log("update_downloaded",
                       f"{config.APP_VERSION} -> {latest} (exe={exe_url})", force=True)
            self._status(f"새 버전({latest})을 내려받았습니다. 곧 재시작합니다...")
            try:
                self._stop_running_loop()
            except Exception:
                pass
            try:
                self._pre_swap_cb()
            except Exception:
                pass
            return self._schedule_restart(tmp_path, latest, exe_url=manual_url)
        except Exception as e:
            detail = f"재시작 예약 중 예외: {type(e).__name__}: {e}"
            remote_log("update_schedule_failed", detail[:400], force=True)
            _record_failure(latest, detail, hard=True)
            self._notify_update_blocked(latest, exe_url=manual_url, reason=detail)
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return {"status": "failed", "latest": latest, "detail": detail,
                    "download_url": manual_url}

    # ------------------------------------------------------------ 고객 안내

    def _notify_update_blocked(self, latest, exe_url=None, reason=None):
        """자동 업데이트가 막혀 있다는 사실을 고객에게 '보이게' 알린다.

        v1.3.x 는 5분마다 같은 백오프 줄을 서버로만 51번 올렸고 화면에는 아무 표시도 없었다.
        v1.6.0 은 '사유 미기록' 만 한 줄 올렸다. 이제는 사유를 반드시 실어 보내고, 화면에는
        배너 + 수동 다운로드 주소를 띄우며, 세션이 길면 주기적으로 다시 알린다.
        """
        now = time.time()
        if self._blocked_notified_at and (now - self._blocked_notified_at) < BLOCKED_RENOTIFY_SECONDS:
            return
        self._blocked_notified_at = now
        reason = (reason or "").strip() or failure_reason(latest)
        url = exe_url or self.download_url()
        remote_log("update_skip_backoff",
                   f"{config.APP_VERSION}->{latest} 자동 교체가 막혀 있습니다"
                   f"(앱은 정상 동작). 사유: {reason}"[:5000],
                   force=True)
        try:
            self._status(
                f"[자동 업데이트 안내] 새 버전 {latest} 이(가) 나왔지만 이 PC 에서 자동 교체가 "
                f"막혀 있습니다. [지금 업데이트] 를 누르거나 아래 주소에서 직접 내려받아 "
                f"주세요:\n    {url}")
        except Exception:
            pass
        try:
            self._blocked_cb(latest, reason, url)
        except Exception:
            pass

    # ------------------------------------------------------------- 다운로드

    def _download_verified(self, exe_url):
        """새 exe 를 임시 경로에 완전히 내려받고 크기 검증.

        (경로, None) 또는 (None, 실패사유). **사유 없이 실패하지 않는다.**
        """
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".exe")
            os.close(fd)
            total = 0
            with requests.get(exe_url, timeout=120, stream=True,
                              headers={"Cache-Control": "no-cache"}) as r:
                if r.status_code != 200:
                    os.unlink(tmp_path)
                    msg = f"HTTP {r.status_code} ({exe_url})"
                    remote_log("update_download_failed", msg, force=True)
                    return None, msg
                expected = r.headers.get("Content-Length")
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        if chunk:
                            f.write(chunk)
                            total += len(chunk)
            if expected and expected.isdigit() and total != int(expected):
                os.unlink(tmp_path)
                msg = f"받은 크기가 다릅니다: 받음={total} 기대={expected}"
                remote_log("update_download_incomplete", msg, force=True)
                return None, msg
            if total < MIN_EXE_BYTES:
                os.unlink(tmp_path)
                msg = f"받은 파일이 너무 작습니다: {total} < {MIN_EXE_BYTES} (AV 차단 의심)"
                remote_log("update_too_small", msg, force=True)
                return None, msg
            return tmp_path, None
        except Exception as e:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            msg = f"{type(e).__name__}: {str(e)[:300]}"
            remote_log("update_download_failed", msg, force=True)
            return None, msg

    # ------------------------------------------------------------ 스왑 예약

    def _schedule_restart(self, new_exe_path, latest, exe_url=None):
        current_exe = target_exe_path() if getattr(sys, "frozen", False) else None
        if not current_exe:
            remote_log("update_skip_dev", "frozen 아님(개발 실행) - 재시작 생략", force=True)
            return {"status": "dev", "latest": latest,
                    "detail": "개발 실행이라 재시작을 건너뜁니다.", "download_url": exe_url}

        # (1) 죽기 전에 미리 시험한다: 대상 폴더에 새 exe 를 실제로 놓아 본다.
        # 권한 없음/디스크 부족/AV 삭제면 여기서 예외가 나고, 앱은 살아 있는 채로 진짜 사유를
        # 올릴 수 있다.
        _set_phase(latest, "stage")
        try:
            staged = stage_new_exe(new_exe_path, current_exe, latest)
        except Exception as e:
            detail = (f"새 파일을 프로그램 폴더에 놓지 못했습니다 target={current_exe} "
                      f"err={type(e).__name__}: {e} | 실측: {_environment_probe()}")
            remote_log("update_stage_failed", detail[:1500], force=True)
            _record_failure(latest, detail, hard=True)
            self._notify_update_blocked(latest, exe_url=exe_url, reason=detail)
            try:
                os.unlink(new_exe_path)
            except Exception:
                pass
            return {"status": "failed", "latest": latest, "detail": detail,
                    "download_url": exe_url}

        try:
            new_size = os.path.getsize(staged)
        except Exception:
            new_size = 0

        _set_phase(latest, "script")
        try:
            script_path, mode = write_swap_script(
                current_exe=current_exe, staged=staged, latest=latest,
                pid=os.getpid(), result_path=_RESULT_PATH, state_path=_STATE_PATH,
                expected_size=new_size)
            cmd = swap_command(script_path, mode)
        except Exception as e:
            detail = (f"교체 스크립트를 만들지 못했습니다 err={type(e).__name__}: {e} "
                      f"| 실측: {_environment_probe()}")
            remote_log("update_script_failed", detail[:1500], force=True)
            _record_failure(latest, detail, hard=True)
            self._notify_update_blocked(latest, exe_url=exe_url, reason=detail)
            return {"status": "failed", "latest": latest, "detail": detail,
                    "download_url": exe_url}

        try:
            proc_flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                          | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            subprocess.Popen(cmd, creationflags=proc_flags)
        except Exception as e:
            detail = (f"교체 스크립트를 실행하지 못했습니다 mode={mode} "
                      f"err={type(e).__name__}: {e} | 실측: {_environment_probe()}")
            remote_log("update_spawn_failed", detail[:1500], force=True)
            _record_failure(latest, detail, hard=True)
            self._notify_update_blocked(latest, exe_url=exe_url, reason=detail)
            return {"status": "failed", "latest": latest, "detail": detail,
                    "download_url": exe_url}

        # 여기까지 왔으면 스크립트가 돌고 있다. 결과 파일을 남기지 못하고 죽으면 다음 실행이
        # 'swap_launched' 를 보고 하드 실패 + 실측 사유로 기록한다.
        _set_phase(latest, "swap_launched",
                   extra={"swap_mode": mode, "expected_size": new_size,
                          "target_path": current_exe})
        remote_log("update_restart",
                   f"{config.APP_VERSION} -> {latest} 재시작 예약 "
                   f"(mode={mode} target={current_exe} size={new_size})", force=True)
        # 리포터는 데몬 스레드다. 바로 os._exit 하면 이 줄이 서버에 닿지 못한다
        # (1.5.0->1.6.0 때 진단이 통째로 사라진 이유 중 하나).
        time.sleep(2.5)
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

    핵심 4가지:
      - 실행 중이던 exe 는 **이름을 바꿔 옆으로 치울 수 있다**(윈도우는 실행 중 파일의
        rename 은 허용, 덮어쓰기는 거부). 그래서 rename -> move 순서면 잠금에 강하다.
      - 그래도 실패하면 **최대 SWAP_RETRIES 회 다시 시도한다**. AV 실시간 검사나 늦게
        죽는 자식 프로세스가 몇 초간 핸들을 붙잡는 경우가 실제로 있다(v1.7.0 추가).
      - 결과(성공 여부, 실제 크기, 예외 문구, 단계 로그)를 **무슨 일이 있어도 파일로
        남긴다**(try/finally). 다음 실행의 앱이 읽어 진단으로 올린다.
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
        f"$maxTries = {int(SWAP_RETRIES)}",
        f"$retrySleep = {int(SWAP_RETRY_SLEEP)}",
        f"$backup = $target + '.old-{latest}'",
        "$result = @{ ok = $false; target_version = " + q(latest) + "; target_path = $target;"
        " expected_size = $expected; placed_size = 0; step = 'start'; tries = 0;"
        " error = ''; log = '' }",
        "$steps = New-Object System.Collections.ArrayList",
        "function Note($m) { [void]$steps.Add($m); $result.log = ($steps -join ' > ') }",
        "function Save-Result {",
        "  try {",
        "    $result.log = ($steps -join ' > ')",
        "    $dir = Split-Path -Parent $resultPath",
        "    if ($dir -and -not (Test-Path -LiteralPath $dir)) "
        "{ New-Item -ItemType Directory -Force -Path $dir | Out-Null }",
        "    ($result | ConvertTo-Json -Compress) | "
        "Set-Content -LiteralPath $resultPath -Encoding UTF8",
        "  } catch { }",
        "}",
        "try {",
        "  $result.step = 'wait_pid'",
        "  for ($i = 0; $i -lt 120; $i++) {",
        "    if (-not (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) { break }",
        "    Start-Sleep -Milliseconds 500",
        "  }",
        "  Note 'pid_gone'",
        "  Start-Sleep -Seconds 1",
        "  $result.step = 'swap_rename'",
        "  for ($t = 1; $t -le $maxTries; $t++) {",
        "    $result.tries = $t",
        "    try {",
        "      if (Test-Path -LiteralPath $backup) "
        "{ Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue }",
        "      if (Test-Path -LiteralPath $target) "
        "{ Move-Item -LiteralPath $target -Destination $backup -Force }",
        "      Move-Item -LiteralPath $staged -Destination $target -Force",
        "      $result.error = ''",
        "      Note \"rename_ok_try$t\"",
        "      break",
        "    } catch {",
        "      $result.error = \"try${t}: \" + $_.Exception.Message",
        "      Note \"rename_fail_try$t\"",
        "      try {",
        "        if ((Test-Path -LiteralPath $backup) -and "
        "-not (Test-Path -LiteralPath $target)) "
        "{ Move-Item -LiteralPath $backup -Destination $target -Force }",
        "      } catch { }",
        "      if ($t -lt $maxTries) { Start-Sleep -Seconds $retrySleep }",
        "    }",
        "  }",
        "  if ($result.error) {",
        "    $result.step = 'swap_copy_fallback'",
        "    try {",
        "      Copy-Item -LiteralPath $staged -Destination $target -Force",
        "      $result.error = ''",
        "      Note 'copy_fallback_ok'",
        "    } catch { $result.error = $result.error + ' | copy: ' + $_.Exception.Message;"
        " Note 'copy_fallback_fail' }",
        "  }",
        "  $result.step = 'verify'",
        "  try { $result.placed_size = (Get-Item -LiteralPath $target).Length } catch "
        "{ $result.error = $result.error + ' | size: ' + $_.Exception.Message }",
        "  if ($result.placed_size -eq $expected) {",
        "    $result.ok = $true",
        "    Note 'verified'",
        "    Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue",
        "  } elseif (-not $result.error) "
        "{ $result.error = \"size mismatch after swap "
        "(placed=$($result.placed_size) expected=$expected)\" }",
        "} catch {",
        "  $result.error = $result.error + ' | fatal: ' + $_.Exception.Message",
        "  Note 'fatal'",
        "} finally {",
        "  Save-Result",
        "  try { if ((Test-Path -LiteralPath $backup) -and "
        "-not (Test-Path -LiteralPath $target)) "
        "{ Move-Item -LiteralPath $backup -Destination $target -Force } } catch { }",
        "  Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue",
        "  Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue",
        "  try { if (Test-Path -LiteralPath $target) { Start-Process -FilePath $target } } "
        "catch { }",
        "  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue",
        "}",
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


def start_updater(stop_running_loop=None, status_cb=None, blocked_cb=None, pre_swap_cb=None):
    """업데이터 스레드를 시작하고 핸들을 돌려준다. frozen 이 아니어도 폴링은 돈다
    (로그로 새 버전 존재를 알 수 있게)."""
    t = UpdaterThread(stop_running_loop=stop_running_loop, status_cb=status_cb,
                      blocked_cb=blocked_cb, pre_swap_cb=pre_swap_cb)
    t.start()
    return t
