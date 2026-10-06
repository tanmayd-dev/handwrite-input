#!/usr/bin/env python3
"""Client for the Handwrite Input daemon."""

import os
import socket
import sys
from pathlib import Path

SOCKET_DIR = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp'))
SOCKET_PATH = SOCKET_DIR / 'handwrite-input.sock'

USAGE = """Usage: handwrite-input-client <command>

Overlay:
  toggle | show | hide | clear | force_reset | quit

Settings (also in the overlay's options bar):
  settings           show the current settings
  models             list the models offered
  model              show the model in use
  model <id|alias>   switch model: small | base | large, or a full model id
  set <key> <value>  change one setting, e.g. set debounce_ms 800
  status             what the daemon is doing right now
"""


def main():
    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help', 'help'):
        print(USAGE, end='')
        sys.exit(0 if args else 1)

    if not SOCKET_PATH.exists():
        print("Handwrite Input daemon is not running", file=sys.stderr)
        sys.exit(1)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    chunks = []
    try:
        sock.connect(str(SOCKET_PATH))
        sock.sendall(' '.join(args).encode())
        sock.shutdown(socket.SHUT_WR)
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        sock.close()

    reply = b''.join(chunks).decode()
    if reply:
        print(reply, end='' if reply.endswith('\n') else '\n')


if __name__ == '__main__':
    main()
