#!/usr/bin/env python3
"""Client for the Handwrite Input daemon."""

import os
import sys
import socket
from pathlib import Path

SOCKET_DIR = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp'))
SOCKET_PATH = SOCKET_DIR / 'handwrite-input.sock'


def main():
    if len(sys.argv) < 2:
        print("Usage: handwrite-input-client <command>")
        print("Commands: toggle, show, hide, clear, force_reset, quit")
        sys.exit(1)

    command = sys.argv[1]

    if not SOCKET_PATH.exists():
        print("Handwrite Input daemon is not running", file=sys.stderr)
        sys.exit(1)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(SOCKET_PATH))
        sock.sendall(command.encode())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        sock.close()


if __name__ == '__main__':
    main()
