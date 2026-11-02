"""명령줄 인터페이스. `python -m steam_buddy <명령>` 으로 실행한다."""

import argparse
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from . import compat, core
from .notify import send_ntfy
from .steam import SteamClient, SteamError
from .store import Store


# ---------- 출력 도우미 ----------

def _width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def _pad(s, w):
    s = str(s)
    return s + " " * max(0, w - _width(s))


def table(headers, rows, out):
    widths = [max(_width(h), *(_width(r[i]) for r in rows)) for i, h in enumerate(headers)]
    print("  ".join(_pad(h, w) for h, w in zip(headers, widths)).rstrip(), file=out)
    print("  ".join("-" * w for w in widths), file=out)
    for r in rows:
        print("  ".join(_pad(c, w) for c, w in zip(r, widths)).rstrip(), file=out)


def parse_duration(s):
    """'1h30m', '90m', '1.5'(시간), '1:30' 을 분 단위로 바꾼다."""
    s = s.strip().lower().replace(" ", "")
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return round(float(s) * 60)
    m = re.fullmatch(r"(\d+):(\d{1,2})", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)(?:h|시간))?(?:(\d+)(?:m|분))?", s)
    if m and (m.group(1) or m.group(2)):
        return round(float(m.group(1) or 0) * 60) + int(m.group(2) or 0)
    raise ValueError(f"플레이 시간 형식을 모르겠어요: {s} (예: 1h30m, 90m, 1.5)")


def parse_when(s, now):
    if not s:
        return now.replace(microsecond=0)
    if s in ("어제", "yesterday"):
        return (now - timedelta(days=1)).replace(microsecond=0)
    return datetime.fromisoformat(s)


# ---------- 앱 ----------

