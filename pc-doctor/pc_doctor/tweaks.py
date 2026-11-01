"""트윅 적용·복원 엔진.

안전 모델
1. 적용 전에 대상 값마다 '이전 값(형식+데이터)' 또는 '원래 없었음'을 JSON 백업으로 먼저 저장(fsync).
   백업 저장이 실패하면 아무것도 바꾸지 않습니다.
2. 원래 없던 키를 새로 만들었다면(cleanup_keys) 복원 시 그 키를 통째로 지웁니다.
3. HKLM(관리자) 항목은 `--admin` 없이는 건너뜁니다.
4. 보안 관련 경로(FORBIDDEN_FRAGMENTS)는 정의 단계에서 거부합니다.
5. `--dry-run`은 읽기만 하고 아무것도 쓰지 않습니다(백업 파일도 만들지 않음).
6. `restore`는 아직 복원하지 않은 백업들을 최신 → 과거 순으로 되돌리므로,
   여러 번 apply 했어도 맨 처음 상태로 정확히 돌아갑니다.
"""

import datetime as _dt
import os

from . import config as cfgmod
from .system import REG_TYPES, RegValue, needs_admin
from .tweaks_data import FORBIDDEN_FRAGMENTS, TWEAKS


class TweakError(Exception):
    pass


def validate_tweak(t):
    for key in ("id", "title", "values"):
        if key not in t:
            raise TweakError("트윅 정의에 %s 없음: %r" % (key, t))
    for v in t["values"]:
        if v["type"] not in REG_TYPES:
            raise TweakError("%s: 알 수 없는 형식 %s" % (t["id"], v["type"]))
        target = ("%s\\%s" % (v["path"], v["name"])).lower()
        for frag in FORBIDDEN_FRAGMENTS:
            if frag.lower() in target:
                raise TweakError("%s: 보안 관련 경로는 다루지 않습니다 (%s)" % (t["id"], v["path"]))
        if not v["path"].upper().startswith(("HKCU\\", "HKLM\\")):
            raise TweakError("%s: HKCU/HKLM만 허용 (%s)" % (t["id"], v["path"]))
    for k in t.get("cleanup_keys", []):
        if not any(v["path"].lower().startswith(k.lower()) for v in t["values"]):
            raise TweakError("%s: cleanup_key는 트윅이 만드는 경로의 상위여야 함" % t["id"])
    return t


def is_admin_tweak(t):
    return any(needs_admin(v["path"]) for v in t["values"])


def all_tweaks():
    return [validate_tweak(t) for t in TWEAKS]


def get_tweaks(ids=None):
    by_id = {t["id"]: t for t in all_tweaks()}
    if not ids:
        return [t for t in by_id.values() if t.get("default")]
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise TweakError("알 수 없는 트윅 id: %s (tweaks list 로 확인)" % ", ".join(unknown))
    return [by_id[i] for i in ids]


def _same(cur, v):
    if cur is None or cur.type != v["type"]:
        return False
    if v["type"] in ("REG_DWORD", "REG_QWORD"):
        return int(cur.data) == int(v["data"])
    return cur.data == v["data"]


def status(system, t):
    """'applied' | 'partial' | 'not_applied' | 'unknown'."""
    try:
        hits = [_same(system.reg_get(v["path"], v["name"]), v) for v in t["values"]]
    except Exception:
        return "unknown"
    if all(hits):
        return "applied"
    return "partial" if any(hits) else "not_applied"


def backup_dir():
    return cfgmod.sub_dir("backups")


def _fmt(rv):
    if rv is None:
        return "(없음)"
    return "%s %r" % (rv.type, rv.data)


