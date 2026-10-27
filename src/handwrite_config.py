"""User settings for handwrite-input.

Stored as JSON so the overlay's options menu, the `handwrite-input-client`
socket commands and hand-editing all act on the same values. The file is
optional: anything missing falls back to DEFAULTS.

Deliberately free of GTK/Wayland imports so it can be exercised headlessly.
"""

import json
import os
from pathlib import Path

DEFAULT_CONFIG_PATH = '~/.config/handwrite-input/config.json'

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


def config_path():
    return Path(os.path.expanduser(os.environ.get('HANDWRITE_CONFIG', DEFAULT_CONFIG_PATH)))


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
