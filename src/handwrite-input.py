#!/usr/bin/env python3
"""
Handwrite Input — system-wide handwriting recognition for Wayland.

A daemon that shows a transparent overlay where you can write with a pen.
After a debounce period, the handwriting is recognized via Tesseract OCR
and typed into the focused application via wtype.

Uses gtk-layer-shell for proper Hyprland overlay support.
"""

import os
import sys
import socket
import subprocess
import tempfile
import signal
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GtkLayerShell', '0.1')
from gi.repository import Gtk, Gdk, GLib, GtkLayerShell

import cairo

SOCKET_DIR = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp'))
SOCKET_PATH = SOCKET_DIR / 'handwrite-input.sock'

DEFAULT_WIDTH = 600
DEFAULT_HEIGHT = 300
DEFAULT_DEBOUNCE_MS = 1500
DEFAULT_LANGUAGE = 'eng'


class HandwriteOverlay:
    """The transparent overlay window for handwriting input."""

    def __init__(self, daemon):
        self.daemon = daemon
        self.window = None
        self.surface = None
        self.context = None
        self.strokes = []
        self.current_stroke = []
        self.is_drawing = False
        self.debounce_id = None

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
        self.drawing_area.connect('size-allocate', self.on_size_allocate)
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
        if self.context is not None:
            self.context.set_source_rgba(1, 1, 1, 1)
            self.context.paint()
        self.drawing_area.queue_draw()

    def force_reset(self):
        self.strokes = []
        self.current_stroke = []
        self.is_drawing = False
        if self.debounce_id is not None:
            GLib.source_remove(self.debounce_id)
            self.debounce_id = None
        if self.context is not None:
            self.context.set_source_rgba(1, 1, 1, 1)
            self.context.paint()
        self.drawing_area.queue_draw()

    def finish(self):
        self.daemon.recognize()
        self.clear()
        self.hide()

    def on_size_allocate(self, widget, allocation):
        if self.surface is None or self.surface.get_width() != allocation.width or self.surface.get_height() != allocation.height:
            self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, allocation.width, allocation.height)
            self.context = cairo.Context(self.surface)
            self.context.set_source_rgba(1, 1, 1, 1)
            self.context.paint()

    def on_draw(self, widget, cr):
        if self.surface:
            cr.set_source_surface(self.surface, 0, 0)
            cr.paint()

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
        self.draw_last_segment()
        return True

    def draw_last_segment(self):
        if self.context is None:
            return
        if len(self.current_stroke) < 2:
            return
        x0, y0, p0 = self.current_stroke[-2]
        x1, y1, p1 = self.current_stroke[-1]

        self.context.set_source_rgba(0, 0, 0, 0.8)
        self.context.set_line_width(8.0)
        self.context.set_line_cap(cairo.LINE_CAP_ROUND)
        self.context.move_to(x0, y0)
        self.context.line_to(x1, y1)
        self.context.stroke()

        self.drawing_area.queue_draw()

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
        if self.overlay.surface is None:
            return

        # Calculate bounding box of all strokes
        all_points = []
        for stroke in self.overlay.strokes:
            all_points.extend([(p[0], p[1]) for p in stroke])

        if not all_points:
            return

        min_x = min(p[0] for p in all_points)
        min_y = min(p[1] for p in all_points)
        max_x = max(p[0] for p in all_points)
        max_y = max(p[1] for p in all_points)

        # Add padding
        padding = 40
        min_x = max(0, min_x - padding)
        min_y = max(0, min_y - padding)
        max_x = min(self.overlay.surface.get_width(), max_x + padding)
        max_y = min(self.overlay.surface.get_height(), max_y + padding)

        width = int(max_x - min_x)
        height = int(max_y - min_y)

        if width < 10 or height < 10:
            return

        # Create cropped surface
        cropped = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        cropped_ctx = cairo.Context(cropped)
        cropped_ctx.set_source_surface(self.overlay.surface, -min_x, -min_y)
        cropped_ctx.paint()

        # Scale up 2x for better OCR
        scaled = cairo.ImageSurface(cairo.FORMAT_ARGB32, width * 2, height * 2)
        scaled_ctx = cairo.Context(scaled)
        scaled_ctx.scale(2, 2)
        scaled_ctx.set_source_surface(cropped, 0, 0)
        scaled_ctx.paint()

        # Save to PNG
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            scaled.write_to_png(f.name)
            png_path = f.name

        try:
            # Run tesseract with PSM 7 (single text line) and LSTM engine
            result = subprocess.run(
                ['tesseract', png_path, 'stdout', '-l', DEFAULT_LANGUAGE, '--psm', '7', '--oem', '1'],
                capture_output=True, text=True, timeout=10
            )
            text = result.stdout.strip()

            if text:
                # Type via wtype
                subprocess.run(['wtype', text], timeout=5)
        except Exception as e:
            print(f"Recognition error: {e}", file=sys.stderr)
        finally:
            os.unlink(png_path)


def main():
    daemon = HandwriteDaemon()
    daemon.start()


if __name__ == '__main__':
    main()
