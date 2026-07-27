# -*- coding: utf-8 -*-
"""프로그램이 직접 관리하는 크롬(Chrome for Testing) 준비 모듈.

사용자 PC에 크롬이 설치되어 있지 않아도, 프로그램이 자기 전용 크롬 바이너리와
그에 딱 맞는 chromedriver 를 내려받아(최초 1회) 캐시에 두고 그걸로 구동한다.
Windows / macOS(Intel·Apple Silicon) / Linux 모두 동일 코드로 동작.

이렇게 하면
  - "설치된 크롬 버전"과 chromedriver 버전이 어긋나는 문제가 사라지고,
  - macOS 의 "Google Chrome for Testing이(가) 예기치 않게 종료되었습니다" (격리 속성)
    문제를 xattr 제거로 예방한다.
"""

import io
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from urllib.request import urlopen, Request

import config

CFT_ENDPOINT = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "last-known-good-versions-with-downloads.json"
)


def _platform_key():
    """Chrome for Testing 플랫폼 키 반환."""
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Windows":
        return "win64" if sys.maxsize > 2 ** 32 else "win32"
    if system == "Darwin":
        return "mac-arm64" if machine in ("arm64", "aarch64") else "mac-x64"
    return "linux64"


def _binary_rel_paths(plat):
    """(chrome 실행파일, chromedriver 실행파일) 상대경로."""
    if plat.startswith("win"):
        cdir = "chrome-win64" if plat == "win64" else "chrome-win32"
        ddir = "chromedriver-win64" if plat == "win64" else "chromedriver-win32"
        return (
            os.path.join(cdir, "chrome.exe"),
            os.path.join(ddir, "chromedriver.exe"),
        )
    if plat.startswith("mac"):
        return (
            os.path.join(
                f"chrome-{plat}",
                "Google Chrome for Testing.app",
                "Contents", "MacOS", "Google Chrome for Testing",
            ),
            os.path.join(f"chromedriver-{plat}", "chromedriver"),
        )
    return (
        os.path.join("chrome-linux64", "chrome"),
        os.path.join("chromedriver-linux64", "chromedriver"),
    )


def _fetch_json(url):
    req = Request(url, headers={"User-Agent": "kream-airpods-provisioner"})
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


class _CrossProcessLock:
    """계정 워커가 동시에 시작될 때 크롬 최초 설치를 한 워커만 하도록 직렬화하는 파일 락.

    예전 버그: 3개 계정 워커가 동시에 시작 -> 각자 같은 경로로 chromedriver.exe 를
    내려받아 압축 해제 -> 한 워커가 chmod/read 하는 사이 다른 워커가 같은 파일을
    덮어써서 [Errno 13] Permission denied 로 두 워커가 죽었다.
    이 락으로 한 워커만 설치하고 나머지는 끝날 때까지 대기 후 캐시를 재사용한다.

    Windows(msvcrt) / POSIX(fcntl) 모두 지원. 락 획득 실패 시에도 절대 크래시하지
    않고(타임아웃 시 그냥 진행) 프로그램을 죽이지 않는다.
    """

    def __init__(self, lock_path, timeout=600.0):
        self.lock_path = lock_path
        self.timeout = timeout
        self._fh = None

    def acquire(self):
        try:
            os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        except Exception:
            pass
        deadline = time.time() + self.timeout
        while True:
            try:
                self._fh = open(self.lock_path, "a+")
            except Exception:
                self._fh = None
                return False
            if self._try_lock(self._fh):
                return True
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None
            if time.time() >= deadline:
                return False
            time.sleep(0.4)

    def release(self):
        if self._fh is None:
            return
        try:
            self._unlock(self._fh)
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass
        self._fh = None

    @staticmethod
    def _try_lock(fh):
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
        except Exception:
            # 락 API 자체를 못 쓰면(희귀) 락 없이 진행하도록 True 반환.
            return True

    @staticmethod
    def _unlock(fh):
        if os.name == "nt":
            import msvcrt
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *a):
        self.release()


