"""tailscale serve 명령 안내 (출력만 한다. 직접 실행하지 않는다)."""
from __future__ import annotations

from .registry import Ctx


def tailscale_lines(ctx: Ctx, *, show_token: bool = False) -> list[str]:
    port = ctx.port("phone-home")
    host = ctx.settings.get("tailnet_host") or "<pc이름>.<tailnet>.ts.net"
    token = ctx.token("phone-home") if show_token and ctx.token("phone-home") else "<phone-home 토큰>"
    lines = [
        "# phone-home 하나만 tailnet 안에서 HTTPS 로 엽니다 (tailnet 구성원만 접근, 인터넷 공개 아님)",
        "tailscale serve --bg %d" % port,
        "",
        "# 확인 / 끄기",
        "tailscale serve status",
        "tailscale serve reset",
        "",
        "# 폰(S21)에서 처음 한 번만 이 주소로 엽니다. 토큰은 쿠키로 바뀌고 주소창에서 지워져요.",
        "https://%s/#token=%s" % (host, token),
        "",
        "주의",
        "  - `tailscale funnel` 은 쓰지 마세요. Funnel 은 인터넷 전체에 공개합니다. 홈랩의 어떤 서비스도 Funnel 대상이 아니에요.",
        "  - 확인: `tailscale funnel status` 가 \"No serve config\" 이거나 비어 있어야 해요.",
        "  - phone-home 이 토큰 하나로 나머지(사이드킥, 물건 기억, 일기, 소리 감시, 자비스)를 프록시해요.",
        "    그래서 다른 포트(%s)는 serve 하지 않습니다." % ", ".join(
            str(ctx.port(n)) for n in ("sidekick", "voice-journal", "object-memory", "sound-watch", "jarvis-hub")),
        "  - 사이드킥의 마이크/카메라는 HTTPS 에서만 돼요. phone-home 주소로 열면 이미 HTTPS 입니다.",
        "  - `tailscale serve` 는 tailnet 관리 화면에서 MagicDNS 와 HTTPS 인증서가 켜져 있어야 해요.",
    ]
    if not show_token:
        lines.append("  - 토큰을 보려면: homelab token show phone-home   (또는 --show-token)")
    if ctx.is_enabled("shorts-vote"):
        lines.append("  - 썸네일 투표(shorts-vote, %d)를 바깥 사람에게 보여 줄 때만 별도로 판단하세요. "
                     "기본은 tailnet 안에서만이고 Funnel 은 그 프로젝트 README 의 주의를 읽은 뒤에만." % ctx.port("shorts-vote"))
    return lines
