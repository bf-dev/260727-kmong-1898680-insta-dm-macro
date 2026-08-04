# -*- coding: utf-8 -*-
"""인스타그램 팔로우+DM 매크로 - 고정 설정.

고객(Kmong 1898680, order 7508852) 전용.
엑셀 C열(인스타그램 URL) -> 팔로우 -> F열(개인화 DM) 발송 -> 다음 행.
"""

import hashlib
import os

APP_NAME = "insta-dm-macro"
APP_VERSION = "1.7.0"
CUSTOMER_ID = "1898680"

INSTAGRAM_BASE = "https://www.instagram.com"
INSTAGRAM_LOGIN_URL = "https://www.instagram.com/accounts/login/"

# 행 처리 사이 랜덤 대기(초). "빠르게 수십 명한테 연타"를 피하기 위한 최소/최대값.
# 팔로우 직후 -> DM 사이의 짧은 대기와, 한 사람 처리 완료 후 다음 사람으로 넘어가기 전의
# 긴 대기 두 군데에 쓰인다. 둘 다 매번 random.uniform 으로 다르게 뽑는다(고정 sleep 금지).
DELAY_AFTER_FOLLOW_MIN = 2
DELAY_AFTER_FOLLOW_MAX = 5
# 오너 요청(2026-07-31)으로 사람 사이 대기를 10초 수준으로 낮춤. 고정 10초로 두면 기계처럼
# 보이므로 10초를 중심으로 한 좁은 랜덤 범위를 유지한다(고정 sleep 금지 원칙).
DELAY_BETWEEN_PEOPLE_MIN = 8
DELAY_BETWEEN_PEOPLE_MAX = 13

# 사람처럼 보이게: 메시지 입력 시 한 글자씩, 글자 사이 랜덤 지터(초)
TYPE_JITTER_MIN = 0.03
TYPE_JITTER_MAX = 0.16

# 프로필 페이지를 열고 곧바로 팔로우를 누르지 않고 '훑어보는' 랜덤 대기(초)
PROFILE_VIEW_PAUSE_MIN = 1.5
PROFILE_VIEW_PAUSE_MAX = 4.0
# DM 스레드 페이지가 뜬 뒤 입력창을 찾기 전 랜덤 대기(초)
DM_PAGE_LOAD_PAUSE_MIN = 1.5
DM_PAGE_LOAD_PAUSE_MAX = 3.0
# 입력창 클릭 후 타이핑 시작 전 / 타이핑 후 전송 전 랜덤 대기(초)
PRE_TYPE_PAUSE_MIN = 0.4
PRE_TYPE_PAUSE_MAX = 1.0
POST_TYPE_PAUSE_MIN = 0.4
POST_TYPE_PAUSE_MAX = 1.2
# 팔로우 클릭 직후 / DM 전송 직후 짧은 랜덤 대기(초)
POST_FOLLOW_CLICK_PAUSE_MIN = 1.0
POST_FOLLOW_CLICK_PAUSE_MAX = 2.5
POST_SEND_PAUSE_MIN = 1.0
POST_SEND_PAUSE_MAX = 2.0

# 페이지 이동/버튼 대기 타임아웃(초)
WAIT_TIMEOUT = 15

# ---- 엔진 선택(v1.1, 기본값은 v1.2 에서 browser 로 되돌림) ----
# "api"   = instagrapi(비공개 모바일 API). 실계정 라이브 로그인이 반복 거부됨(2026-07-30) -
#           더 이상 기본값 아님. 계정/비밀번호를 프로그램에 맡기고 싶은 경우의 대안으로만 남김.
# "browser" = 크롬 웹 UI 조작(v1.0 방식). 아이디/비번을 프로그램이 저장/입력하지 않고 사람이
#             직접 로그인 - 기본값. 오늘 실계정으로 로그인/팔로우 라이브 검증 완료.
DEFAULT_ENGINE = "browser"
# instagrapi 요청 사이 랜덤 지연 범위(초) - Client.delay_range 로 들어간다.
API_DELAY_MIN = 2
API_DELAY_MAX = 6
# 계정당 고정 IP 를 쓰고 싶을 때만 채운다(instagrapi 권장). 비워두면 PC 의 인터넷을 그대로 쓴다.
# 예: "socks5h://아이디:비밀번호@호스트:1080"
API_PROXY = os.getenv("INSTA_DM_PROXY", "")

