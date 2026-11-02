import socket
import unittest

import helpers  # noqa: F401
from jarvis import wol
from jarvis.handlers import PermanentError
from jarvis.handlers.wake import WakeHandler


class FakeSock:
    instances = []

    def __init__(self):
        self.sent, self.opts, self.closed = [], [], False
        FakeSock.instances.append(self)

    def setsockopt(self, *a):
        self.opts.append(a)

    def sendto(self, data, addr):
        self.sent.append((data, addr))
        return len(data)

    def close(self):
        self.closed = True


class WolTest(unittest.TestCase):
    def test_parse_formats(self):
        want = bytes([0xAA, 0xBB, 0xCC, 0x01, 0x02, 0x0F])
        for s in ("AA:BB:CC:01:02:0F", "aa-bb-cc-01-02-0f", "AABB.CC01.020F", "aabbcc01020f", " AA:BB:CC:01:02:0F "):
            self.assertEqual(wol.parse_mac(s), want, s)
        for bad in ("AA:BB:CC:01:02", "GG:BB:CC:01:02:0F", "AA:BB-CC:01:02:0F", "", None, "AA:BB:CC:01:02:0F:11"):
            with self.assertRaises(ValueError, msg=bad):
                wol.parse_mac(bad)

    def test_packet_bytes(self):
        pkt = wol.magic_packet("01:23:45:67:89:AB")
        self.assertEqual(len(pkt), 102)
        self.assertEqual(pkt[:6], b"\xff" * 6)
        mac = bytes.fromhex("0123456789ab")
        for i in range(16):
            self.assertEqual(pkt[6 + 6 * i: 12 + 6 * i], mac)
        self.assertEqual(pkt.hex()[:24], "ffffffffffff0123456789ab")

    def test_send_uses_broadcast(self):
        FakeSock.instances = []
        n = wol.send_magic_packet("01:23:45:67:89:AB", "192.168.0.255", 9, repeat=2, sock_factory=FakeSock)
        s = FakeSock.instances[0]
        self.assertEqual(n, 204)
        self.assertEqual(s.sent[0][1], ("192.168.0.255", 9))
        self.assertIn((socket.SOL_SOCKET, socket.SO_BROADCAST, 1), s.opts)
        self.assertTrue(s.closed)
        with self.assertRaises(ValueError):
            wol.send_magic_packet("01:23:45:67:89:AB", port=70000, sock_factory=FakeSock)

    def test_wake_handler(self):
        FakeSock.instances = []
        h = WakeHandler({"repeat": 1}, sock_factory=FakeSock)
        r = h.run({"payload": {"mac": "01:23:45:67:89:AB", "broadcast": "10.0.0.255", "port": 7, "label": "5080"}})
        self.assertEqual(r["sent_bytes"], 102)
        self.assertEqual(FakeSock.instances[0].sent[0][1], ("10.0.0.255", 7))
        self.assertIn("5080", r["summary"])
        with self.assertRaises(PermanentError):
            h.run({"payload": {"mac": "nope"}})

    def test_real_socket_loopback(self):
        """실제 UDP 소켓으로 localhost에 보내 받은 바이트가 매직 패킷인지 확인."""
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        rx.settimeout(2)
        try:
            wol.send_magic_packet("01:23:45:67:89:AB", "127.0.0.1", rx.getsockname()[1], repeat=1)
            data, _ = rx.recvfrom(200)
        finally:
            rx.close()
        self.assertEqual(data, wol.magic_packet("01:23:45:67:89:AB"))


if __name__ == "__main__":
    unittest.main()
