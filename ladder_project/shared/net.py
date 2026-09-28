from __future__ import annotations

import ipaddress
import json
import socket
import threading
from typing import Any
from typing import Generator

from .constants import BUFFER_SIZE


def sanitize_text(value: Any, max_len: int = 40) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\r", " ").replace("\n", " ").strip()[:max_len]


def resolve_socket_family(host: str) -> int:
    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        return socket.AF_INET
    return socket.AF_INET6 if ip_obj.version == 6 else socket.AF_INET


def socket_endpoint(host: str, port: int) -> tuple[Any, ...]:
    family = resolve_socket_family(host)
    if family == socket.AF_INET6:
        return (host, port, 0, 0)
    return (host, port)


def open_server_socket(host: str, port: int, backlog: int = 200) -> socket.socket:
    family = resolve_socket_family(host)
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(socket_endpoint(host, port))
        sock.listen(backlog)
        return sock
    except OSError:
        sock.close()
        raise


def open_client_socket(host: str, port: int, timeout: float = 5.0) -> socket.socket:
    family = resolve_socket_family(host)
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(socket_endpoint(host, port))
        sock.settimeout(None)
        return sock
    except OSError:
        sock.close()
        raise


def send_json(conn: socket.socket, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    conn.sendall(raw)


def iter_json_messages(
    conn: socket.socket,
    stop_event: threading.Event | None = None,
) -> Generator[dict[str, Any], None, None]:
    conn.settimeout(0.5)
    buffer = b""

    while True:
        if stop_event is not None and stop_event.is_set():
            break

        try:
            chunk = conn.recv(BUFFER_SIZE)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(message, dict):
                    yield message
        except socket.timeout:
            continue
        except OSError:
            break
