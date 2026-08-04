# -*- coding: utf-8 -*-
"""계정 별명 <-> 실제 인스타 계정 묶음(바인딩) 저장소.

**이 파일은 기록장이지 심판이 아니다 (v1.6.0).**

v1.5.0 까지는 여기에 저장된 값이 '정답'이었고, 실제로 크롬 창이 다른 계정으로 돌고 있으면
프로그램이 브라우저를 저장값 쪽으로 되돌렸다. 그게 고객 1898680 을 다섯 번째로 막은 원인이다.
실측(2026-08-03 05:17:29, v1.4.0):

    [login_ok] account=mugenboksa user=mightysun_09 uid=67584782851

별명 `mugenboksa` 에 **부모 계정** `mightysun_09` 가 잘못 묶였다. 그 잘못된 기록이 v1.5.0 으로
그대로 넘어왔고, v1.5.0 의 전환기가 그걸 근거로 브라우저를 끌고 갔다(2026-08-04 09:07:48):

    [login_identity] account=mugenboksa user=mugenboksa uid=42105781019 verdict=mismatch
    [login_switch_attempt] want=mightysun_09 ok=True

고객이 직접 로그인한 계정(mugenboksa)에서 **강제로 끌려 나왔다.** 그래서 규칙을 뒤집는다:

  1. 살아 있는 계정이 항상 이긴다. 저장값과 다르면 저장값을 **고쳐 쓴다**(브라우저를 고치지 않는다).
  2. 이 파일의 값으로는 아무것도 막지 않는다. 표시용 + 진행상황 키 용도뿐이다.
  3. v1.5.0 이전 로직이 만든 기록은 구조적으로 못 믿으므로 업그레이드 때 계정 기록만 비운다
     (`migrate_legacy`). 별명 자체는 남겨서 고객 화면에서 사라지지 않게 한다.
"""

import json
import os
import time

import config

# 이 값 미만으로 저장된 바인딩은 'v1.5.0 이전 로직이 만든 것' 이라 신뢰하지 않는다.
BINDING_VERSION = 2


