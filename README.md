# Handwrite Input

System-wide handwriting input for Wayland. Write with a pen/stylus and have your handwriting recognized and typed into any application.

## Features

- Transparent overlay window for handwriting input
- Pen/stylus support with pressure sensitivity
- Tesseract OCR for handwriting recognition
- Text insertion via `wtype` (Wayland-native)
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
  imports = [ handwrite-input.nixosModules.handwrite-input ];

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

3. Write with your pen in the overlay window.

4. After a short delay, your handwriting will be recognized and typed into the focused application.

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
- `services.handwrite-input.settings.language` — Tesseract language code (default: `eng`)
- `services.handwrite-input.settings.debounceMs` — Delay before recognition (default: `1500`)
- `services.handwrite-input.settings.windowWidth` — Overlay width (default: `600`)
- `services.handwrite-input.settings.windowHeight` — Overlay height (default: `300`)

## Dependencies

- Python 3 with GTK3, Cairo, and PyGObject
- Tesseract OCR
- wtype (Wayland typing tool)

## License

MIT
