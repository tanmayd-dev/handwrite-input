"""User settings for handwrite-input.

Stored as JSON so the overlay's options menu, the `handwrite-input-client`
socket commands and hand-editing all act on the same values. The file is
optional: anything missing falls back to DEFAULTS.

The location is `${XDG_CONFIG_HOME:-$HOME/.config}/handwrite-input/config.json`.
Earlier builds hard-coded `~/.config/handwrite-input/config.json` and ignored
XDG_CONFIG_HOME; load() copies such a file over once, without deleting it (see
migrate()).

Deliberately free of GTK/Wayland imports so it can be exercised headlessly.
"""

import json
import os
import shutil
import sys
from pathlib import Path

APP_NAME = 'handwrite-input'

# Where earlier builds always looked. Kept verbatim (~, not XDG) so migration
# finds the file even when XDG_CONFIG_HOME points somewhere else.
LEGACY_CONFIG_PATH = '~/.config/handwrite-input/config.json'

DEFAULTS = {
    # Pause in milliseconds after the last stroke before recognising.
    'debounce_ms': 1200,
    # 'auto' | 'ydotool' | 'wtype' (see handwrite_inject).
    'injector': 'auto',
    # Optional comma-separated technical vocabulary used to choose among
    # Google's alternative readings. Empty means always take its first
    # candidate. Example: waybar,dwm,dwl,config,src
    'lexicon': '',
    # Optional path corrections applied after recognition, as
    # source=replacement pairs. Only matches directly after "in". Useful when
    # handwriting a leading dot is not returned by the recogniser:
    # config=~/.config,local/src/dwl=~/.local/src/dwl
    'path_terms': '',
    # Recognise automatically after the debounce pause. Off by default: the
    # recogniser works on whole lines, so typing each paused chunk separately
    # loses context and turns a sentence into fragments. With this off, write
    # the whole prompt and press Done (or hide the overlay) to recognise it
    # once.
    'auto_recognize': False,
    # Pen stroke width used when drawing the overlay.
    'line_width': 15.0,
}


def config_home():
    """XDG configuration root: $XDG_CONFIG_HOME, else ~/.config."""
    value = os.environ.get('XDG_CONFIG_HOME', '').strip()
    if value:
        return Path(os.path.expanduser(value))
    return Path(os.path.expanduser('~/.config'))


def legacy_config_path():
    """The pre-XDG settings file, with XDG_CONFIG_HOME ignored."""
    return Path(os.path.expanduser(LEGACY_CONFIG_PATH))


def config_path():
    """Settings file in use: HANDWRITE_CONFIG, else the XDG location."""
    override = os.environ.get('HANDWRITE_CONFIG')
    if override:
        return Path(os.path.expanduser(override))
    return config_home() / APP_NAME / 'config.json'


def migrate():
    """Copy a pre-XDG settings file to the XDG location, once.

    Only does something when XDG_CONFIG_HOME moves the file (so the old and new
    paths differ), the new file does not exist yet, and the old one does. The
    copy is deliberately non-destructive: the old file is left untouched, so
    pointing XDG_CONFIG_HOME back — or downgrading — keeps working.

    An explicit HANDWRITE_CONFIG is never migrated into or out of. Returns the
    path written when a copy was made, else None.
    """
    if os.environ.get('HANDWRITE_CONFIG'):
        return None
    target = config_path()
    source = legacy_config_path()
    if source == target or target.exists() or not source.exists():
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    except OSError as exc:
        # Settings are optional; a failed migration must not stop the daemon.
        print(f'Could not migrate settings from {source}: {exc}', file=sys.stderr)
        return None
    print(
        f'Migrated settings from {source} to {target} (original kept)',
        file=sys.stderr,
    )
    return target


def coerce(key, value):
    """Validate one setting, falling back to the default when unusable."""
    default = DEFAULTS.get(key)
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'yes', 'true', 'on')
        return bool(value)
    if isinstance(default, int):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        if key == 'debounce_ms':
            return max(200, min(10000, number))
        return number
    if isinstance(default, float):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return max(1.0, min(60.0, number)) if key == 'line_width' else number
    if key == 'injector':
        text = str(value).strip().lower()
        return text if text in ('auto', 'ydotool', 'wtype') else default
    if key == 'lexicon':
        if isinstance(value, (list, tuple)):
            return ','.join(str(term).strip() for term in value if str(term).strip())
        return str(value).strip()
    if key == 'path_terms':
        if isinstance(value, (list, tuple)):
            return ','.join(str(term).strip() for term in value if str(term).strip())
        return str(value).strip()
    return value


def load():
    """Read the settings file, overlaying it on DEFAULTS."""
    settings = dict(DEFAULTS)
    migrate()
    path = config_path()
    try:
        with open(path) as handle:
            stored = json.load(handle)
    except FileNotFoundError:
        return settings
    except (OSError, ValueError):
        # A corrupt file must not stop the daemon from starting.
        return settings
    if not isinstance(stored, dict):
        return settings
    for key, value in stored.items():
        if key in DEFAULTS:
            settings[key] = coerce(key, value)
    return settings


def save(settings):
    """Write the full settings dict, creating the parent directory."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {key: coerce(key, settings.get(key, default)) for key, default in DEFAULTS.items()}
    with open(path, 'w') as handle:
        json.dump(merged, handle, indent=2, sort_keys=True)
        handle.write('\n')
    return merged
