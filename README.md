# Handwrite Input

System-wide handwriting input for Wayland. Write with a pen/stylus and have your
handwriting recognized and typed into any application.

Recognition is performed by Google Input Tools, so the daemon needs network
access. There is no local/offline recognizer.

## Features

- Full-screen transparent overlay for handwriting input (gtk-layer-shell)
- Pen/stylus support
- Google Input Tools recognition, with one request per visual line sent in
  parallel
- Multi-line writing: strokes are grouped into lines using pen movement, then
  each line is recognized separately
- Technical vocabulary and path corrections for words the recognizer gets
  almost right (`way bar` -> `waybar`, `config` -> `~/.config`)
- Recognition runs off the GTK main loop, so the overlay stays responsive while
  requests are in flight
- Text insertion via `ydotool` (uinput) with automatic fallback to `wtype`
  (Wayland virtual keyboard)
- Unix socket IPC for integration with window managers
- NixOS module for easy installation

## Installation

### NixOS

Add the flake input to your `flake.nix`:

```nix
{
  inputs.handwrite-input.url = "git+https://gitea.edley.me/petere/handwrite-input.git";
}
```

Then import the module and enable the service:

```nix
{
  imports = [ inputs.handwrite-input.nixosModules.handwrite-input ];

  services.handwrite-input.enable = true;
}
```

### Arch Linux

`PKGBUILD` (in the repository root) builds `handwrite-input-git` and installs
the daemon, the client and a systemd **user** unit:

```bash
git clone https://github.com/tanmayd-dev/handwrite-input.git
cd handwrite-input
makepkg -si
systemctl --user enable --now handwrite-input
```

The unit is `/usr/lib/systemd/user/handwrite-input.service` and is pulled in by
`graphical-session.target`. Compositors that never reach that target (plain
Hyprland without uwsm, for example) have to start it themselves, e.g.
`exec-once = systemctl --user start handwrite-input` in `hyprland.conf`.

Both injectors are optional dependencies: `ydotool` is preferred and needs its
daemon (`sudo systemctl enable --now ydotoold`) plus access to `/dev/uinput`;
`wtype` needs nothing beyond the compositor. If the overlay does not appear,
the user manager may lack the session environment:

```bash
systemctl --user import-environment WAYLAND_DISPLAY XDG_RUNTIME_DIR
systemctl --user restart handwrite-input
```

### Manual

The runtime needs Python 3 with GTK3, gtk-layer-shell, Cairo, and PyGObject
from the system. No Python ML packages are required:

```bash
python3 -m venv --system-site-packages .venv
```

Start the daemon from the checkout:

```bash
.venv/bin/python src/handwrite-input.py
```

The client is a separate script:

```bash
.venv/bin/python src/handwrite-input-client.py status
```

The example wrappers in `~/.local/bin` point at those two scripts.

## Usage

1. Start the daemon (automatically on NixOS with the module):

   ```bash
   handwrite-input &
   ```

2. Show the overlay:

   ```bash
   handwrite-input-client show
   ```

3. Write with your pen in the overlay.

4. Write the whole prompt, then press **Done** (or hide the overlay). The full
   ink is recognized once and typed into the focused application.

5. Hide the overlay:

   ```bash
   handwrite-input-client hide
   ```

With the packaged install the daemon is a systemd user service:

```bash
systemctl --user enable --now handwrite-input
systemctl --user status handwrite-input
```

`auto_recognize=false` is the default: pressing **Done** gives the recognizer
the whole line as context. Enabling it sends each paused chunk separately,
which is normally less accurate.

### Hyprland Integration

Add a keybind to your Hyprland config:

```lua
hl.bind(mod .. " + CTRL + P", hl.dsp.exec_cmd("handwrite-input-client toggle"))
```

## Options

The overlay's options bar switches the pause before recognition. Settings are
saved to `${XDG_CONFIG_HOME:-$HOME/.config}/handwrite-input/config.json` and
applied immediately; no restart is needed. The file is optional: missing keys
fall back to the defaults in the table below.

Earlier builds hard-coded `~/.config/handwrite-input/config.json` and ignored
`XDG_CONFIG_HOME`. When that variable is set and the XDG location has no
settings file yet, the old file is copied there once at startup. The migration
is non-destructive — the old file is left in place, untouched, so pointing
`XDG_CONFIG_HOME` back or downgrading keeps working. `HANDWRITE_CONFIG` still
selects a specific file, and nothing is migrated while it is set.

The same settings are available from the client:

