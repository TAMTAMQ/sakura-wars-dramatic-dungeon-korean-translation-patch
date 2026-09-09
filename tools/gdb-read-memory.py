"""Read guest memory from a running emulator over the GDB remote protocol.

melonDS exposes a GDB stub per CPU, so this speaks just enough of the protocol
to halt the target, pull byte ranges, and let it run again.
"""

from __future__ import annotations

import argparse
import socket
from pathlib import Path


class GdbClient:
    def __init__(self, host: str, port: int, timeout: float = 10.0) -> None:
        self._socket = socket.create_connection((host, port), timeout=timeout)
        self._socket.settimeout(timeout)
        self._buffer = b""

    def close(self) -> None:
        self._socket.close()

    @staticmethod
    def _checksum(payload: bytes) -> bytes:
        return f"{sum(payload) & 0xFF:02x}".encode()

    def _send(self, payload: bytes) -> None:
        self._socket.sendall(b"$" + payload + b"#" + self._checksum(payload))

    def _read_byte(self) -> bytes:
        if not self._buffer:
            chunk = self._socket.recv(65536)
            if not chunk:
                raise ConnectionError("gdb stub closed the connection")
            self._buffer = chunk
        byte, self._buffer = self._buffer[:1], self._buffer[1:]
        return byte

    def _receive(self) -> bytes:
        while True:
            byte = self._read_byte()
            if byte == b"+":
                continue
            if byte == b"-":
                raise ConnectionError("gdb stub rejected the packet")
            if byte == b"$":
                break
        payload = b""
        while True:
            byte = self._read_byte()
            if byte == b"#":
                break
            payload += byte
        self._read_byte()
        self._read_byte()
        self._socket.sendall(b"+")
        return payload

    def command(self, payload: str) -> bytes:
        self._send(payload.encode())
        return self._receive()

    def interrupt(self) -> bytes:
        self._socket.sendall(b"\x03")
        return self._receive()

    def read(self, address: int, length: int, chunk: int = 1024) -> bytes:
        data = bytearray()
        while len(data) < length:
            span = min(chunk, length - len(data))
            reply = self.command(f"m{address + len(data):x},{span:x}")
            if reply.startswith(b"E") and len(reply) <= 3:
                raise ValueError(f"gdb stub refused address {address + len(data):#010x}: {reply!r}")
            data.extend(bytes.fromhex(reply.decode()))
        return bytes(data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3333)
    parser.add_argument("--address", required=True)
    parser.add_argument("--length", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--halt", action="store_true", help="interrupt the target before reading")
    parser.add_argument("--continue-after", action="store_true", help="resume the target when done")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    address = int(args.address, 0)
    length = int(args.length, 0)
    client = GdbClient(args.host, args.port)
    try:
        if args.halt:
            client.interrupt()
        payload = client.read(address, length)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload)
        print(f"read {len(payload)} bytes from {address:#010x} -> {args.output}")
        if args.continue_after:
            client._send(b"c")
    finally:
        client.close()


if __name__ == "__main__":
    main()
