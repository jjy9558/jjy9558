"""Wake-on-LAN 매직 패킷: FF x6 + MAC x16 (102바이트), UDP 브로드캐스트."""
import re
import socket

_HEX12 = re.compile(r"^[0-9a-fA-F]{12}$")


def parse_mac(mac):
    """'AA:BB:CC:DD:EE:FF', 'AA-BB-..', 'AABB.CCDD.EEFF', 'AABBCCDDEEFF' -> 6바이트."""
    if not isinstance(mac, str):
        raise ValueError("MAC 주소는 문자열이어야 합니다")
    s = mac.strip()
    if re.match(r"^[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}$", s):
        hexs = re.sub(r"[:-]", "", s)
    elif re.match(r"^([0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}$", s):
        hexs = s.replace(".", "")
    elif _HEX12.match(s):
        hexs = s
    else:
        raise ValueError("MAC 주소 형식이 아닙니다: %r" % mac)
    return bytes.fromhex(hexs)


def magic_packet(mac):
    raw = mac if isinstance(mac, (bytes, bytearray)) else parse_mac(mac)
    if len(raw) != 6:
        raise ValueError("MAC은 6바이트여야 합니다")
    return b"\xff" * 6 + bytes(raw) * 16


def send_magic_packet(mac, broadcast="255.255.255.255", port=9, repeat=3, sock_factory=None):
    """패킷을 repeat번 보낸다. 보낸 바이트 수 합계를 반환."""
    pkt = magic_packet(mac)
    port = int(port)
    if not 0 < port < 65536:
        raise ValueError("포트 범위 오류: %s" % port)
    factory = sock_factory or (lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
    sent = 0
    sock = factory()
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(max(1, int(repeat))):
            sent += sock.sendto(pkt, (broadcast, port))
    finally:
        sock.close()
    return sent
