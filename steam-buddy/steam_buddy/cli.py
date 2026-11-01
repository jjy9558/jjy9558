"""명령줄 인터페이스. `python -m steam_buddy <명령>` 으로 실행한다."""

import argparse
import re
import sys
import unicodedata
from datetime import datetime, timedelta

from . import core
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
    def __init__(self, store=None, client=None, now=None, out=None, push=send_ntfy):
        self.store = store or Store()
        self.client = client or SteamClient()
        self._now = now
        self.out = out or sys.stdout
        self.push = push

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