def _load_all():
    try:
        with open(config.ACCOUNT_BINDINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all(data):
    try:
        os.makedirs(os.path.dirname(config.ACCOUNT_BINDINGS_FILE), exist_ok=True)
        with open(config.ACCOUNT_BINDINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def get(label):
    """별명에 묶인 계정 정보 {'user_id','username','bound_at'} 또는 None."""
    entry = _load_all().get(str(label))
    return entry if isinstance(entry, dict) else None


def entries():
    """{별명: {'user_id','username',...}} 전체. 별명 드롭다운(오타 방지)의 원본."""
    return {k: v for k, v in _load_all().items() if isinstance(v, dict)}


def labels():
    """저장된 별명 목록(정렬)."""
    return sorted(entries().keys(), key=lambda s: s.lower())


def run_key(user_id, label=None):
    """진행상황/하루 상한을 셀 때 쓰는 키.

    별명이 아니라 **실제 계정의 숫자 id** 를 키로 쓴다. 고객이 같은 계정을 'mugenboksa' /
    'megenboksa' 처럼 오타 낸 두 별명으로 돌려도 진행상황이 갈라지지 않고, 반대로 한 별명이
    다른 계정을 가리키게 되면 남의 진행상황을 물려받지 않는다. id 를 못 읽으면 별명 폴백.
    """
    if user_id:
        return f"acct:{user_id}"
    return (label or "default").strip() or "default"


def bind(label, user_id, username=None):
    """별명 <-> 계정 묶음을 저장(덮어쓰기). user_id 가 없으면 저장하지 않는다."""
    if not user_id:
        return False
    data = _load_all()
    data[str(label)] = {
        "user_id": str(user_id),
        "username": username or "",
        "bound_at": int(time.time()),
        "binding_version": BINDING_VERSION,
    }
    return _save_all(data)


def unbind(label):
    data = _load_all()
    if str(label) in data:
        del data[str(label)]
        return _save_all(data)
    return False


def forget_account(label):
    """별명은 목록에 남기고 **그 별명에 저장된 계정 기록만** 지운다.

    GUI 의 [계정 기록 지우기] 버튼이 부르는 경로. 고객 화면에 `mugenboksa · @mightysun_09`
    처럼 눈에 보이게 틀린 값이 떠 있을 때 스스로 고칠 수 있어야 한다(v1.5.0 에는 그 방법이
    아예 없었다). 별명을 통째로 지우지 않는 이유: 별명이 사라지면 고객이 만들어 둔 크롬
    프로필과의 연결까지 흐려져서 "계정이 통째로 없어졌다"로 보인다.

    반환: 지워진 옛 값 {'user_id','username'} 또는 None(그 별명 기록 자체가 없었음).
    """
    data = _load_all()
    key = str(label)
    entry = data.get(key)
    if not isinstance(entry, dict):
        return None
    old = {"user_id": entry.get("user_id") or "", "username": entry.get("username") or ""}
    data[key] = {
        "user_id": "",
        "username": "",
        "binding_version": BINDING_VERSION,
        "reset_at": int(time.time()),
        "reset_from_user_id": old["user_id"],
        "reset_from_username": old["username"],
        "reset_reason": "user",
    }
    _save_all(data)
    return old


def migrate_legacy():
    """v1.5.0 이전 로직이 저장한 바인딩의 **계정 기록만** 비운다(별명은 남긴다).

    왜 통째로 못 믿는가: v1.4.0 의 로그인 경로는 '지금 크롬 세션에 붙어 있는 계정'을 별명에
    그대로 묶었는데, 서브계정이 딸린 부모 세션에서는 그게 고객이 의도한 계정이 아니었다
    (실측: `login_ok account=mugenboksa user=mightysun_09`). 어느 기록이 옳고 그른지 사후에
    구별할 방법이 없으므로 전부 비우고 다음 로그인 때 실제 계정으로 다시 채운다.

    반환: [{'label','user_id','username'}] - **화면과 진단 로그에 남길 목록**(조용히 지우지 않는다).
    """
    data = _load_all()
    reset, changed = [], False
    for label, entry in list(data.items()):
        if not isinstance(entry, dict):
            data[label] = {"user_id": "", "username": "", "binding_version": BINDING_VERSION}
            changed = True
            continue
        if int(entry.get("binding_version") or 0) >= BINDING_VERSION:
            continue
        uid, who = entry.get("user_id") or "", entry.get("username") or ""
        if not uid and not who:
            entry["binding_version"] = BINDING_VERSION      # 비어 있으면 표시만 올린다
            changed = True
            continue
        reset.append({"label": label, "user_id": uid, "username": who})
        data[label] = {
            "user_id": "",
            "username": "",
            "binding_version": BINDING_VERSION,
            "reset_at": int(time.time()),
            "reset_from_user_id": uid,
            "reset_from_username": who,
            "reset_reason": "legacy",
        }
        changed = True
    if changed:
        _save_all(data)
    return reset


def label_for_user_id(user_id, exclude_label=None):
    """이 계정 id 가 이미 '다른 별명' 에 묶여 있으면 그 별명을 돌려준다(없으면 None)."""
    if not user_id:
        return None
    for label, entry in _load_all().items():
        if exclude_label is not None and label == str(exclude_label):
            continue
        if isinstance(entry, dict) and str(entry.get("user_id") or "") == str(user_id):
            return label
    return None


def check(label, live_user_id, live_username=None):
    """지금 붙어 있는 세션을 이 별명의 계정으로 **기록**한다. 절대 판정으로 막지 않는다.

    반환: (verdict, detail)
      "ok"       - 저장된 계정과 같다.
      "bound"    - 저장된 게 없어서 이번 세션 계정을 새로 기억했다.
      "rebound"  - 저장된 계정과 다르다. **살아 있는 계정이 이기고 저장값을 고쳐 썼다.**
      "unknown"  - 세션에서 계정 id 자체를 못 읽었다(로그인 안 됨/쿠키 없음).

    v1.5.0 의 "mismatch" 는 없어졌다. 그 값을 근거로 브라우저를 저장값 쪽으로 되돌리는
    코드가 고객을 자기가 로그인한 계정에서 끌어냈기 때문이다(2026-08-04 실측).
    """
    if not live_user_id:
        return "unknown", "세션에서 계정 id(ds_user_id)를 읽지 못했습니다"

    entry = get(label)
    stored_id = (entry or {}).get("user_id") or ""
    if stored_id:
        if str(stored_id) == str(live_user_id):
            # 아이디가 바뀌었을 수도 있으니 표시용 username 만 갱신한다.
            if live_username and live_username != entry.get("username"):
                bind(label, live_user_id, live_username)
            return "ok", f"{label} = @{entry.get('username') or live_username or live_user_id}"
        old = entry.get("username") or stored_id
        bind(label, live_user_id, live_username)
        return ("rebound",
                f"별명 '{label}' 에 저장돼 있던 계정(@{old})이 지금 크롬 창의 계정"
                f"(@{live_username or live_user_id})과 달라서, 지금 로그인된 계정 기준으로 "
                f"기억을 고쳤습니다. 크롬 창은 건드리지 않습니다")

    other = label_for_user_id(live_user_id, exclude_label=label)
    bind(label, live_user_id, live_username)
    if other is not None:
        return ("bound",
                f"{label} = @{live_username or live_user_id} (새로 기억함. 같은 계정이 별명 "
                f"'{other}' 에도 묶여 있지만 진행상황은 계정 기준이라 중복 DM 은 가지 않습니다)")
    return "bound", f"{label} = @{live_username or live_user_id} (새로 기억함)"
