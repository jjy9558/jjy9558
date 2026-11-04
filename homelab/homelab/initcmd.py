"""``homelab init``: 질문(선택) → 설정 확정 → 토큰 생성 → 프로젝트별 설정 파일 쓰기.

모든 질문에는 같은 이름의 플래그가 있어서 --yes 로 묻지 않고 돌릴 수 있다.
이미 있는 프로젝트 설정은 --force 없이는 건드리지 않고, --dry-run 은 아무것도 쓰지 않고 diff 만 보여 준다.
"""
from __future__ import annotations

import sys
from typing import Callable, TextIO

from . import configgen, paths, secretstore
from .registry import SERVICES, Ctx, default_enabled

STATUS_TEXT = {
    "created": "만들었어요",
    "same": "이미 같은 내용이에요",
    "exists": "이미 있어서 그대로 뒀어요 (바꾸려면 --force)",
    "overwritten": "덮어썼어요",
    "would-create": "[dry-run] 새로 만들 예정",
    "would-overwrite": "[dry-run] 덮어쓸 예정 (백업 후)",
    "would-skip": "[dry-run] 이미 있어서 건너뜀 (--force 면 덮어씀)",
}


class InitError(ValueError):
    pass


def _csv(text: str | None) -> list[str]:
    return [x.strip() for x in (text or "").replace("，", ",").split(",") if x.strip()]


class Prompter:
    def __init__(self, interactive: bool, ask: Callable[[str], str], out: TextIO):
        self.interactive = interactive
        self.ask = ask
        self.out = out

    def text(self, flag, question: str, default: str) -> str:
        if flag is not None:
            return str(flag)
        if not self.interactive:
            return default
        ans = self.ask("%s [%s] " % (question, default) if default else "%s (비우면 없음) " % question).strip()
        return ans if ans else default

    def yesno(self, flag, question: str, default: bool) -> bool:
        if flag is not None:
            return bool(flag)
        if not self.interactive:
            return default
        ans = self.ask("%s [%s] " % (question, "Y/n" if default else "y/N")).strip().lower()
        if not ans:
            return default
        return ans in ("y", "yes", "예", "ㅇ", "네")


def collect_settings(args, base: dict, pr: Prompter) -> dict:
    s = dict(base)
    s["python"] = pr.text(args.python, "서버를 켤 python 경로", s.get("python") or sys.executable)
    s["repo_root"] = pr.text(args.repo_root, "프로젝트들이 들어 있는 저장소 폴더",
                             s.get("repo_root") or str(paths.default_repo_root()))
    s["llm_url"] = pr.text(args.llm_url, "로컬 LLM 주소(OpenAI 호환, /v1까지)", s.get("llm_url") or "")
    enabled = list(s.get("enabled") or default_enabled())
    if args.enable is None and pr.interactive:
        ans = pr.text(None, "켤 서비스 목록(쉼표로, 가능: %s)" % ", ".join(SERVICES), ",".join(enabled))
        enabled = _csv(ans)
    for n in _csv(args.enable):
        if n not in enabled:
            enabled.append(n)
    enabled = [n for n in enabled if n not in set(_csv(args.disable))]
    s["hub_host"] = pr.text(args.hub_host, "자비스 허브가 열릴 주소(미니PC면 Tailscale IP)", s.get("hub_host") or "127.0.0.1")
    s["hub_url"] = pr.text(args.hub_url, "워커가 붙을 허브 주소(비우면 위 주소로 계산)", s.get("hub_url") or "")
    s["wol_mac"] = pr.text(args.wol_mac, "5080 PC 의 MAC 주소(Wake-on-LAN, 비우면 안 씀)", s.get("wol_mac") or "")
    if s["wol_mac"]:
        s["wol_broadcast"] = pr.text(args.wol_broadcast, "WOL 브로드캐스트 주소", s.get("wol_broadcast") or "255.255.255.255")
    elif args.wol_broadcast is not None:
        s["wol_broadcast"] = args.wol_broadcast
    s["minipc"] = pr.yesno(args.minipc, "미니PC 용 워커 설정 파일도 만들까요?", bool(s.get("minipc")))
    s["ntfy"] = pr.yesno(args.ntfy, "ntfy 푸시를 켤까요? (무작위 토픽을 만들어 줘요)", bool(s.get("ntfy")))
    s["wallet_limit_usd"] = pr.text(args.wallet_limit, "Claude 사용량 일일 한도(USD, 0=알림 없음)",
                                    str(s.get("wallet_limit_usd", 20)))
    words = pr.text(args.redact_words, "TIL 초안에서 추가로 가릴 단어(쉼표)", ",".join(s.get("redact_words") or []))
    s["redact_words"] = _csv(words)
    s["gm_world"] = pr.text(args.gm_world, "gm-trpg 로 켤 세계 이름(비우면 안 씀)", s.get("gm_world") or "")
    appids = pr.text(args.appids, "game-calendar 가 추적할 Steam appid(쉼표)", ",".join(str(a) for a in s.get("appids") or []))
    s["appids"] = _csv(appids)
    s["morning_gate"] = pr.yesno(args.morning_gate, "알람 승인 뒤에 아침 브리핑을 읽어 줄까요?", bool(s.get("morning_gate")))
    s["tailnet_host"] = pr.text(args.tailnet_host, "tailscale 안내문에 넣을 이 PC 호스트 이름(선택)", s.get("tailnet_host") or "")
    if s["gm_world"] and "gm-trpg" not in enabled and "gm-trpg" not in _csv(args.disable):
        enabled.append("gm-trpg")
    s["enabled"] = [n for n in SERVICES if n in enabled]
    unknown = [n for n in enabled if n not in SERVICES]
    if unknown:
        raise InitError("알 수 없는 서비스: %s (가능: %s)" % (", ".join(unknown), ", ".join(SERVICES)))
    return paths.validate_settings(s, list(SERVICES))