def apply(system, ids=None, admin=False, dry_run=False, out=print, now=None):
    """선택된 트윅 적용. 반환: dict(applied, skipped, errors, backup, restart)."""
    selected = get_tweaks(ids)
    todo, skipped = [], []
    for t in selected:
        if is_admin_tweak(t) and not admin:
            skipped.append(t["id"])
            out("⏭  %s: 관리자 권한(HKLM) 필요 → 건너뜀 (--admin 으로 관리자 터미널에서 실행)" % t["id"])
        else:
            todo.append(t)
    result = {"applied": [], "skipped": skipped, "errors": [], "backup": None, "restart": set()}
    if not todo:
        out("적용할 트윅이 없습니다.")
        return result

    # 1) 현재 값 읽기 (백업 내용)
    entries, keys = [], []
    for t in todo:
        for v in t["values"]:
            cur = system.reg_get(v["path"], v["name"])
            entries.append({
                "tweak": t["id"], "path": v["path"], "name": v["name"],
                "existed": cur is not None, "previous": cur.to_json() if cur else None,
            })
            out("%s %s\\%s: %s → %s %r" % ("[dry-run]" if dry_run else "•", v["path"], v["name"] or "(기본값)",
                                           _fmt(cur), v["type"], v["data"]))
        for k in t.get("cleanup_keys", []):
            keys.append({"tweak": t["id"], "key": k, "existed": system.reg_key_exists(k)})

    if dry_run:
        out("[dry-run] 아무것도 바꾸지 않았습니다.")
        result["applied"] = [t["id"] for t in todo]
        return result

    # 2) 백업 먼저 저장 — 실패하면 예외로 중단(아무것도 안 바뀜)
    now = now or _dt.datetime.now()
    backup = {
        "kind": "pc-doctor-tweaks-backup", "version": 1,
        "created_at": now.isoformat(timespec="seconds"), "admin": admin,
        "tweaks": [t["id"] for t in todo], "values": entries, "keys": keys, "restored_at": None,
    }
    d = backup_dir()
    base = "tweaks-%s" % now.strftime("%Y%m%d-%H%M%S")
    path, n = os.path.join(d, base + ".json"), 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(d, "%s-%d.json" % (base, n))
    cfgmod.write_json_atomic(path, backup)
    result["backup"] = path
    out("💾 백업 저장: %s" % path)

    # 3) 적용
    for t in todo:
        ok = True
        for v in t["values"]:
            try:
                system.reg_set(v["path"], v["name"], RegValue(v["type"], v["data"]))
            except Exception as e:
                ok = False
                result["errors"].append("%s %s\\%s: %s" % (t["id"], v["path"], v["name"], e))
        if ok:
            result["applied"].append(t["id"])
            if t.get("restart"):
                result["restart"].add(t["restart"])
    for e in result["errors"]:
        out("⚠️  실패: %s" % e)
    out("✅ 적용: %s" % (", ".join(result["applied"]) or "없음"))
    return result


def list_backups(include_restored=False):
    d = backup_dir()
    out = []
    for fn in sorted(os.listdir(d)):
        if fn.startswith("tweaks-") and fn.endswith(".json"):
            p = os.path.join(d, fn)
            b = cfgmod.read_json(p)
            if include_restored or not b.get("restored_at"):
                out.append((p, b))
    out.sort(key=lambda pb: (pb[1].get("created_at", ""), pb[0]))
    return out


def restore_backup(system, path, admin=False, dry_run=False, out=print, now=None):
    b = cfgmod.read_json(path)
    if b.get("kind") != "pc-doctor-tweaks-backup":
        raise TweakError("pc-doctor 트윅 백업 파일이 아닙니다: %s" % path)
    errors, skipped = [], []
    for e in reversed(b["values"]):
        if needs_admin(e["path"]) and not admin:
            skipped.append(e)
            out("⏭  %s\\%s: 관리자 권한 필요 → 건너뜀" % (e["path"], e["name"]))
            continue
        label = "%s\\%s" % (e["path"], e["name"] or "(기본값)")
        try:
            if e["existed"]:
                rv = RegValue.from_json(e["previous"])
                out("%s %s ← %s" % ("[dry-run]" if dry_run else "•", label, _fmt(rv)))
                if not dry_run:
                    system.reg_set(e["path"], e["name"], rv)
            else:
                out("%s %s ← (삭제: 원래 없던 값)" % ("[dry-run]" if dry_run else "•", label))
                if not dry_run:
                    system.reg_delete_value(e["path"], e["name"])
        except Exception as ex:
            errors.append("%s: %s" % (label, ex))
    for k in reversed(b.get("keys", [])):
        if not k["existed"]:
            if needs_admin(k["key"]) and not admin:
                continue
            out("%s 키 삭제 (원래 없던 키): %s" % ("[dry-run]" if dry_run else "•", k["key"]))
            if not dry_run:
                try:
                    system.reg_delete_key_tree(k["key"])
                except Exception as ex:
                    errors.append("%s: %s" % (k["key"], ex))
    if not dry_run and not errors and not skipped:
        b["restored_at"] = (now or _dt.datetime.now()).isoformat(timespec="seconds")
        cfgmod.write_json_atomic(path, b)
    for e in errors:
        out("⚠️  실패: %s" % e)
    return {"errors": errors, "skipped": len(skipped)}


def restore(system, backup_path=None, admin=False, dry_run=False, out=print):
    """backup_path가 없으면 아직 복원 안 된 백업 전부를 최신부터 되돌림."""
    if backup_path:
        paths = [backup_path]
    else:
        paths = [p for p, _b in reversed(list_backups())]
    if not paths:
        out("복원할 백업이 없습니다.")
        return {"restored": [], "errors": []}
    restored, errors = [], []
    for p in paths:
        out("↩️  복원: %s" % p)
        r = restore_backup(system, p, admin=admin, dry_run=dry_run, out=out)
        errors.extend(r["errors"])
        if not r["errors"] and not r["skipped"]:
            restored.append(p)
    return {"restored": restored, "errors": errors}


RESTART_MSG = {
    "explorer": "탐색기 재시작 후 적용됩니다 (--restart-explorer 옵션 또는 로그아웃).",
    "logoff": "로그아웃 후 다시 로그인하면 적용됩니다.",
    "reboot": "재부팅 후 적용됩니다.",
}