class App:
    def __init__(self, store=None, client=None, now=None, out=None, push=send_ntfy, sleep=time.sleep, delay=0.3, compat_db=None):
        self.store = store or Store()
        self.client = client or SteamClient()
        self._now = now
        self.out = out or sys.stdout
        self.push = push
        self._sleep = sleep
        self.delay = delay  # 연속 API 호출 사이 대기(초). 테스트에서는 0
        self._compat = compat_db

    @property
    def compat(self):
        # 사용자 compat.json은 data.json과 같은 폴더(STEAM_BUDDY_HOME)에 둔다
        if self._compat is None:
            self._compat = compat.CompatDB(self.store.path.parent / "compat.json")
        return self._compat

    def now(self):
        return self._now() if callable(self._now) else (self._now or datetime.now())

    def say(self, *a):
        print(*a, file=self.out)

    # 스토어에서 게임 찾기: 숫자면 appid, 아니면 이름 검색 첫 결과
    def resolve_remote(self, query):
        q = str(query).strip()
        if q.isdigit():
            return int(q)
        hits = self.client.search(q)
        if not hits:
            raise SteamError(f"'{q}' 검색 결과가 없어요.")
        if len(hits) > 1:
            others = ", ".join(f"{h['name']}({h['appid']})" for h in hits[1:4])
            self.say(f"'{hits[0]['name']}'(으)로 골랐어요. 다른 후보: {others}")
        return hits[0]["appid"]

    # 저장된 게임 찾기
    def resolve_local(self, query):
        found = self.store.find(query)
        if not found:
            raise SteamError(f"기록된 게임 중 '{query}'이(가) 없어요. 먼저 buy로 추가하세요.")
        if len(found) > 1:
            names = ", ".join(f"{g['name']}({g['appid']})" for g in found)
            raise SteamError(f"여러 게임이 걸려요: {names}. appid로 지정해 주세요.")
        return found[0]

    # ----- 사기 전 -----
    def cmd_check(self, a):
        appid = self.resolve_remote(a.game)
        d = self.client.app_details(appid)
        rv = self.client.reviews(appid)
        rv_ko = self.client.reviews(appid, language="koreana")
        upd = self.client.last_update(appid) if d["early_access"] else None
        now = self.now()

        self.say(f"\n🎮 {d['name']}  (appid {appid})")
        if d["is_free"]:
            self.say("  가격      무료")
        elif d["price"]:
            sale = f"  ({d['discount']}% 할인, 정가 {core.fmt_won(d['initial_price'])})" if d["discount"] else ""
            self.say(f"  가격      {core.fmt_won(d['price'])}{sale}")
        else:
            self.say("  가격      정보 없음")
        self.say(f"  한국어    {d['korean']}")
        if rv["total"]:
            self.say(f"  리뷰      {rv['desc']}  긍정 {rv['ratio']:.0%} / {rv['total']:,}개")
        else:
            self.say("  리뷰      아직 없음")
        if rv_ko["total"]:
            self.say(f"  한국어 리뷰 긍정 {rv_ko['ratio']:.0%} / {rv_ko['total']:,}개")
        self.say(f"  출시      {d['release_date'] or '?'}{' (출시 예정)' if d['coming_soon'] else ''}")

        risk = core.early_access_risk(d, rv, upd, now)
        if risk:
            self.say(f"  ⚠ 얼리액세스 방치 위험: {risk['score']}점 ({risk['level']})")
            for r in risk["reasons"]:
                self.say(f"     - {r}")

        ce = self.compat.get(appid)
        if ce:
            for label, val in compat.summary_lines(ce):
                self.say(f"  {_pad(label, 9)} {val}")
        else:
            self.say("  스팀덱    정보 없음 (compat set 으로 직접 기록할 수 있어요)")

        owned = self.store.get(appid)
        if owned:
            self.say(f"  📌 이미 기록된 게임이에요 ({owned['purchased_at'][:10]} 구매).")

        warn = []
        if d["korean"] == "없음":
            warn.append("한국어 미지원")
        if rv["ratio"] is not None and rv["ratio"] < 0.7:
            warn.append("평가가 갈림")
        if risk and risk["level"] == "높음":
            warn.append("방치 위험 높음")
        if ce and ce.get("deck") == "unsupported":
            warn.append("스팀덱 미지원")
        if ce and (ce.get("anticheat") or {}).get("linux") == "no":
            warn.append("안티치트가 리눅스 미지원")
        if d["discount"] == 0 and d["price"]:
            warn.append("할인 중 아님 (급하지 않으면 세일 대기)")
        verdict = "괜찮아 보여요. 사면 2시간 안에 판단해 보세요." if not warn else "고민 포인트: " + ", ".join(warn)
        self.say(f"  → {verdict}\n")
        return 0

    # ----- 살 때 / 산 뒤 -----
    def cmd_buy(self, a):
        appid = self.resolve_remote(a.game)
        now = self.now()
        try:
            d = self.client.app_details(appid)
            name, price = d["name"], d["price"]
        except SteamError:
            if a.price is None or not a.name:
                raise
            name, price = a.name, 0
        if a.name:
            name = a.name
        game = self.store.get(appid) or {}
        game.update({
            "appid": appid,
            "name": name,
            "purchased_at": parse_when(a.date, now).isoformat(),
            "price_paid": a.price if a.price is not None else price,
            "minutes": game.get("minutes", 0),
            "source": "직접입력",
        })
        self.store.put(game)
        self.store.save()
        st = core.refund_status(game, now)
        self.say(f"기록했어요: {name} {core.fmt_won(game['price_paid'])}")
        self.say(f"환불 마감 {st['deadline']:%m/%d %H:%M} (남은 시간 {core.fmt_timedelta(st['time_left'])}), "
                 f"플레이 {core.fmt_minutes(st['minutes_left'])} 남음")
        return 0

    def cmd_play(self, a):
        g = self.resolve_local(a.game)
        mins = parse_duration(a.time)
        g["minutes"] = g.get("minutes", 0) + mins if a.add else mins
        self.store.save()
        st = core.refund_status(g, self.now())
        self.say(f"{g['name']}: 총 {core.fmt_minutes(g['minutes'])} 플레이")
        if st["refundable"]:
            self.say(f"환불 가능 플레이 시간 {core.fmt_minutes(st['minutes_left'])} 남음")
        else:
            self.say("이제 환불 조건(2시간 미만)을 넘었어요.")
        return 0

    def cmd_keep(self, a):
        g = self.resolve_local(a.game)
        g["kept"] = True
        self.store.save()
        self.say(f"{g['name']}: 계속 갖기로 했어요. 환불 알림을 끕니다.")
        return 0

    def cmd_refunded(self, a):
        g = self.resolve_local(a.game)
        g["refunded"] = True
        g["kept"] = True
        self.store.save()
        self.say(f"{g['name']}: 환불 처리로 표시했어요. 돌아보기 통계에서 빠져요.")
        return 0

    def cmd_remove(self, a):
        g = self.resolve_local(a.game)
        self.store.remove(g["appid"])
        self.store.save()
        self.say(f"{g['name']} 기록을 지웠어요.")
        return 0

    def _active(self):
        return [g for g in self.store.games.values() if not g.get("refunded")]

    def cmd_status(self, a):
        now = self.now()
        rows = []
        for g in sorted(self._active(), key=lambda g: g["purchased_at"], reverse=True):
            st = core.refund_status(g, now)
            if st["state"] in ("기한지남", "보유확정") and not a.all:
                continue
            mark = {"급함": "🔴", "여유": "🟢"}.get(st["state"], "⚪")
            rows.append([
                f"{mark} {st['state']}", g["name"], core.fmt_timedelta(st["time_left"]),
                f"{core.fmt_minutes(st['minutes'])} / 2시간", f"{st['deadline']:%m/%d %H:%M}",
            ])
        if not rows:
            self.say("환불 기한이 남은 게임이 없어요." + ("" if a.all else " (전체는 --all)"))
            return 0
        table(["상태", "게임", "남은 기간", "플레이", "마감"], rows, self.out)
        return 0

    # ----- 플레이 시간 동기화 -----
    def sync(self):
        cfg = self.store.config
        if not (cfg.get("key") and cfg.get("steamid")):
            return None
        owned = self.client.owned_games(cfg["key"], cfg["steamid"])
        known = set(cfg.get("known_appids", []))
        first_sync = not known
        updated, added = 0, []
        now = self.now().replace(microsecond=0)
        for o in owned:
            g = self.store.get(o["appid"])
            if g:
                if g.get("minutes", 0) != o["minutes"]:
                    g["minutes"] = o["minutes"]
                    updated += 1
            elif not first_sync and o["appid"] not in known:
                # 지난 동기화 이후 새로 생긴 게임 = 방금 산 게임으로 본다
                g = {
                    "appid": o["appid"], "name": o["name"], "purchased_at": now.isoformat(),
                    "price_paid": 0, "minutes": o["minutes"], "source": "자동감지",
                }
                try:
                    g["price_paid"] = self.client.app_details(o["appid"])["price"]
                except SteamError:
                    pass
                self.store.put(g)
                added.append(g["name"])
        cfg["known_appids"] = sorted(known | {o["appid"] for o in owned})
        self.store.save()
        return {"owned": len(owned), "updated": updated, "added": added, "first": first_sync}

    def cmd_sync(self, a):
        r = self.sync()
        if r is None:
            self.say("먼저 `config --key <Steam Web API 키> --steamid <SteamID64>`로 설정해 주세요.")
            return 1
        self.say(f"보유 게임 {r['owned']}개 확인, 플레이 시간 {r['updated']}개 갱신.")
        if r["first"]:
            self.say("첫 동기화라 지금 가진 게임을 기준으로 저장했어요. 이후 새로 생기는 게임은 자동으로 기록돼요.")
        for n in r["added"]:
            self.say(f"  + 새 게임 감지: {n} (오늘 구매로 기록, 날짜가 다르면 buy --date로 고치세요)")
        return 0

    # ----- 알림 (작업 스케줄러에서 하루 몇 번 돌리는 용도) -----
    def cmd_alert(self, a):
        try:
            self.sync()
        except SteamError as e:
            self.say(f"(동기화 건너뜀: {e})")
        now = self.now()
        urgent = []
        for g in self._active():
            st = core.refund_status(g, now)
            if st["state"] == "급함":
                urgent.append(f"{g['name']}: 마감까지 {core.fmt_timedelta(st['time_left'])}, "
                              f"플레이 {core.fmt_minutes(st['minutes_left'])} 남음")
        if not urgent:
            self.say("급한 환불 기한 없음.")
            return 0
        msg = "\n".join(urgent)
        self.say("⏰ 환불 기한 임박\n" + msg)
        topic = a.ntfy or self.store.config.get("ntfy")
        if topic:
            try:
                self.push(topic, "스팀 환불 기한 임박", msg + "\n\n계속 할 거면 keep, 아니면 지금 환불 신청하세요.")
                self.say(f"(ntfy '{topic}'로 보냈어요)")
            except Exception as e:
                self.say(f"(알림 전송 실패: {e})")
                return 1
        return 0

    # ----- 돌아보기 -----
    def cmd_rate(self, a):
        g = self.resolve_local(a.game)
        if not 1 <= a.score <= 5:
            raise SteamError("만족도는 1~5로 주세요.")
        g["rating"] = a.score
        if a.note:
            g["note"] = a.note
        self.store.save()
        self.say(f"{g['name']}: 만족도 {'★' * a.score}{'☆' * (5 - a.score)}")
        return 0

    def cmd_report(self, a):
        rows_data = [core.value_row(g) for g in self._active()]
        if not rows_data:
            self.say("기록된 게임이 없어요.")
            return 0
        rows_data.sort(key=lambda r: (r["per_hour"] is None, r["per_hour"] or 0))
        rows = []
        for r in rows_data:
            rows.append([
                r["name"], core.fmt_won(r["price"]), f"{r['hours']:.1f}h",
                core.fmt_won(r["per_hour"]) + "/h" if r["per_hour"] is not None else "-",
                ("★" * r["rating"]) if r["rating"] else "-", r["note"],
            ])
        table(["게임", "낸 돈", "플레이", "시간당", "만족", "메모"], rows, self.out)
        total = sum(r["price"] for r in rows_data)
        hours = sum(r["hours"] for r in rows_data)
        unplayed = [r["name"] for r in rows_data if r["hours"] < 0.1]
        self.say(f"\n합계 {core.fmt_won(total)}, {hours:.1f}시간"
                 + (f", 시간당 {core.fmt_won(total / hours)}" if hours >= 0.1 else ""))
        if unplayed:
            self.say(f"아직 안 켜본 게임 {len(unplayed)}개: {', '.join(unplayed)}")
        regret = [r["name"] for r in rows_data if r["rating"] and r["rating"] <= 2]
        if regret:
            self.say(f"후회 목록(만족 2 이하): {', '.join(regret)}")
        return 0

    # ----- 번들 실가치 -----
    def cmd_bundle(self, a):
        appids = [self.resolve_remote(g) for g in a.games]
        price = a.price
        if a.bundle_id:
            # 실험적(best-effort): 응답 모양이 다르면 읽지 못할 수 있어요
            try:
                b = self.client.bundle_details(a.bundle_id)
            except SteamError as e:
                if not appids:
                    raise
                self.say(f"(번들 정보 조회 실패, 직접 적은 게임만 계산해요: {e})")
            else:
                self.say(f"번들: {b['name']}")
                appids += [x for x in b["appids"] if x not in appids]
                if price is None:
                    price = b["price"]
        if not appids:
            raise SteamError("계산할 게임이 없어요. 게임을 적거나 --bundle-id를 주세요.")
        if price is None:
            raise SteamError("번들 가격을 --price로 알려 주세요.")
        known = set(self.store.config.get("known_appids", []))
        skip = set(a.skip or [])
        items, rows = [], []
        for appid in appids:
            d = self.client.app_details(appid)
            owned = bool(self.store.get(appid)) or appid in known
            skipped = appid in skip
            item = {"price": 0 if d["is_free"] else d["price"], "owned": owned, "skipped": skipped}
            items.append(item)
            state = "관심없음(제외)" if skipped else "보유" if owned else "새 게임"
            rows.append([d["name"], core.fmt_won(item["price"]) if item["price"] else "무료/정보없음",
                         state, d["korean"]])
        self.say("")
        table(["게임", "현재가", "상태", "한국어"], rows, self.out)
        r = core.bundle_value(items, price)
        self.say(f"\n개별 구매 합계(제외한 것 빼고) {core.fmt_won(r['full_sum'])}")
        self.say(f"실제 가치(새 게임 {r['new_count']}개) {core.fmt_won(r['real'])}  vs  번들 {core.fmt_won(price)}")
        if r["per_game"] is not None:
            self.say(f"새 게임 1개당 실질 {core.fmt_won(r['per_game'])}")
        self.say(f"→ {r['verdict']}\n")
        return 0

    # ----- 숨은 명작 탐색 -----
    def cmd_gems(self, a):
        cands, notes = [], []

        def add(ids):
            for i in ids:
                if i not in cands:
                    cands.append(i)

        if a.appids:
            add(int(x) for x in a.appids.split(",") if x.strip().isdigit())
        if a.from_search:
            add(h["appid"] for h in self.client.search(a.from_search))
        if a.wishlist:
            sid = self.store.config.get("steamid")
            if not sid:
                notes.append("위시리스트: 먼저 `config --steamid`로 SteamID64를 설정해 주세요.")
            else:
                try:
                    add(self.client.wishlist(sid))
                except SteamError as e:
                    notes.append(f"위시리스트: {e}")
        for n in notes:
            self.say(f"({n})")
        if not cands:
            raise SteamError("후보가 없어요. --from-search, --appids, --wishlist 중 하나를 주세요.")
        if len(cands) > a.limit:
            self.say(f"(후보 {len(cands)}개 중 앞의 {a.limit}개만 확인해요. 더 보려면 --limit)")
            cands = cands[:a.limit]

        tag = (a.tag or "").lower()
        found, calls = [], 0
        for appid in cands:
            if calls:
                self._sleep(self.delay)
            calls += 1
            try:
                rv = self.client.reviews(appid)
            except SteamError:
                continue
            if rv["ratio"] is None or not a.min_reviews <= rv["total"] <= a.max_reviews or rv["ratio"] < a.min_ratio:
                continue
            self._sleep(self.delay)
            try:
                d = self.client.app_details(appid)
            except SteamError:
                continue
            if tag and not any(tag in t.lower() for t in d["genres"] + [d["name"]]):
                continue
            if a.korean and d["korean"] == "없음":
                continue
            found.append((core.gem_score(rv["positive"], rv["total"]), appid, d, rv))
        if not found:
            self.say(f"조건에 맞는 게임이 없어요. ({len(cands)}개 확인)")
            return 0
        found.sort(key=lambda t: -t[0])
        rows = []
        for sc, appid, d, rv in found:
            price = "무료" if d["is_free"] else core.fmt_won(d["price"]) if d["price"] else "-"
            rows.append([d["name"], str(appid), f"{rv['ratio']:.0%}", f"{rv['total']:,}",
                         f"{core.wilson_lower_bound(rv['positive'], rv['total']):.0%}", f"{sc * 100:.1f}",
                         price, d["korean"]])
        self.say("")
        table(["게임", "appid", "긍정", "리뷰수", "윌슨하한", "점수", "가격", "한국어"], rows, self.out)
        self.say(f"\n{len(cands)}개 확인, {len(found)}개가 조건에 맞아요. (점수 = 윌슨하한 70% + 긍정비율 30%)")
        return 0

    # ----- 호환 정보 (스팀덱 / 안티치트 / 핸드헬드 프리셋) -----
    def _compat_appid(self, query):
        q = str(query).strip()
        if q.isdigit() and int(q) > 0:
            return int(q)
        return self.resolve_local(q)["appid"]

    def _compat_name(self, appid, e=None):
        g = self.store.get(appid)
        return (e or {}).get("name") or (g["name"] if g else "")

    def cmd_compat(self, a):
        if a.action == "set":
            return self._compat_set(a)
        return self._compat_show(a)

    def _compat_set(self, a):
        appid = self._compat_appid(a.game)
        ac = {k: v for k, v in (("kind", a.anticheat), ("linux", a.ac_linux), ("deck", a.ac_deck)) if v is not None}
        pr = {k: v for k, v in (("tdp_w", a.tdp), ("fps_cap", a.fps), ("resolution", a.resolution),
                                ("fsr", a.fsr), ("graphics_notes", a.gfx)) if v is not None}
        if not (ac or pr or a.deck or a.note is not None or a.name):
            raise ValueError("바꿀 항목이 없어요. --deck, --anticheat, --tdp 등을 지정하세요.")
        self.compat.update(appid, name=a.name, deck=a.deck, note=a.note, anticheat=ac or None, preset=pr or None)
        self.say(f"{appid} 호환 정보를 저장했어요. ({self.compat.user_path})")
        return 0

    def _compat_show(self, a):
        if a.game:
            appid = self._compat_appid(a.game)
            e = self.compat.get(appid)
            if not e:
                self.say(f"{appid}: 기록된 호환 정보가 없어요. compat set 으로 추가하세요.")
                return 0
            self.say(f"\n{self._compat_name(appid, e) or appid}  (appid {appid})")
            for label, val in compat.summary_lines(e):
                self.say(f"  {_pad(label, 9)} {val}")
            return 0
        ids = self.compat.all_ids()
        if not ids:
            self.say("기록된 호환 정보가 없어요.")
            return 0
        rows = []
        for k in ids:
            e = self.compat.get(k)
            ac = e.get("anticheat")
            deck = compat.DECK_LABELS.get(e.get("deck"), "-") + ("*" if e.get("example") else "")
            rows.append((k, self._compat_name(int(k), e) or "-", deck,
                         (ac.get("kind", "?") + "/리눅스 " + compat.SUPPORT_LABELS[ac.get("linux", "unknown")]) if ac else "-",
                         "있음" if e.get("preset") else "-"))
        table(("appid", "이름", "스팀덱", "안티치트", "프리셋"), rows, self.out)
        if any(self.compat.get(k).get("example") for k in ids):
            self.say("* 표시는 예시 데이터예요. 실제 호환 정보가 아니니 직접 확인해서 compat set 으로 덮어쓰세요.")
        return 0

    def cmd_preset(self, a):
        appid = self._compat_appid(a.game)
        if a.action == "export":
            e = self.compat.get(appid)
            if not e or not e.get("preset"):
                raise ValueError(f"{appid}에 저장된 프리셋이 없어요. compat set --tdp ... 로 먼저 만드세요.")
            text = json.dumps(compat.export_preset(appid, self._compat_name(appid, e), e["preset"]),
                              ensure_ascii=False, indent=2)
            if a.out:
                Path(a.out).write_text(text + "\n", encoding="utf-8")
                self.say(f"프리셋을 {a.out}에 저장했어요.")
            else:
                self.say(text)
            return 0
        try:
            text = sys.stdin.read() if a.file == "-" else Path(a.file).read_text(encoding="utf-8")
        except OSError as ex:
            raise ValueError(f"파일을 읽을 수 없어요: {ex}")
        doc_appid, _name, preset = compat.parse_preset_doc(text)
        if doc_appid != appid and not a.force:
            raise ValueError(f"파일의 appid({doc_appid})가 지정한 appid({appid})와 달라요. 그래도 가져오려면 --force")
        self.compat.set_preset(appid, preset)
        self.say(f"{appid} 프리셋을 가져왔어요. ({self.compat.user_path})")
        return 0

    def cmd_config(self, a):
        cfg = self.store.config
        changed = False
        for k in ("key", "steamid", "ntfy"):
            v = getattr(a, k)
            if v is not None:
                cfg[k] = v
                changed = True
        if changed:
            self.store.save()
        masked = (cfg.get("key", "")[:4] + "…") if cfg.get("key") else "(없음)"
        self.say(f"Web API 키: {masked}")
        self.say(f"SteamID64: {cfg.get('steamid', '(없음)')}")
        self.say(f"ntfy 주제: {cfg.get('ntfy', '(없음)')}")
        self.say(f"데이터 파일: {self.store.path}")
        return 0