def run_init(layout: paths.Layout, args, out: TextIO, *, ask: Callable[[str], str] = input,
             is_tty: bool | None = None) -> int:
    interactive = (sys.stdin.isatty() if is_tty is None else is_tty) and not args.yes
    pr = Prompter(interactive, ask, out)
    if args.rotate_tokens and not args.force:
        print("--rotate-tokens 는 --force 와 함께 써야 해요 (토큰만 바꾸면 기존 설정 파일과 어긋나요).", file=out)
        return 2
    try:
        base = paths.load_settings(layout)
        settings = collect_settings(args, base, pr)
        secrets = secretstore.load(layout.secrets_path)
    except (paths.SettingsError, secretstore.SecretsError, InitError) as e:
        print("❌ %s" % e, file=out)
        return 2
    if not settings.get("python"):
        settings["python"] = sys.executable
    ctx = Ctx(layout, settings, secrets)
    tok_names, extra_names = configgen.required_tokens(ctx)
    changed = secretstore.ensure(secrets, tok_names, extra_names, rotate=args.rotate_tokens)
    dry = args.dry_run

    print("홈랩 폴더: %s%s" % (layout.root, "   [dry-run: 아무것도 쓰지 않아요]" if dry else ""), file=out)
    print("켤 서비스: %s" % ", ".join(ctx.enabled_services()), file=out)

    # 홈랩 자신의 설정
    new_settings_text = paths.settings_json(settings)
    old_settings_text = layout.settings_path.read_text(encoding="utf-8") if layout.settings_path.exists() else ""
    if not dry:
        if new_settings_text != old_settings_text:
            secretstore.write_private(layout.settings_path, new_settings_text)
            print("  - homelab.json: %s" % ("저장했어요" if old_settings_text else "만들었어요"), file=out)
        if changed:
            secretstore.save(layout.secrets_path, secrets)
        elif not layout.secrets_path.exists():
            secretstore.save(layout.secrets_path, secrets)
        if changed:
            print("  - secrets.json: 토큰 %d개를 새로 만들었어요 (%s)" % (len(changed), layout.secrets_path), file=out)
        else:
            print("  - secrets.json: 있던 토큰을 그대로 써요", file=out)
    else:
        print("  - homelab.json: %s" % ("바뀔 예정" if new_settings_text != old_settings_text else "변화 없음"), file=out)
        print("  - secrets.json: 새로 만들 토큰 %d개 (있던 건 유지)" % len(changed), file=out)

    files = configgen.render_all(ctx)
    if not dry:
        configgen.ensure_data_dirs(ctx)
    results = configgen.apply(files, secrets, force=args.force, dry_run=dry)
    desc = {f.key: f.desc for f in files}
    print("", file=out)
    for r in results:
        print("  - %s: %s" % (desc.get(r.key, r.key), STATUS_TEXT[r.status]), file=out)
        print("      %s" % r.path, file=out)
        if r.backup:
            print("      백업: %s" % r.backup, file=out)
        if dry and r.diff and (r.status in ("would-create", "would-overwrite", "would-skip")):
            for ln in r.diff.splitlines():
                print("      | " + ln, file=out)
    skipped = [r for r in results if r.status in ("exists", "would-skip")]
    print("", file=out)
    if skipped and not dry:
        print("※ 이미 있는 설정 %d개는 그대로 뒀어요. 토큰이 다르면 `homelab doctor` 가 알려 줘요 "
              "(맞추려면 `homelab init --force`, 기존 파일은 .bak-시각 으로 백업돼요)." % len(skipped), file=out)
    if sys.platform == "win32":
        print("※ 토큰 모음은 사용자 프로필 안(%s)에 있어요. 다른 사람과 공유하거나 클라우드 동기화 폴더에 두지 마세요."
              % layout.secrets_path, file=out)
    else:
        print("※ secrets.json 은 권한 600 이에요. 공유하지 마세요.", file=out)
    if ctx.ntfy_on:
        print("※ ntfy 토픽: 폰 ntfy 앱에서 구독하세요 → homelab token show ntfy-topic", file=out)
    print("", file=out)
    print("다음 단계:", file=out)
    print("  1) homelab doctor           점검", file=out)
    print("  2) homelab up               서버 켜기", file=out)
    print("  3) homelab tailscale --print   폰에서 여는 법", file=out)
    print("  4) homelab schedule --print    매일 도는 작업 등록", file=out)
    return 0
