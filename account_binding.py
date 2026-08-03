# -*- coding: utf-8 -*-
"""계정 별명 <-> 실제 인스타 계정 묶음(바인딩) 저장소.

왜 필요한가 (고객이 세 번 리포트한 '계정 전환이 안 된다'):
  별명마다 크롬 프로필 폴더를 따로 쓰는 것만으로는 부족하다. 별명을 바꿔도 프로그램이
  '이 프로필에 이미 로그인돼 있네' 하고 그대로 쓰는데, 그 세션이 정말 그 별명의 계정인지
  아무도 확인하지 않았다. 고객 실측 로그:
      04:51:08 [login_reused] account=mightysun_09   user=mightysun_09
      04:55:19 [login_reused] account=mightyjimin    user=mightysun_09   <- 별명과 실제 계정 불일치
  그래서 별명별로 **처음 로그인에 성공한 계정의 숫자 id(ds_user_id)** 를 저장해 두고, 다음에
  그 별명을 재사용할 때 실제 붙어 있는 세션의 id 와 대조한다. 다르면 그 세션을 버리고 자동으로
  새 로그인을 받는다. 고객이 [다른 계정으로 로그인] 버튼을 누를 필요가 없어야 한다.

숫자 id 를 쓰는 이유: 아이디(username)는 화면에서 긁어야 해서 오탐 여지가 있지만
`ds_user_id` 쿠키는 인스타가 로그인 세션에 직접 심는 값이라 피드 내용과 무관하다.
"""

import json
import os
import time

import config


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
    }
    return _save_all(data)


def unbind(label):
    data = _load_all()
    if str(label) in data:
        del data[str(label)]
        return _save_all(data)
    return False


def label_for_user_id(user_id, exclude_label=None):
    """이 계정 id 가 이미 '다른 별명' 에 묶여 있으면 그 별명을 돌려준다(없으면 None)."""
    if not user_id:
        return None
    for label, entry in _load_all().items():
        if exclude_label is not None and label == str(exclude_label):
            continue
        if isinstance(entry, dict) and str(entry.get("user_id")) == str(user_id):
            return label
    return None


def check(label, live_user_id, live_username=None):
    """지금 붙어 있는 세션이 이 별명의 계정이 맞는지 판정한다.

    반환: (verdict, detail)
      "ok"           - 저장된 계정과 일치. 그대로 쓰면 된다.
      "bound"        - 저장된 게 없어서 이번 세션을 이 별명의 계정으로 새로 저장했다.
      "mismatch"     - **다른 계정이 붙어 있다.** 세션을 버리고 새로 로그인해야 한다.
      "unknown"      - 세션에서 계정 id 자체를 못 읽었다(로그인 안 됨/쿠키 없음).
    """
    if not live_user_id:
        return "unknown", "세션에서 계정 id(ds_user_id)를 읽지 못했습니다"

    entry = get(label)
    if entry and entry.get("user_id"):
        if str(entry["user_id"]) == str(live_user_id):
            # 아이디가 바뀌었을 수도 있으니 표시용 username 만 갱신한다.
            if live_username and live_username != entry.get("username"):
                bind(label, live_user_id, live_username)
            return "ok", f"{label} = @{entry.get('username') or live_username or live_user_id}"
        return ("mismatch",
                f"별명 '{label}' 에는 @{entry.get('username') or entry['user_id']} 가 묶여 있는데 "
                f"지금 붙어 있는 계정은 @{live_username or live_user_id} 입니다")

    # 이 별명은 아직 묶인 계정이 없다. 다만 이 계정이 '다른 별명'에 이미 묶여 있다면,
    # 그건 이전 별명의 세션을 그대로 물려받은 상황이므로 새 로그인을 받아야 한다.
    other = label_for_user_id(live_user_id, exclude_label=label)
    if other is not None:
        return ("mismatch",
                f"지금 붙어 있는 계정(@{live_username or live_user_id})은 이미 별명 '{other}' 의 "
                f"계정입니다. '{label}' 은 새로 로그인해야 합니다")

    bind(label, live_user_id, live_username)
    return "bound", f"{label} = @{live_username or live_user_id} (새로 기억함)"
