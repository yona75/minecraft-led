"""Minimal Minecraft RCON client (vanilla server, no mods needed)."""
import socket
import struct


class RconError(Exception):
    pass


class Rcon:
    SERVERDATA_AUTH = 3
    SERVERDATA_AUTH_RESPONSE = 2
    SERVERDATA_EXECCOMMAND = 2
    SERVERDATA_RESPONSE_VALUE = 0

    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.sock = None
        self._req_id = 0

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock.settimeout(self.timeout)
        resp_id, _, _ = self._send(self.SERVERDATA_AUTH, self.password)
        if resp_id == -1:
            raise RconError("RCON authentication failed (bad password)")

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def command(self, cmd: str) -> str:
        _, _, body = self._send(self.SERVERDATA_EXECCOMMAND, cmd)
        return body

    def _send(self, ptype: int, body: str):
        self._req_id += 1
        req_id = self._req_id
        payload = struct.pack("<ii", req_id, ptype) + body.encode("utf-8") + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(payload)) + payload)
        return self._recv_packet()

    def _recv_packet(self):
        length = struct.unpack("<i", self._recv_exact(4))[0]
        data = self._recv_exact(length)
        resp_id, ptype = struct.unpack("<ii", data[:8])
        body = data[8:-2].decode("utf-8", errors="replace")
        return resp_id, ptype, body

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RconError("connection closed by server")
            buf += chunk
        return buf

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()


if __name__ == "__main__":
    import sys
    host, port, password = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    with Rcon(host, port, password) as r:
        print(r.command(" ".join(sys.argv[4:]) or "list"))
