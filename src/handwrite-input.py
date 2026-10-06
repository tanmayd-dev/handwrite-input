#!/usr/bin/env python3
"""
Handwrite Input — system-wide handwriting recognition for Wayland.

A daemon that shows a transparent overlay where you can write with a pen.
After a debounce period, the handwriting is recognized with TrOCR
(microsoft/trocr-large-handwritten, ~2.2 GB, downloaded on first use)
and typed into the focused application via wtype.

Uses gtk-layer-shell for proper Hyprland overlay support.
"""

import os
import sys
import socket
import subprocess
import signal
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GtkLayerShell', '0.1')
from gi.repository import Gtk, Gdk, GLib, GtkLayerShell

import cairo
from transformers import RobertaTokenizer, ViTImageProcessor, VisionEncoderDecoderModel

import handwrite_ocr

SOCKET_DIR = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp'))
SOCKET_PATH = SOCKET_DIR / 'handwrite-input.sock'

DEBUG_IMAGE_PATH = '/tmp/handwrite-debug.png'

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

        # Button bar
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

        vbox.pack_start(hbox, False, False, 0)

        self.window.add(vbox)
        self.window.connect('delete-event', lambda w, e: self.hide())
        self.window.show_all()

    def hide(self):
        if self.window is not None:
            self.window.hide()

    def clear(self):
        self.strokes = []
        self.current_stroke = []
        self.is_drawing = False
        self.consumed = 0
        self.drawing_area.queue_draw()

    def force_reset(self):
        self.strokes = []
        self.current_stroke = []
        self.is_drawing = False
        self.consumed = 0
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
        cr.set_line_width(LINE_WIDTH)
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
        self.debounce_id = GLib.timeout_add(DEFAULT_DEBOUNCE_MS, self.on_debounce)

    def on_debounce(self):
        self.debounce_id = None
        self.daemon.recognize()
        return False


class HandwriteDaemon:
    """The main daemon that manages the overlay and IPC."""

    def __init__(self):
        self.overlay = HandwriteOverlay(self)
        self.sock = None
        self.visible = False
        self._trocr = None

    def _load_trocr(self):
        if self._trocr is None:
            # Slow Roberta tokenizer — AutoTokenizer in transformers 5.x tries
            # (and fails) to convert this to a fast tokenizer.
            tokenizer = RobertaTokenizer.from_pretrained(
                'microsoft/trocr-large-handwritten'
            )
            image_processor = ViTImageProcessor.from_pretrained(
                'microsoft/trocr-large-handwritten'
            )
            model = VisionEncoderDecoderModel.from_pretrained(
                'microsoft/trocr-large-handwritten'
            )
            model.eval()
            self._trocr = (tokenizer, image_processor, model)
        return self._trocr

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
            data = conn.recv(1024).decode().strip()
            conn.close()

            if data == 'toggle':
                self.toggle()
            elif data == 'show':
                self.show()
            elif data == 'hide':
                self.hide()
            elif data == 'clear':
                self.overlay.clear()
            elif data == 'force_reset':
                self.overlay.force_reset()
            elif data == 'quit':
                self.cleanup()
                Gtk.main_quit()
        except Exception as e:
            print(f"Error handling client: {e}", file=sys.stderr)
        return True

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
        if not self.overlay.strokes:
            return

        strokes = self.overlay.strokes[self.overlay.consumed:]
        if not strokes:
            return

        image = handwrite_ocr.build_ink_image(strokes, line_width=LINE_WIDTH)
        if image is None:
            return

        # Keep the exact image handed to the model, for debugging.
        try:
            image.save(DEBUG_IMAGE_PATH)
        except OSError:
            pass

        try:
            tokenizer, image_processor, model = self._load_trocr()
            lines = handwrite_ocr.split_lines(image)
            text = handwrite_ocr.read_lines(lines, tokenizer, image_processor, model)
            if text:
                # Type via wtype
                subprocess.run(['wtype', text], timeout=5)
            # Only mark the ink as read once it has been typed, so a failed
            # recognition is retried rather than silently dropped.
            self.overlay.consumed = len(self.overlay.strokes)
        except Exception as e:
            print(f"Recognition error: {e}", file=sys.stderr)


def main():
    daemon = HandwriteDaemon()
    daemon.start()


if __name__ == '__main__':
    main()
