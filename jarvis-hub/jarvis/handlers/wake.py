"""wake: 메인 PC에 Wake-on-LAN 매직 패킷. 메인 PC와 같은 LAN에 있는 미니PC 워커가 맡는다.

payload: {"mac": "AA:BB:CC:DD:EE:FF", "broadcast": "192.168.0.255", "port": 9, "label": "5080"}
(허브가 hub.json의 wake 설정으로 채운다. Tailscale은 L2 브로드캐스트를 넘기지 못하므로 같은 LAN 필수.)
"""
from .. import wol
from .base import Handler, HandlerError, PermanentError


class WakeHandler(Handler):
    type = "wake"

    def __init__(self, cfg, sock_factory=None, **kw):
        super().__init__(cfg, **kw)
        self.sock_factory = sock_factory

    def run(self, job):
        p = job.get("payload") or {}
        try:
            mac = wol.parse_mac(p.get("mac"))
        except ValueError as e:
            raise PermanentError(str(e))
        try:
            sent = wol.send_magic_packet(mac, p.get("broadcast") or "255.255.255.255", p.get("port") or 9,
                                         repeat=self.cfg.get("repeat", 3), sock_factory=self.sock_factory)
        except ValueError as e:
            raise PermanentError(str(e))
        except OSError as e:
            raise HandlerError("매직 패킷 전송 실패: %s" % e)
        label = p.get("label") or "PC"
        return {"sent_bytes": sent, "summary": "%s에 켜기 신호를 보냈어요" % label}
