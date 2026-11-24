import socket
import unittest
import unittest.mock

from research import netpref

V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))
V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443))


class PreferIpv4Tests(unittest.TestCase):
    def tearDown(self):
        netpref.restore()

    def test_ipv4_moves_to_the_front_when_family_is_unspecified(self):
        def fake(host, port, family=0, type=0, proto=0, flags=0):
            return [V6, V4, V6]

        with unittest.mock.patch.object(socket, "getaddrinfo", fake):
            netpref.prefer_ipv4()
            out = socket.getaddrinfo("example.com", 443)
        self.assertEqual([item[0] for item in out],
                         [socket.AF_INET, socket.AF_INET6, socket.AF_INET6])

    def test_explicit_family_is_left_alone(self):
        def fake(host, port, family=0, type=0, proto=0, flags=0):
            return [V6, V4]

        with unittest.mock.patch.object(socket, "getaddrinfo", fake):
            netpref.prefer_ipv4()
            out = socket.getaddrinfo("example.com", 443, socket.AF_INET6)
        self.assertEqual([item[0] for item in out], [socket.AF_INET6, socket.AF_INET])

    def test_restore_puts_the_original_back_and_double_apply_is_harmless(self):
        original = socket.getaddrinfo
        netpref.prefer_ipv4()
        netpref.prefer_ipv4()
        self.assertIsNot(socket.getaddrinfo, original)
        netpref.restore()
        self.assertIs(socket.getaddrinfo, original)


if __name__ == "__main__":
    unittest.main()
