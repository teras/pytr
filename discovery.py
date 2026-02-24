#!/usr/bin/env python3
# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""PYTR LAN discovery service.

Listens for UDP broadcast queries on port 5444.
When a client sends "who is PytrServer?", responds with JSON:
  {"Id": "<hostname>", "Name": "PYTR", "Address": "http://<ip>:8000"}

Designed to run with network_mode: host in Docker.
"""
import json
import socket

DISCOVERY_PORT = 5444
HTTP_PORT = 8000
QUERY = b"who is PytrServer?"


def get_local_ip():
    """Get the LAN IP of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("", DISCOVERY_PORT))

    hostname = socket.gethostname()
    local_ip = get_local_ip()
    response = json.dumps({
        "Id": hostname,
        "Name": "PYTR",
        "Address": f"http://{local_ip}:{HTTP_PORT}",
    }).encode()

    print(f"PYTR discovery listening on UDP {DISCOVERY_PORT} (address: http://{local_ip}:{HTTP_PORT})")

    while True:
        data, addr = sock.recvfrom(256)
        if data.strip() == QUERY:
            print(f"Discovery query from {addr[0]}")
            sock.sendto(response, addr)


if __name__ == "__main__":
    main()
