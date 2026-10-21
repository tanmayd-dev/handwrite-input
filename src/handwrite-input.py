#!/usr/bin/env python3
"""
Handwrite Input — system-wide handwriting recognition for Wayland.

A daemon that shows a transparent overlay where you can write with a pen.
After a debounce period, the handwriting is recognized with TrOCR
(microsoft/trocr-large-handwritten, ~2.2 GB, downloaded on first use)
and typed into the focused application via ydotool (uinput) or wtype.

Uses gtk-layer-shell for proper Hyprland overlay support.
"""

import json
import os
import sys
import socket
import subprocess
import signal
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GtkLayerShell', '0.1')
from gi.repository import Gtk, Gdk, GLib, GtkLayerShell

import cairo
from transformers import (
    RobertaTokenizer,
    ViTImageProcessor,
    VisionEncoderDecoderModel,
    XLMRobertaTokenizer,
)

import handwrite_config
import handwrite_inject
import handwrite_ocr

SOCKET_DIR = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp'))
SOCKET_PATH = SOCKET_DIR / 'handwrite-input.sock'

DEBUG_IMAGE_PATH = '/tmp/handwrite-debug.png'

# Pause choices offered in the overlay's options bar.
PAUSE_CHOICES = (400, 800, 1200, 2000, 3000)

DEFAULT_DEBOUNCE_MS = 1500
DEFAULT_LANGUAGE = 'en'
LINE_WIDTH = 15.0


