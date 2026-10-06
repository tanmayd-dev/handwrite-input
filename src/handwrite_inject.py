"""Type recognised text into the focused window.

Two injectors are supported:

ydotool
    Writes through /dev/uinput, so the events are indistinguishable from an
    ordinary keyboard. Works in every client, XWayland ones included.

wtype
    Drives the compositor's virtual-keyboard protocol with a keymap built from
    the text, then exits and lets the compositor restore the physical keymap.
    Clients that resolve the keymap late — XWayland buffers keycodes before
    translating them, and several toolkits do the same — translate them with the
    *physical* keymap instead, so the text arrives as digits and stray symbols.
    Wayland-native terminals happen to resolve immediately, which is why this
    failure looks app-specific.

`auto` (the default) prefers ydotool when its daemon socket is reachable and
falls back to wtype.
"""

import os
import shutil
import subprocess
import sys
import tempfile

DEFAULT_INJECTOR = 'auto'
VALID_INJECTORS = ('auto', 'ydotool', 'wtype')

# Where ydotoold publishes its socket. YDOTOOL_SOCKET is honoured first when it
# is set; /run/ydotoold/socket is where NixOS' programs.ydotool puts it.
SOCKET_CANDIDATES = ('/run/ydotoold/socket', '/tmp/.ydotool_socket')


def ydotool_socket():
    """Path of a reachable ydotoold socket, or None if there is none.

    The daemon deliberately does not depend on YDOTOOL_SOCKET being exported
    into its unit: that variable only reaches user services when PAM has it,
    which is easy to miss and fails silently.
    """
    runtime_dir = os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'
    candidates = [
        os.environ.get('YDOTOOL_SOCKET'),
        os.path.join(runtime_dir, '.ydotool_socket'),
        *SOCKET_CANDIDATES,
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def preferred():
    """The configured injector, from HANDWRITE_INJECTOR."""
    value = os.environ.get('HANDWRITE_INJECTOR', DEFAULT_INJECTOR)
    return value if value in VALID_INJECTORS else DEFAULT_INJECTOR


def inject_text(text, preference=None):
    """Type `text` into the focused window; return the tool used, or None.

    Raises nothing: injector failures are reported on stderr so the daemon can
    fall back rather than losing the recognised text.
    """
    preference = preference or preferred()
    socket_path = ydotool_socket()
    binary = shutil.which('ydotool')

    if preference in ('auto', 'ydotool'):
        if binary and socket_path:
            # --file sidesteps argument parsing of the recognised text.
            with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as handle:
                handle.write(text)
                path = handle.name
            try:
                proc = subprocess.run(
                    ['ydotool', 'type', '--file', path],
                    env={**os.environ, 'YDOTOOL_SOCKET': socket_path},
                    timeout=30,
                    capture_output=True,
                    text=True,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                print(f'ydotool could not run: {exc}', file=sys.stderr)
            else:
                if proc.returncode == 0:
                    return 'ydotool'
                print(
                    f'ydotool failed (rc={proc.returncode}): {proc.stderr.strip()}',
                    file=sys.stderr,
                )
            finally:
                os.unlink(path)
        else:
            print(
                'ydotool unavailable '
                f"(binary: {binary or 'not found'}, socket: {socket_path or 'not found'})",
                file=sys.stderr,
            )
            if preference == 'ydotool':
                return None

    if preference in ('auto', 'wtype'):
        try:
            proc = subprocess.run(
                ['wtype', '-'], input=text, timeout=30, capture_output=True, text=True
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f'wtype could not run: {exc}', file=sys.stderr)
        else:
            if proc.returncode == 0:
                return 'wtype'
            print(f'wtype failed (rc={proc.returncode}): {proc.stderr.strip()}', file=sys.stderr)

    return None
