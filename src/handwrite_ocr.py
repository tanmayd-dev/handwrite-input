"""OCR helpers for handwrite-input.

Turns pen strokes into black-on-white images and reads them with TrOCR.

TrOCR is a *single text line* model: giving it two lines at once (or a line
whose baseline wanders) makes it emit garbage, e.g. "12345637" or "This best"
instead of "this is a test". So the strokes are rendered once, split into text
lines by the gaps in the horizontal ink profile, and each line is read on its
own.

Deliberately free of GTK/Wayland imports so it can be exercised headlessly.
"""

import io

import cairo
import numpy as np
from PIL import Image

DEFAULT_LINE_WIDTH = 15.0
SCALE = 3
PADDING = 40

# A band of ink is only treated as a text line if it carries this much ink and
# is at least this dense; a lone stray point would otherwise become a "line".
MIN_LINE_INK = 200
MIN_LINE_DENSITY = 0.004


def build_ink_image(strokes, scale=SCALE, padding=PADDING, line_width=DEFAULT_LINE_WIDTH):
    """Render strokes as black-on-white and return a PIL image (or None).

    `strokes` is a list of strokes; each stroke is a list of (x, y, ...) tuples.
    """
    points = [(p[0], p[1]) for stroke in strokes for p in stroke]
    if not points:
        return None

    min_x = max(0, min(p[0] for p in points) - padding)
    min_y = max(0, min(p[1] for p in points) - padding)
    max_x = max(p[0] for p in points) + padding
    max_y = max(p[1] for p in points) + padding

    width = int(max_x - min_x)
    height = int(max_y - min_y)
    if width < 10 or height < 10:
        return None

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width * scale, height * scale)
    cr = cairo.Context(surface)
    cr.set_source_rgba(1, 1, 1, 1)
    cr.paint()
    cr.scale(scale, scale)
    cr.translate(-min_x, -min_y)
    cr.set_source_rgba(0, 0, 0, 0.8)
    cr.set_line_width(line_width)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)

    for stroke in strokes:
        if len(stroke) < 2:
            continue
        cr.move_to(stroke[0][0], stroke[0][1])
        for point in stroke[1:]:
            cr.line_to(point[0], point[1])
        cr.stroke()

    return surface_to_image(surface)


def surface_to_image(surface):
    """Convert a cairo surface to a PIL image via an in-memory PNG."""
    buf = io.BytesIO()
    surface.write_to_png(buf)
    buf.seek(0)
    with Image.open(buf) as im:
        return im.convert('RGB')


def split_lines(image, gap_ratio=1 / 60, min_ink=MIN_LINE_INK, min_density=MIN_LINE_DENSITY):
    """Split a black-on-white ink image into one image per text line.

    Lines are separated by a gap in the horizontal ink profile at least
    `gap_ratio` of the image height. Returns a list of cropped PIL images.
    """
    grey = np.asarray(image.convert('L'))
    ink = grey < 128
    if not ink.any():
        return []

    profile = ink.sum(axis=1)
    height = grey.shape[0]
    min_gap = max(8, int(height * gap_ratio))

    bands = []
    start = None
    gap = 0
    for y, count in enumerate(profile):
        if count:
            if start is None:
                start = y
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                bands.append((start, y - gap))
                start = None
    if start is not None:
        bands.append((start, height - 1))

    lines = []
    for y0, y1 in bands:
        if y1 <= y0:
            continue
        band = ink[y0:y1 + 1]
        count = int(band.sum())
        if count < min_ink or count / band.size < min_density:
            continue
        columns = np.where(band.any(axis=0))[0]
        x0, x1 = int(columns.min()), int(columns.max())
        # TrOCR reads tight crops best: padding of a quarter of the line height
        # or more measurably degrades the output ("test" -> "to est").
        pad = max(2, min(10, (y1 - y0) // 16))
        lines.append(
            image.crop(
                (
                    max(0, x0 - pad),
                    max(0, y0 - pad),
                    min(image.width, x1 + pad + 1),
                    min(image.height, y1 + pad + 1),
                )
            )
        )
    return lines


def tidy_line(text):
    """Join a line the model spaced out character by character ("t e s t")."""
    tokens = text.split()
    if len(tokens) >= 3 and all(len(token) == 1 for token in tokens):
        return ''.join(tokens)
    return text


def read_lines(lines, tokenizer, image_processor, model, max_new_tokens=64, batched=False):
    """Read each line image with TrOCR and join the results with spaces.

    With `batched`, all lines go through the model in a single call, which
    removes per-call overhead on CPU. The image processor pads the batch to the
    widest line, so a short line shares the batch width of the longest one.
    """
    import torch  # only needed for recognition, not for rendering/splitting

    lines = list(lines)
    if not lines:
        return ''

    with torch.inference_mode():
        if batched:
            pixel_values = image_processor(images=lines, return_tensors='pt').pixel_values
            generated_ids = model.generate(pixel_values, max_new_tokens=max_new_tokens)
            texts = tokenizer.batch_decode(generated_ids, skip_special_tokens=True)
        else:
            texts = []
            for line in lines:
                pixel_values = image_processor(images=line, return_tensors='pt').pixel_values
                generated_ids = model.generate(pixel_values, max_new_tokens=max_new_tokens)
                texts.append(tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0])

    parts = [tidy_line(text.strip()) for text in texts]
    return ' '.join(part for part in parts if part)