class HandwriteOverlay:
    """The transparent overlay window for handwriting input."""

    def __init__(self, daemon):
        self.daemon = daemon
        self.window = None
        self.strokes = []
        self.current_stroke = []
        self.is_drawing = False
        self.debounce_id = None
        # Strokes already sent for recognition; only the tail beyond this is
        # new ink. Without it the debounce re-types everything on each pause.
        self.consumed = 0
        # Bumped whenever the ink is discarded, so a recognition running on a
        # worker thread cannot mark ink as read that no longer exists.
        self.generation = 0
        # Options bar widgets (see build_toolbar), populated when shown.
        self.model_combo = None
        self.pause_combo = None
        self.status_label = None
        self._syncing = False

    def show(self):
        if self.window is not None:
            self.force_reset()
            self.window.show_all()
            return

        self.window = Gtk.Window()
        self.window.set_decorated(False)

        # Initialize as a layer shell surface (proper Hyprland overlay)
        GtkLayerShell.init_for_window(self.window)
        GtkLayerShell.set_layer(self.window, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_exclusive_zone(self.window, -1)
        GtkLayerShell.set_keyboard_mode(self.window, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.TOP, 50)
        GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.BOTTOM, 50)
        GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.LEFT, 50)
        GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.RIGHT, 50)

        # Try to make it transparent
        screen = self.window.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.window.set_visual(visual)

        # Drawing area — fills the entire window
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.connect('draw', self.on_draw)
        self.drawing_area.connect('motion-notify-event', self.on_motion)
        self.drawing_area.connect('button-press-event', self.on_button_press)
        self.drawing_area.connect('button-release-event', self.on_button_release)
        self.drawing_area.set_events(
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK
        )

        # Layout
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.pack_start(self.drawing_area, True, True, 0)

        # Options bar: everything here is settable at runtime and persisted to
        # handwrite_config's JSON file.
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hbox.set_margin_top(6)
        hbox.set_margin_bottom(6)
        hbox.set_margin_start(6)
        hbox.set_margin_end(6)

        clear_btn = Gtk.Button(label="Clear")
        clear_btn.connect('clicked', lambda b: self.clear())
        hbox.pack_start(clear_btn, False, False, 0)

        done_btn = Gtk.Button(label="Done")
        done_btn.connect('clicked', lambda b: self.finish())
        hbox.pack_end(done_btn, False, False, 0)

        self.status_label = Gtk.Label(label=self.daemon.status)
        hbox.pack_end(self.status_label, False, False, 0)

        hbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 6)

        hbox.pack_start(Gtk.Label(label="Model:"), False, False, 0)
        self.model_combo = Gtk.ComboBoxText()
        for model_id, label in handwrite_config.KNOWN_MODELS:
            self.model_combo.append(model_id, label)
        self.model_combo.connect('changed', self.on_model_changed)
        hbox.pack_start(self.model_combo, False, False, 0)

        hbox.pack_start(Gtk.Label(label="Pause:"), False, False, 0)
        self.pause_combo = Gtk.ComboBoxText()
        for pause in PAUSE_CHOICES:
            self.pause_combo.append(str(pause), f'{pause} ms')
        self.pause_combo.connect('changed', self.on_pause_changed)
        hbox.pack_start(self.pause_combo, False, False, 0)

        vbox.pack_start(hbox, False, False, 0)

        self.window.add(vbox)
        self.window.connect('delete-event', lambda w, e: self.hide())
        self.window.show_all()
        self.sync_toolbar()

    def sync_toolbar(self):
        """Point the options bar at the current settings."""
        if self.model_combo is None:
            return
        settings = self.daemon.settings
        self._syncing = True
        try:
            model = settings['model']
            if self.model_combo.set_active_id(model) is None:
                self.model_combo.append(model, f'{model} (custom)')
                self.model_combo.set_active_id(model)
            pause = str(settings['debounce_ms'])
            if self.pause_combo.set_active_id(pause) is None:
                self.pause_combo.append(pause, f'{pause} ms (custom)')
                self.pause_combo.set_active_id(pause)
            self.status_label.set_text(self.daemon.status)
        finally:
            self._syncing = False

    def on_model_changed(self, combo):
        if self._syncing:
            return
        model = combo.get_active_id()
        if model and model != self.daemon.settings['model']:
            self.daemon.apply_settings({'model': model})

    def on_pause_changed(self, combo):
        if self._syncing:
            return
        value = combo.get_active_id()
        if value:
            self.daemon.apply_settings({'debounce_ms': int(value)})

    def hide(self):
        if self.window is not None:
            self.window.hide()

    def clear(self):
        self.strokes = []
        self.current_stroke = []
        self.is_drawing = False
        self.consumed = 0
        self.generation += 1
        self.drawing_area.queue_draw()

    def force_reset(self):
        self.strokes = []
        self.current_stroke = []
        self.is_drawing = False
        self.consumed = 0
        self.generation += 1
        if self.debounce_id is not None:
            GLib.source_remove(self.debounce_id)
            self.debounce_id = None
        self.drawing_area.queue_draw()

    def finish(self):
        self.daemon.recognize()
        self.clear()
        self.hide()

    def on_draw(self, widget, cr):
        # Draw white background
        cr.set_source_rgba(1, 1, 1, 1)
        cr.paint()

        # Draw all completed strokes
        for stroke in self.strokes:
            self._draw_stroke(cr, stroke)

        # Draw current stroke
        if self.current_stroke:
            self._draw_stroke(cr, self.current_stroke)

    def _draw_stroke(self, cr, stroke):
        if len(stroke) < 2:
            return
        cr.set_source_rgba(0, 0, 0, 0.8)
        cr.set_line_width(self.daemon.settings['line_width'])
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.move_to(stroke[0][0], stroke[0][1])
        for point in stroke[1:]:
            cr.line_to(point[0], point[1])
        cr.stroke()

    def on_button_press(self, widget, event):
        if event.button != 1:
            return False
        device = event.get_source_device()
        if device is None:
            return False
        source = device.get_source()
        if source in (Gdk.InputSource.PEN, Gdk.InputSource.ERASER, Gdk.InputSource.MOUSE):
            self.is_drawing = True
            self.current_stroke = [(event.x, event.y, 1.0)]
            return True
        return False

    def on_button_release(self, widget, event):
        if event.button != 1:
            return False
        if self.is_drawing and self.current_stroke:
            self.strokes.append(self.current_stroke)
            self.current_stroke = []
            self.is_drawing = False
            self.schedule_recognize()
        return True

    def on_motion(self, widget, event):
        if not self.is_drawing:
            return False
        device = event.get_source_device()
        if device is None:
            return False
        source = device.get_source()
        if source not in (Gdk.InputSource.PEN, Gdk.InputSource.ERASER, Gdk.InputSource.MOUSE):
            return False

        pressure = event.get_axis(Gdk.AxisUse.PRESSURE)
        if pressure is None:
            pressure = 1.0

        self.current_stroke.append((event.x, event.y, pressure))
        self.drawing_area.queue_draw()
        return True

    def schedule_recognize(self):
        if self.debounce_id is not None:
            GLib.source_remove(self.debounce_id)
        self.debounce_id = GLib.timeout_add(self.daemon.settings['debounce_ms'], self.on_debounce)

    def on_debounce(self):
        self.debounce_id = None
        self.daemon.recognize()
        return False