```bash
handwrite-input-client settings
handwrite-input-client set debounce_ms 600
handwrite-input-client status
```

| Setting | Default | Meaning |
|---|---|---|
| `debounce_ms` | `1200` | Pause after the last stroke before recognizing (only used with `auto_recognize`) |
| `auto_recognize` | `false` | Recognize after each pause; off means recognize the whole prompt on **Done**/hide |
| `injector` | `auto` | `ydotool`, `wtype`, or `auto` |
| `line_width` | `15.0` | Stroke width used to draw the overlay |
| `lexicon` | empty | Comma-separated technical terms used to choose between Google's candidates, e.g. `waybar,dwm,dwl,config,src` |
| `path_terms` | empty | Optional `source=replacement` path corrections applied after `in`, e.g. `config=~/.config,local/src/dwl=~/.local/src/dwl` |

Set `HANDWRITE_CONFIG` to use a different settings file.

### Google Input Tools

The backend posts the **raw pen strokes** for each line to the free,
**undocumented** endpoint at `inputtools.google.com`. It does not upload a
rendered image. This is not a supported Google API: it is rate-limited, may
change without notice, and may disappear entirely. There is no local/offline
recognizer, so without network access recognition is unavailable — if a request
fails, the daemon reports `recognition failed; ink kept`, leaves the ink in
place, and you can press **Done** again once connectivity is back.

Check the endpoint before relying on it (needs neither a compositor nor the
daemon):

```bash
handwrite-input --check
handwrite-input-client --check
```

Both print the endpoint and its answer, and exit non-zero with a clear reason
when the endpoint cannot be reached or answers in an unexpected shape. The
check sends one tiny synthetic stroke, not any of your ink.

Because Google returns several candidate readings, `lexicon` is used to pick
the candidate containing the most configured terms. Whole-term matching also
accepts split forms, so `way bar` is normalized to `waybar` when `waybar` is in
the lexicon. Leave it empty to always use Google's first candidate.

`path_terms` is a small, explicit correction for paths whose leading dot is not
returned by the recognizer. For example, with
`path_terms=config=~/.config,local/src/dwl=~/.local/src/dwl`,
`waybar is in config` becomes `waybar is in ~/.config`, while occurrences not
preceded by `in` are left alone.

For debugging, start the daemon with `HANDWRITE_DUMP_STROKES=1` to write the
last sample to `/tmp/handwrite-strokes.json`. A standalone comparison tool is
available:

```bash
python tools/try_google_handwriting.py
```

### Speed

The five-line sample used during development completed in roughly 0.6–1.0 s
once the per-line requests were made concurrently. Recognition latency still depends on
the network; the overlay itself hides as soon as **Done** is pressed, and the
text appears when the requests return.

## Configuration

The NixOS module provides the following options:

- `services.handwrite-input.enable` — Enable the service
- `services.handwrite-input.package` — The package to use
- `services.handwrite-input.users` — Sessions allowed to use the daemon; members are added to `programs.ydotool.group` (needs a new login)
- `services.handwrite-input.injector` — `auto` (default), `ydotool`, or `wtype`

Runtime settings are not NixOS options: they live in the JSON file above, so
they can be changed from the overlay or the client without a rebuild.

## Dependencies

- Python 3 with GTK3, gtk-layer-shell, Cairo, and PyGObject
- Network access to `inputtools.google.com` (online-only; no offline fallback)
- `ydotool` (preferred) or `wtype` for typing the recognized text
- systemd, for the packaged user unit (the scripts also run by hand)

`PKGBUILD` lists the Arch package names for the first two: `python`,
`python-gobject`, `python-cairo`, `gtk3`, `gtk-layer-shell`; the injectors are
`optdepends`.

Enabling the NixOS service turns on `programs.ydotool` and `hardware.uinput` and
adds `services.handwrite-input.users` to the ydotool group; group membership
needs a fresh login to take effect.

### Why ydotool rather than wtype alone

`wtype` types through the Wayland virtual-keyboard protocol: it uploads a keymap
covering only the characters in the text, sends the keycodes, then exits and lets
the compositor restore the physical keymap. Clients that resolve that keymap late
— XWayland buffers keycodes before translating them, and several toolkits do the
same — translate them with the physical keymap instead, so recognized text
arrives as digits and stray symbols (`This is a test` landing as `12345637`).
Wayland-native terminals happen to resolve the keymap immediately, which is why
the failure looks app-specific. `ydotool` writes to `/dev/uinput`, so the
keystrokes are indistinguishable from an ordinary keyboard in every client.

## License

MIT