def build_parser():
    p = argparse.ArgumentParser(prog="steam-buddy", description="스팀 구매 결정 비서")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("check", help="사기 전 체크: 가격, 한국어, 리뷰, 얼리액세스 방치 위험")
    s.add_argument("game", help="게임 이름 또는 appid")

    s = sub.add_parser("buy", help="구매 기록 (환불 기한 추적 시작)")
    s.add_argument("game")
    s.add_argument("--date", help="구매 시각, 예: 2026-11-01 또는 2026-11-01T21:30 (기본: 지금)")
    s.add_argument("--price", type=int, help="실제 낸 돈 (기본: 현재 스토어 가격)")
    s.add_argument("--name", help="표시 이름 바꾸기")

    s = sub.add_parser("play", help="플레이 시간 입력 (API 키 없을 때)")
    s.add_argument("game")
    s.add_argument("time", help="총 플레이 시간, 예: 1h30m, 90m, 1.5")
    s.add_argument("--add", action="store_true", help="총합 대신 더하기")

    for name, h in (("keep", "계속 갖기로 결정 (알림 끄기)"), ("refunded", "환불했다고 표시"), ("remove", "기록 지우기")):
        s = sub.add_parser(name, help=h)
        s.add_argument("game")

    s = sub.add_parser("status", help="환불 기한 현황")
    s.add_argument("--all", action="store_true", help="기한 지난 게임도 표시")

    sub.add_parser("sync", help="Steam에서 플레이 시간 가져오기 (+ 새로 산 게임 자동 감지)")

    s = sub.add_parser("alert", help="임박한 환불 기한 알림 (스케줄러용)")
    s.add_argument("--ntfy", help="ntfy 주제 (config에 저장한 값보다 우선)")

    s = sub.add_parser("rate", help="만족도 기록 1~5")
    s.add_argument("game")
    s.add_argument("score", type=int)
    s.add_argument("--note", help="한 줄 메모")

    sub.add_parser("report", help="돌아보기: 시간당 비용, 안 켜본 게임, 후회 목록")

    s = sub.add_parser("bundle", help="번들 실가치 계산 (이미 가진 게임 제외)")
    s.add_argument("games", nargs="*", help="번들에 든 게임 이름 또는 appid")
    s.add_argument("--price", type=int, help="번들 가격(원). --bundle-id가 가격을 주면 생략 가능")
    s.add_argument("--bundle-id", type=int, help="(실험적) 스토어 번들 ID로 구성 불러오기")
    s.add_argument("--skip", type=int, action="append", help="관심 없는 게임 appid (여러 번 가능)")

    s = sub.add_parser("gems", help="숨은 명작 탐색: 긍정 비율은 높고 리뷰 수는 적은 게임")
    s.add_argument("--from-search", help="검색어로 후보 모으기")
    s.add_argument("--appids", help="쉼표로 구분한 appid 목록, 예: 1,2,3")
    s.add_argument("--wishlist", action="store_true", help="(실험적) 공개 위시리스트에서 후보 모으기")
    s.add_argument("--tag", help="장르/이름에 이 키워드가 든 것만")
    s.add_argument("--min-reviews", type=int, default=50)
    s.add_argument("--max-reviews", type=int, default=2000)
    s.add_argument("--min-ratio", type=float, default=0.9)
    s.add_argument("--korean", action="store_true", help="한국어 지원 게임만")
    s.add_argument("--limit", type=int, default=30, help="확인할 후보 수 상한 (기본 30)")

    s = sub.add_parser("compat", help="스팀덱 호환 등급/안티치트/핸드헬드 메모 기록·보기")
    cs = s.add_subparsers(dest="action", required=True)
    t = cs.add_parser("set", help="호환 정보 기록 (사용자 compat.json)")
    t.add_argument("game", help="appid 또는 기록된 게임 이름")
    t.add_argument("--name")
    t.add_argument("--deck", choices=list(compat.DECK_LABELS), help="스팀덱 등급")
    t.add_argument("--anticheat", help="안티치트 종류 (예: EasyAntiCheat, BattlEye)")
    t.add_argument("--ac-linux", choices=list(compat.SUPPORT_LABELS), help="안티치트 리눅스 지원")
    t.add_argument("--ac-deck", choices=list(compat.SUPPORT_LABELS), help="안티치트 스팀덱 지원")
    t.add_argument("--note", help="호환 메모")
    t.add_argument("--tdp", type=int, help="TDP(W)")
    t.add_argument("--fps", type=int, help="FPS 제한")
    t.add_argument("--resolution", help="해상도, 예: 1280x800")
    t.add_argument("--fsr", choices=list(compat.FSR_VALUES))
    t.add_argument("--gfx", help="그래픽 옵션 메모")
    t = cs.add_parser("show", help="호환 정보 보기 (게임 생략 시 전체 표)")
    t.add_argument("game", nargs="?")

    s = sub.add_parser("preset", help="핸드헬드 프리셋 JSON 내보내기/가져오기")
    ps = s.add_subparsers(dest="action", required=True)
    t = ps.add_parser("export", help="프리셋을 JSON으로 출력")
    t.add_argument("game")
    t.add_argument("--out", help="파일로 저장")
    t = ps.add_parser("import", help="JSON 프리셋 가져오기 (스키마 검사)")
    t.add_argument("game")
    t.add_argument("file", help="프리셋 JSON 파일 ('-'이면 표준입력)")
    t.add_argument("--force", action="store_true", help="파일 안의 appid가 달라도 가져오기")

    s = sub.add_parser("config", help="설정 보기/바꾸기")
    s.add_argument("--key", help="Steam Web API 키 (https://steamcommunity.com/dev/apikey)")
    s.add_argument("--steamid", help="SteamID64 (17자리 숫자)")
    s.add_argument("--ntfy", help="ntfy 알림 주제 이름")
    return p


def main(argv=None, app=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # 윈도우 콘솔 한글 깨짐 방지
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    app = app or App()
    try:
        return getattr(app, "cmd_" + args.cmd)(args)
    except (SteamError, ValueError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
