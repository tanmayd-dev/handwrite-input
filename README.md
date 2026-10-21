# Handwrite Input

System-wide handwriting input for Wayland. Write with a pen/stylus and have your handwriting recognized and typed into any application.

## Features

- Full-screen transparent overlay for handwriting input (gtk-layer-shell)
- Pen/stylus support
- Handwriting recognition with [TrOCR](https://huggingface.co/microsoft/trocr-small-handwritten), a transformer model trained on the IAM handwriting dataset, running locally on onnxruntime
- No PyTorch, Transformers or Hugging Face Hub access at runtime: the checkpoint is exported to ONNX once, then loaded from `~/.local/share/handwrite-input/models`
- Multi-line writing: the ink is split into text lines and each line is read on its own (TrOCR only understands one line at a time)
- Incremental typing: ink is recognized and typed as you pause, and only new ink is read afterwards
- Recognition runs off the GTK main loop, so the overlay stays responsive while the model works
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

The runtime needs Python 3 with GTK3, gtk-layer-shell and PyGObject from the
system, plus the packages in `requirements-onnx.txt`:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements-onnx.txt
```

Export a checkpoint once (this is the only step that needs PyTorch and
Transformers):

```bash
python3 -m venv /tmp/trocr-export
/tmp/trocr-export/bin/pip install -r requirements-export.txt
/tmp/trocr-export/bin/python tools/export_trocr_onnx.py
```

The exporter writes `encoder_model.onnx`, `decoder_model.onnx`,
`tokenizer.json` and `onnx_config.json` to
`~/.local/share/handwrite-input/models/trocr-small-onnx/`. Set
`HANDWRITE_ONNX_MODEL` to load an export from a different directory.

The `default.nix` and flake packaging still install the upstream PyTorch
runtime; they have not been converted to the ONNX engine yet. On NixOS, use the
manual setup above (or change the package to depend on `onnxruntime` and
`tokenizers` and point `HANDWRITE_ONNX_MODEL` at the export).

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

4. Recognition runs shortly after you stop writing (1200 ms after the last stroke, adjustable); the text is typed into the focused application. Once ink has been typed it is not read again, so you can keep writing and adding to the text.

5. Hide the overlay:
   ```bash
   handwrite-input-client hide
   ```

### Hyprland Integration

Add a keybind to your Hyprland config:

```lua
hl.bind(mod .. " + CTRL + P", hl.dsp.exec_cmd("handwrite-input-client toggle"))
```

## Options

The overlay's options bar (along the bottom) switches the model and the pause
before recognition. Both are saved to `~/.config/handwrite-input/config.json`
and applied immediately — no rebuild, no restart. A model switch loads the ONNX
export in the background; the status label on the right says what it is doing.

The same settings are available from the client, which is convenient for
keybinds:

```bash
handwrite-input-client settings           # current values
handwrite-input-client models             # models on offer
handwrite-input-client model small        # full id, or a shortcut for an installed export
handwrite-input-client set debounce_ms 600
handwrite-input-client status
```

| Setting | Default | Meaning |
|---|---|---|
| `model` | `microsoft/trocr-small-handwritten` | ONNX TrOCR checkpoint (only exported checkpoints can be loaded) |
| `debounce_ms` | `1200` | Pause after the last stroke before recognising |
| `batch_lines` | `false` | Read all lines in one model call (accepted for compatibility; the ONNX engine always reads line by line) |
| `injector` | `auto` | `ydotool`, `wtype`, or `auto` |
| `line_width` | `15.0` | Stroke width used to render the ink |

Set `HANDWRITE_CONFIG` to use a different settings file, and
`HANDWRITE_ONNX_MODEL` to point at an ONNX export directory.

Only a `trocr-small` export is installed by default. The base and large
checkpoints can be exported with the same tool; the shortcut names are mapped
to full ids but the matching export directory must exist.

### Speed

Recognition is CPU-bound and TrOCR is a single-line model, so the checkpoint
dominates both load time and time per line. The installed ONNX export
(`trocr-small`, 62M params) loads in about 0.6 s and reads a line in roughly
0.2–0.3 s on 6 threads; the daemon is ready about 0.8 s after launch, with no
network access. Peak resident memory is around 1.1 GB with both ONNX sessions
loaded.

Two levers were measured and rejected:

- **Batching the lines into one model call** is *slower*, because the processor
  pads every line to the widest one in the batch.
- **int8 dynamic quantisation** roughly doubles throughput but destroys the
  output — `This is a longer test to see…` came back as `Tthis , is a teenager
  treat to the streets of the streets…` — and quantising a larger checkpoint was
  killed by the kernel for running the box out of memory. It is deliberately not
  offered.

Two things do help beyond the model choice: recognition runs on a worker thread
(so the overlay keeps repainting and capturing strokes while the model works)
and bands of ink much shorter than the typical line are treated as stray marks
rather than read as a line, which avoids a whole inference pass per tick.

The decoder is exported without a key/value cache, matching the checkpoint's
`use_cache=false` generation config. This keeps the ONNX graph simple; at this
model size the extra decoder work is small compared with the encoder pass.

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
- `numpy`, `pillow`, `onnxruntime`, and `tokenizers` (see
  `requirements-onnx.txt`)
- `ydotool` (preferred) or `wtype` for typing the recognised text

The ONNX export itself is produced once by `tools/export_trocr_onnx.py`, which
is the only part that needs PyTorch, Transformers, sentencepiece and `onnx`
(`requirements-export.txt`). The daemon never contacts the Hugging Face Hub;
the model is loaded from `~/.local/share/handwrite-input/models` (override with
`HANDWRITE_ONNX_MODEL`).

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
