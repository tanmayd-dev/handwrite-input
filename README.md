# Handwrite Input

System-wide handwriting input for Wayland. Write with a pen/stylus and have your handwriting recognized and typed into any application.

## Features

- Full-screen transparent overlay for handwriting input (gtk-layer-shell)
- Pen/stylus support
- Handwriting recognition with [TrOCR](https://huggingface.co/microsoft/trocr-large-handwritten), a transformer model trained on the IAM handwriting dataset
- Multi-line writing: the ink is split into text lines and each line is read on its own (TrOCR only understands one line at a time)
- Incremental typing: ink is recognized and typed as you pause, and only new ink is read afterwards
- Text insertion via `ydotool` (uinput) with automatic fallback to `wtype` (Wayland virtual keyboard)
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

### Manual

```bash
nix build .#default
```

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

4. Recognition runs shortly after you stop writing (1500 ms after the last stroke); the text is typed into the focused application. Once ink has been typed it is not read again, so you can keep writing and adding to the text.

5. Hide the overlay:
   ```bash
   handwrite-input-client hide
   ```

### Hyprland Integration

Add a keybind to your Hyprland config:

```lua
hl.bind(mod .. " + CTRL + P", hl.dsp.exec_cmd("handwrite-input-client toggle"))
```

## Configuration

The NixOS module provides the following options:

- `services.handwrite-input.enable` — Enable the service
- `services.handwrite-input.package` — The package to use
- `services.handwrite-input.users` — Sessions allowed to use the daemon; members are added to `programs.ydotool.group` (needs a new login)
- `services.handwrite-input.injector` — `auto` (default), `ydotool`, or `wtype`

The daemon's own defaults (debounce interval, stroke width) live in `src/handwrite-input.py`.

## Dependencies

- Python 3 with GTK3, gtk-layer-shell, Cairo, and PyGObject
- PyTorch and Transformers (pinned; see `default.nix` for why)
- `ydotool` (preferred) or `wtype` for typing the recognised text

The first recognition downloads the TrOCR weights (~2.2 GB) from Hugging Face into `~/.cache/huggingface` and caches them there.

Enabling the service turns on `programs.ydotool` and `hardware.uinput` and adds
`services.handwrite-input.users` to the ydotool group; group membership needs a
fresh login to take effect.

### Why ydotool rather than wtype alone

`wtype` types through the Wayland virtual-keyboard protocol: it uploads a keymap
covering only the characters in the text, sends the keycodes, then exits and lets
the compositor restore the physical keymap. Clients that resolve that keymap late
— XWayland buffers keycodes before translating them, and several toolkits do the
same — translate them with the physical keymap instead, so recognised text
arrives as digits and stray symbols (`This is a test` landing as `12345637`).
Wayland-native terminals happen to resolve the keymap immediately, which is why
the failure looks app-specific. `ydotool` writes to `/dev/uinput`, so the
keystrokes are indistinguishable from an ordinary keyboard in every client.

## License

MIT