class HandwriteDaemon:
    """The main daemon that manages the overlay and IPC."""

    def __init__(self):
        self.settings = handwrite_config.load()
        self.overlay = HandwriteOverlay(self)
        self.sock = None
        self.visible = False
        self.status = 'idle'
        self._trocr = None
        self._trocr_model = None
        self._lock = threading.Lock()
        self._busy = False

    # --- model ------------------------------------------------------------

    def _load_trocr(self, model_id=None):
        """Load (and cache) the tokenizer, image processor and model.

        Only ever called from worker threads: loading takes seconds and must
        not run on the GTK main loop.
        """
        model_id = model_id or self.settings['model']
        with self._lock:
            if self._trocr is None or self._trocr_model != model_id:
                self._trocr = None
                short = model_id.rsplit('/', 1)[-1]
                self._set_status(f'loading {short}…')
                # Slow Roberta tokenizer — AutoTokenizer in transformers 5.x
                # tries (and fails) to convert this to a fast tokenizer.
                tokenizer = self._load_tokenizer(model_id)
                image_processor = ViTImageProcessor.from_pretrained(model_id)
                model = VisionEncoderDecoderModel.from_pretrained(model_id)
                model.eval()
                self._trocr = (tokenizer, image_processor, model)
                self._trocr_model = model_id
                self._set_status(f'{short} ready')
        return self._trocr

    def preload_model(self):
        """Warm the model in the background so the first write is not slow."""
        threading.Thread(target=self._preload_worker, daemon=True).start()

    @staticmethod
    def _load_tokenizer(model_id):
        """Load the tokenizer flavour a checkpoint actually ships.

        trocr-large/base-handwritten ship roberta files (vocab.json +
        merges.txt); trocr-small-handwritten instead ships
        sentencepiece.bpe.model and declares XLMRobertaTokenizer, which needs
        `protobuf` in the environment.

        transformers 5.x can load a degenerate roberta tokenizer from the
        small checkpoint (a five-token vocab) instead of raising, which makes
        every line decode to the empty string. Only accept a real vocab.
        """
        try:
            tokenizer = RobertaTokenizer.from_pretrained(model_id)
            if tokenizer.vocab_size >= 1000:
                return tokenizer
            print(
                f'Roberta tokenizer for {model_id} has a {tokenizer.vocab_size}-token vocab; '
                'trying XLMRobertaTokenizer',
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001 - try the other flavour
            print(
                f'Roberta tokenizer unavailable for {model_id} ({exc}); '
                'trying XLMRobertaTokenizer',
                file=sys.stderr,
            )
        return XLMRobertaTokenizer.from_pretrained(model_id)

    def _preload_worker(self):
        target = self.settings['model']
        try:
            self._load_trocr()
        except Exception as exc:  # noqa: BLE001 - report, keep the daemon alive
            print(f'Model load error: {exc}', file=sys.stderr)
            # A failed switch must not leave the daemon with no model at all:
            # go back to whichever one was working.
            fallback = self._trocr_model
            if not fallback or fallback == target:
                self._set_status('model failed to load')
                return
            print(f'Model switch failed, reverting to {fallback}', file=sys.stderr)
            self.settings['model'] = fallback
            try:
                handwrite_config.save(self.settings)
            except OSError as save_exc:
                print(f'Could not save settings: {save_exc}', file=sys.stderr)
            GLib.idle_add(self.overlay.sync_toolbar)
            try:
                self._load_trocr()
            except Exception as reload_exc:  # noqa: BLE001
                print(f'Could not reload {fallback}: {reload_exc}', file=sys.stderr)
                self._set_status('no model loaded')
                return
            short = fallback.rsplit('/', 1)[-1]
            self._set_status(f'{short} restored (switch failed)')

    def _set_status(self, text):
        self.status = text
        GLib.idle_add(self._show_status)

    def _show_status(self):
        if self.overlay.status_label is not None:
            self.overlay.status_label.set_text(self.status)
        return False

    # --- settings ---------------------------------------------------------

    def apply_settings(self, updates):
        """Validate, persist and apply settings, reloading the model if needed."""
        previous_model = self.settings['model']
        for key, value in updates.items():
            if key in handwrite_config.DEFAULTS:
                self.settings[key] = handwrite_config.coerce(key, value)
        try:
            handwrite_config.save(self.settings)
        except OSError as exc:
            print(f'Could not save settings: {exc}', file=sys.stderr)
        if self.settings['model'] != previous_model:
            self.preload_model()
        GLib.idle_add(self.overlay.sync_toolbar)
        return self.settings

    def set_setting(self, key, value):
        """Apply one setting from IPC; return a human-readable reply."""
        if key not in handwrite_config.DEFAULTS:
            return f'unknown setting: {key}\nknown: {", ".join(sorted(handwrite_config.DEFAULTS))}\n'
        self.apply_settings({key: value})
        return f'{key} = {self.settings[key]!r}\n'

    def start(self):
        # Clean up old socket
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        # Create socket
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(SOCKET_PATH))
        self.sock.listen(5)
        self.sock.setblocking(False)

        # Add to GTK main loop
        GLib.io_add_watch(self.sock, GLib.IO_IN, self.on_client_connect)

        # Handle SIGTERM
        signal.signal(signal.SIGTERM, self.on_signal)

        # Warm the model now: the first write would otherwise wait for it.
        print(f'Settings: {handwrite_config.config_path()}', file=sys.stderr)
        self.preload_model()

        Gtk.main()

    def on_signal(self, signum, frame):
        self.cleanup()
        sys.exit(0)

    def cleanup(self):
        if self.sock:
            self.sock.close()
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

    def on_client_connect(self, sock, condition):
        try:
            conn, _ = sock.accept()
            conn.settimeout(2)
            command = conn.recv(4096).decode().strip()
            reply = self.handle_command(command)
            if reply:
                conn.sendall(reply.encode())
            conn.close()
        except Exception as e:
            print(f"Error handling client: {e}", file=sys.stderr)
        return True

    def handle_command(self, command):
        """Run one IPC command; return a reply for the client, or ''."""
        if not command:
            return ''

        name, _, rest = command.partition(' ')
        rest = rest.strip()

        if name == 'toggle':
            self.toggle()
        elif name == 'show':
            self.show()
        elif name == 'hide':
            self.hide()
        elif name == 'clear':
            self.overlay.clear()
        elif name == 'force_reset':
            self.overlay.force_reset()
        elif name == 'quit':
            self.cleanup()
            Gtk.main_quit()
        elif name == 'status':
            return f'{self.status}\n'
        elif name == 'settings':
            return json.dumps(self.settings, indent=2, sort_keys=True) + '\n'
        elif name == 'models':
            lines = [
                f'{model_id}\t{label}' for model_id, label in handwrite_config.KNOWN_MODELS
            ]
            return '\n'.join(lines) + '\n'
        elif name == 'model':
            if not rest:
                return f'{self.settings["model"]}\n'
            if rest == 'list':
                return self.handle_command('models')
            return self.set_setting('model', rest)
        elif name == 'set':
            key, _, value = rest.partition(' ')
            if not key or not value:
                return 'usage: set <key> <value>\n'
            return self.set_setting(key, value)
        else:
            return f'unknown command: {name}\n'
        return ''

    def toggle(self):
        if self.visible:
            self.hide()
        else:
            self.show()

    def show(self):
        self.overlay.show()
        self.visible = True

    def hide(self):
        self.overlay.hide()
        self.visible = False

    def recognize(self):
        """Queue a recognition on a worker thread.

        Inference takes seconds to tens of seconds on CPU; running it on the GTK
        main loop would freeze the overlay (no repaint, queued pen events) for
        that whole time.
        """
        if not self.overlay.strokes or self._busy:
            return

        strokes = self.overlay.strokes[self.overlay.consumed:]
        if not strokes:
            return

        total = len(self.overlay.strokes)
        generation = self.overlay.generation
        self._busy = True
        threading.Thread(
            target=self._recognize_worker,
            args=(strokes, total, generation),
            daemon=True,
        ).start()

    def _recognize_worker(self, strokes, total, generation):
        advanced = False
        try:
            image = handwrite_ocr.build_ink_image(
                strokes, line_width=self.settings['line_width']
            )
            if image is None:
                return

            # Keep the exact image handed to the model, for debugging.
            try:
                image.save(DEBUG_IMAGE_PATH)
            except OSError:
                pass

            started = time.time()
            tokenizer, image_processor, model = self._load_trocr()
            lines = handwrite_ocr.split_lines(image)
            text = handwrite_ocr.read_lines(
                lines,
                tokenizer,
                image_processor,
                model,
                batched=bool(self.settings['batch_lines']),
            )
            elapsed = time.time() - started
            if text:
                injector = handwrite_inject.inject_text(text, self.settings['injector'])
                injector = injector or 'FAILED'
            else:
                injector = 'nothing'
            print(
                f"OCR: {len(strokes)} stroke(s) -> image {image.size}, "
                f"{len(lines)} line(s) {[line.size for line in lines]} "
                f"in {elapsed:.1f}s: {text!r} [{injector}]",
                file=sys.stderr,
            )
            self._set_status(f'{len(lines)} line(s) in {elapsed:.1f}s')
            # Only mark the ink as read once it has been typed, so a failed
            # recognition is retried rather than silently dropped — and only
            # while that ink still exists (the overlay may have been cleared).
            if generation == self.overlay.generation:
                self.overlay.consumed = max(self.overlay.consumed, total)
                advanced = True
        except Exception as e:
            print(f"Recognition error: {e}", file=sys.stderr)
            self._set_status('recognition failed')
        finally:
            self._busy = False
            # Ink written while the model was busy was skipped by the debounce;
            # pick it up now rather than waiting for the next stroke.
            if advanced and self.overlay.consumed < len(self.overlay.strokes):
                GLib.timeout_add(200, self._recognize_again)

    def _recognize_again(self):
        self.recognize()
        return False


def main():
    daemon = HandwriteDaemon()
    daemon.start()


if __name__ == '__main__':
    main()