# ---- 계정 보호(v1.1) ----
# 하루에 한 계정으로 처리할 최대 인원(팔로우+DM 한 세트 = 1명). 랜덤 대기는 '연타'만 막을 뿐
# 하루 총량은 못 막는다. 인스타 차단은 총량에서 훨씬 크게 걸리므로 상한에 닿으면 스스로 멈춘다.
# GUI 에서 바꿀 수 있고 settings.json 에 저장된다. 카운트는 계정 별명별/날짜별로 따로 센다.
DEFAULT_DAILY_CAP = 40
DAILY_CAP_MIN = 1
DAILY_CAP_MAX = 300
# 연속 실패가 이만큼 쌓이면 계정 제한/DOM 변경을 의심하고 배치를 중단한다(계속 두드리면 더 위험).
CONSECUTIVE_FAILURE_HALT = 3
# 셀렉터(팔로우 버튼/DM 입력창)를 연속으로 못 찾은 횟수 - 인스타 DOM 변경 신호. 중단 + 진단 업로드.
SELECTOR_MISS_HALT_STREAK = 3

# 원격 진단(Artifacts API) - 고객에게 노출하지 않음
WORKS_API = "https://works.insu.ng/works/api"
STATIC_BASE = f"https://works.insu.ng/works/public/{CUSTOMER_ID}"

# 자동 업데이트
VERSION_URL = f"{STATIC_BASE}/version-{APP_NAME}.json"
UPDATE_CHECK_SECONDS = 300
# 자동 교체가 막힌 PC 에서 고객에게 보여줄 수동 다운로드 주소(zip 안에 실행파일 1개).
# 버전 json 의 zipUrl/exeUrl 을 읽을 수 있으면 그걸 우선 안내하고, 못 읽을 때만 이 주소를 쓴다.
# **버전에서 자동으로 만든다.** v1.6.0 까지는 여기가 1.5.0 zip 에 박혀 있어서, 자동 교체가
# 막힌 고객에게 '최신' 이라며 옛 버전 주소를 안내할 수 있었다(kmong 1898680, 260804).
# CI 의 릴리스 단계도 같은 규칙(`APP_VERSION` 의 점을 없앤 접미사)으로 zip 을 만든다.
MANUAL_DOWNLOAD_ZIP_NAME = f"insta-dm-macro-fix4-{APP_VERSION.replace('.', '')}.zip"
MANUAL_DOWNLOAD_URL = f"{STATIC_BASE}/{MANUAL_DOWNLOAD_ZIP_NAME}"

# 프로그램 전용 크롬(Chrome for Testing) / 계정별 프로필 / 설정·진행상황 저장 위치
_HOME = os.path.expanduser("~")
CHROME_CACHE_DIR = os.path.join(_HOME, ".insta_dm_macro", "chrome")
CHROME_PROFILE_ROOT = os.path.join(_HOME, ".insta_dm_macro", "profiles")
APP_DIR = os.path.join(os.getenv("APPDATA", _HOME), "InstaDmMacro")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
# 계정 별명 -> 실제 인스타 계정(숫자 id + 아이디) 매핑. '계정 전환이 안 되는' 사고를 막는 핵심.
ACCOUNT_BINDINGS_FILE = os.path.join(APP_DIR, "account_bindings.json")
PROGRESS_DIR = os.path.join(APP_DIR, "progress")


def profile_dir_for(account_label):
    """계정 라벨(사용자가 지은 별명)별 크롬 프로필 디렉터리. 계정마다 로그인 세션 분리.

    안전한 문자만 남기면 서로 다른 별명이 같은 폴더로 뭉개질 수 있다("A 계정"과 "A계정",
    "Jimin"과 "Jimin!" 이 전부 같은 폴더). 그러면 별명을 바꿔도 이전 계정으로 로그인된
    프로필을 그대로 열게 되어 '계정 전환이 안 되는' 것처럼 보인다. 원본 별명의 해시를 붙여
    충돌을 없앤다. 단, 특수문자가 없어 예전에도 같은 이름이었을 별명은 기존 폴더를 그대로
    써서 이미 로그인해 둔 세션이 날아가지 않게 한다.
    """
    label = (account_label or "default").strip() or "default"
    safe = "".join(c for c in label if c.isalnum() or c in "._-") or "default"
    legacy = os.path.join(CHROME_PROFILE_ROOT, safe)
    if safe == label and os.path.isdir(legacy):
        return legacy
    digest = hashlib.sha1(label.encode("utf-8")).hexdigest()[:8]
    return os.path.join(CHROME_PROFILE_ROOT, f"{safe}-{digest}")