def _extract_zip_bytes(data, dest_dir):
    """zip 바이트를 dest_dir 에 풀되 유닉스 실행권한(external_attr)을 복원한다."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            extracted = z.extract(info, dest_dir)
            mode = (info.external_attr >> 16) & 0o7777
            if mode:
                try:
                    os.chmod(extracted, mode)
                except Exception:
                    pass


def _download_and_extract(url, dest_dir, log):
    """다운로드 후 임시 폴더에 풀고 원자적으로 최종 위치로 옮긴다.

    임시 폴더에 완전히 푼 다음 rename 으로 이동하므로, 다른 워커가 '반쯤 쓰인'
    실행파일을 읽거나 chmod 하다 [Errno 13] Permission denied 로 충돌하는 일이 없다.
    락으로 이미 직렬화되지만, 원자적 이동은 두 번째 안전장치다.
    """
    log(f"다운로드: {url.rsplit('/', 1)[-1]}")
    req = Request(url, headers={"User-Agent": "kream-airpods-provisioner"})
    with urlopen(req, timeout=180) as r:
        data = r.read()
    log(f"압축 해제 중... ({len(data) // (1024 * 1024)}MB)")

    tmp_dir = dest_dir + f".tmp-{os.getpid()}-{int(time.time() * 1000) % 100000}"
    _rmtree_quiet(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)
    _extract_zip_bytes(data, tmp_dir)

    # tmp 안의 최상위 항목(chrome-win64 등)을 최종 위치로 원자적으로 옮긴다.
    for name in os.listdir(tmp_dir):
        src = os.path.join(tmp_dir, name)
        dst = os.path.join(dest_dir, name)
        if os.path.exists(dst):
            # 이미 (다른 다운로드가) 있으면 우리 것은 버린다.
            continue
        try:
            os.replace(src, dst)  # 같은 볼륨: 원자적 rename
        except OSError:
            # 폴백: 복사 후 정리 (rename 이 안 되는 드문 경우)
            _copytree_quiet(src, dst)
    _rmtree_quiet(tmp_dir)


def _download_and_extract_retry(url, dest_dir, log, tries=3):
    """PermissionError 등 일시적 파일 충돌에 대비한 재시도 래퍼."""
    last = None
    for i in range(1, tries + 1):
        try:
            _download_and_extract(url, dest_dir, log)
            return
        except PermissionError as e:
            last = e
            if i >= tries:
                break
            log(f"파일 접근이 잠시 막혀 다시 시도합니다({i}/{tries})...")
            time.sleep(1.5 * i)
        except OSError as e:
            # 파일 잠김(WinError 32) 등도 잠깐 뒤 재시도.
            last = e
            if i >= tries:
                break
            time.sleep(1.5 * i)
    if last is not None:
        raise last


def _rmtree_quiet(path):
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _copytree_quiet(src, dst):
    try:
        shutil.copytree(src, dst)
    except Exception:
        pass


def _chmod_tree_executable(root):
    """추출된 크롬 트리의 모든 파일에 실행권한(+x)을 보장.

    크롬 앱은 crashpad_handler, 각종 헬퍼 실행파일에 +x 가 필요하다.
    데이터 파일에 +x 가 붙어도 무해하므로 전체에 부여한다.
    """
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            _make_executable(os.path.join(dirpath, name))


def _make_executable(path):
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass


def _strip_quarantine_mac(app_root, log):
    """macOS: 다운로드 격리 속성 제거 -> '예기치 않게 종료' 예방."""
    if platform.system() != "Darwin":
        return
    try:
        subprocess.run(
            ["xattr", "-dr", "com.apple.quarantine", app_root],
            check=False, capture_output=True,
        )
        log("macOS 격리 속성 제거 완료")
    except Exception as e:
        log(f"격리 속성 제거 건너뜀: {e}")


def ensure_chrome(log=print):
    """크롬/드라이버를 준비하고 (chrome_path, driver_path, version) 반환.

    여러 계정 워커가 동시에 호출해도 안전하다. 최초 설치는 파일 락으로 직렬화되어
    한 워커만 내려받고, 나머지는 대기 후 완성된 캐시를 그대로 재사용한다.
    """
    # 최초 설치를 워커 간에 직렬화한다. 락을 얻은 워커만 다운로드/압축해제하고,
    # 대기하던 워커는 락 해제 후 이미 완성된 캐시를 재사용한다.
    lock_path = os.path.join(config.CHROME_CACHE_DIR, ".install.lock")
    with _CrossProcessLock(lock_path, timeout=900.0):
        return _ensure_chrome_locked(log)


def _ensure_chrome_locked(log=print):
    plat = _platform_key()
    chrome_rel, driver_rel = _binary_rel_paths(plat)

    # 최신 Stable 버전 조회 (실패 시 캐시에 남은 버전 재사용)
    version = None
    downloads = None
    try:
        info = _fetch_json(CFT_ENDPOINT)
        stable = info["channels"]["Stable"]
        version = stable["version"]
        downloads = stable["downloads"]
    except Exception as e:
        log(f"버전 정보 조회 실패({e}) - 캐시된 크롬을 찾습니다.")

    # 캐시에서 사용 가능한 버전 탐색
    def _paths_for(ver):
        base = os.path.join(config.CHROME_CACHE_DIR, ver)
        return (
            os.path.join(base, chrome_rel),
            os.path.join(base, driver_rel),
            base,
        )

    if version is None:
        # 오프라인 폴백: 캐시 폴더 중 유효한 것 사용
        if os.path.isdir(config.CHROME_CACHE_DIR):
            for ver in sorted(os.listdir(config.CHROME_CACHE_DIR), reverse=True):
                cp, dp, _ = _paths_for(ver)
                if os.path.exists(cp) and os.path.exists(dp):
                    _make_executable(cp)
                    _make_executable(dp)
                    log(f"캐시된 크롬 사용: {ver}")
                    return cp, dp, ver
        raise RuntimeError("크롬을 내려받을 수 없고 캐시도 없습니다. 인터넷 연결을 확인하세요.")

    chrome_path, driver_path, base = _paths_for(version)
    if os.path.exists(chrome_path) and os.path.exists(driver_path):
        _make_executable(chrome_path)
        _make_executable(driver_path)
        log(f"크롬 준비 완료(캐시): {version}")
        return chrome_path, driver_path, version

    # 다운로드 필요
    os.makedirs(base, exist_ok=True)
    chrome_url = next(x["url"] for x in downloads["chrome"] if x["platform"] == plat)
    driver_url = next(x["url"] for x in downloads["chromedriver"] if x["platform"] == plat)

    log(f"전용 크롬 최초 설치 ({version}, {plat}) - 잠시만 기다려 주세요.")
    _download_and_extract_retry(chrome_url, base, log)
    _download_and_extract_retry(driver_url, base, log)

    # 크롬 트리 전체에 실행권한 보장 (crashpad_handler 등 헬퍼 포함)
    _chmod_tree_executable(os.path.join(base, os.path.dirname(chrome_rel).split(os.sep)[0]))
    _make_executable(chrome_path)
    _make_executable(driver_path)

    # macOS 격리 속성 제거 (.app 루트 기준)
    if plat.startswith("mac"):
        app_root = os.path.join(base, f"chrome-{plat}",
                                "Google Chrome for Testing.app")
        _strip_quarantine_mac(app_root, log)
        _strip_quarantine_mac(driver_path, log)

    if not (os.path.exists(chrome_path) and os.path.exists(driver_path)):
        raise RuntimeError("크롬 설치 후에도 실행 파일을 찾지 못했습니다.")

    log(f"전용 크롬 설치 완료: {version}")
    return chrome_path, driver_path, version
