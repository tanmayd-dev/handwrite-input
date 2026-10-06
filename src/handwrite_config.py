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
    # Model id passed to transformers, or one of the KNOWN_MODELS shortcuts.
    'model': 'microsoft/trocr-base-handwritten',
    # Pause in milliseconds after the last stroke before recognising.
    'debounce_ms': 1200,
    # Feed all lines of one recognition to the model in a single batch.
    # Off by default: measured on a five-line sample, batching was *slower*
    # than reading line by line (11.5s vs 7.7s with trocr-large) because the
    # processor pads every line to the widest one in the batch.
    'batch_lines': False,
    # 'auto' | 'ydotool' | 'wtype' (see handwrite_inject).
    'injector': 'auto',
    # Pen stroke width used when rendering the ink for the model.
    'line_width': 15.0,
}

# Shortcuts accepted in the model setting, cheapest first.
KNOWN_MODELS = (
    ('microsoft/trocr-small-handwritten', 'small (62M params, fastest)'),
    ('microsoft/trocr-base-handwritten', 'base (334M params)'),
    ('microsoft/trocr-large-handwritten', 'large (558M params, most accurate)'),
)

MODEL_ALIASES = {
    'small': 'microsoft/trocr-small-handwritten',
    'base': 'microsoft/trocr-base-handwritten',
    'large': 'microsoft/trocr-large-handwritten',
}


def config_path():
    return Path(os.path.expanduser(os.environ.get('HANDWRITE_CONFIG', DEFAULT_CONFIG_PATH)))


def resolve_model(value):
    """Turn a shortcut ('base') or a model id into a model id."""
    value = str(value).strip()
    return MODEL_ALIASES.get(value.lower(), value)


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
    if key == 'model':
        text = str(value).strip()
        return resolve_model(text) if text else default
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
