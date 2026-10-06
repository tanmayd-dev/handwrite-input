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


def ydotool_socket():
    """Path of the ydotoold socket (see ydotool(1) and YDOTOOL_SOCKET)."""
    return os.environ.get('YDOTOOL_SOCKET') or f'/run/user/{os.getuid()}/.ydotool_socket'


def preferred():
    """The configured injector, from HANDWRITE_INJECTOR."""
    value = os.environ.get('HANDWRITE_INJECTOR', DEFAULT_INJECTOR)
    return value if value in VALID_INJECTORS else DEFAULT_INJECTOR


def ydotool_available():
    return bool(shutil.which('ydotool')) and os.path.exists(ydotool_socket())


def inject_text(text, preference=None):
    """Type `text` into the focused window; return the tool used, or None.

    Raises nothing: injector failures are reported on stderr so the daemon can
    fall back rather than losing the recognised text.
    """
    preference = preference or preferred()

    if preference in ('auto', 'ydotool'):
        if ydotool_available():
            # --file sidesteps argument parsing of the recognised text.
            with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as handle:
                handle.write(text)
                path = handle.name
            try:
                proc = subprocess.run(
                    ['ydotool', 'type', '--file', path],
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
        elif preference == 'ydotool':
            print(
                'ydotool requested but not reachable (daemon or socket missing)',
                file=sys.stderr,
            )

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
