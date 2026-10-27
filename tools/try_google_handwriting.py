#!/usr/bin/env python3
"""One-off comparison with Google Input Tools' handwriting endpoint.

This is a standalone diagnostic for the endpoint used by the daemon. It is
undocumented, online-only, and may change or rate-limit without notice.

Capture a sample with the daemon running as:

    HANDWRITE_DUMP_STROKES=1 handwrite-input

then write something and press Done. The strokes land in
/tmp/handwrite-strokes.json. Run:

    python tools/try_google_handwriting.py

By default strokes are split into visual lines using the same "large jump
back to the left" heuristic that handwriting naturally produces; use
--no-group to send every stroke in one request.
"""

import argparse
import json
import urllib.request

ENDPOINT = (
    'https://inputtools.google.com/request?itc=en-t-i0-handwrit&app=demopage'
)


def bbox(strokes):
    points = [p for stroke in strokes for p in stroke]
    return (
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    )


def group_lines(strokes):
    """Split strokes where the pen jumps back left and down to a new line."""
    strokes = [s for s in strokes if len(s) >= 2]
    if not strokes:
        return []
    x0, y0, x1, y1 = bbox(strokes)
    page_width = max(1.0, x1 - x0)
    groups = []
    current = []
    for stroke in strokes:
        if current:
            cur_x0, cur_y0, cur_x1, cur_y1 = bbox(current)
            sx, sy = stroke[0][0], stroke[0][1]
            prev_end_x = current[-1][-1][0]
            line_height = max(1.0, cur_y1 - cur_y0)
            back_left = sx < prev_end_x - 0.15 * page_width
            below = sy > cur_y0 + 0.45 * line_height
            if back_left and below:
                groups.append(current)
                current = []
        current.append(stroke)
    if current:
        groups.append(current)
    return groups


def request_group(strokes, language='en'):
    x0, y0, x1, y1 = bbox(strokes)
    width = max(1, int(x1 - x0 + 20))
    height = max(1, int(y1 - y0 + 20))
    ink = []
    clock = 0
    for stroke in strokes:
        xs = [int(p[0] - x0 + 10) for p in stroke]
        ys = [int(p[1] - y0 + 10) for p in stroke]
        ts = []
        for point in stroke:
            # Prefer the pen timestamp if the dump has one, otherwise just
            # provide a monotonically increasing clock.
            if len(point) > 3 and isinstance(point[3], (int, float)):
                ts.append(int(point[3]))
            elif len(point) > 2 and isinstance(point[2], (int, float)):
                clock += 16
                ts.append(clock)
            else:
                clock += 16
                ts.append(clock)
        ink.append([xs, ys, ts])

    payload = {
        'app_version': 0.4,
        'api_level': '537.36',
        'device': 'handwrite-input comparison',
        'input_type': 0,
        'options': 'enable_pre_space',
        'requests': [
            {
                'writing_guide': {
                    'writing_area_width': width,
                    'writing_area_height': height,
                },
                'language': language,
                'ink': ink,
                'max_num_results': 10,
                'max_completions': 0,
            }
        ],
    }
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--strokes',
        default='/tmp/handwrite-strokes.json',
        help='stroke dump written by the daemon',
    )
    parser.add_argument('--no-group', action='store_true', help='send all strokes at once')
    parser.add_argument('--language', default='en')
    args = parser.parse_args()

    with open(args.strokes) as f:
        strokes = json.load(f)
    groups = [strokes] if args.no_group else group_lines(strokes)
    print(f'{len(strokes)} strokes, {len(groups)} request(s)')

    for index, group in enumerate(groups, 1):
        x0, y0, x1, y1 = bbox(group)
        print(f'line {index}: {len(group)} strokes, bbox ({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f})')
        try:
            result = request_group(group, args.language)
        except Exception as exc:
            print(f'  request failed: {exc}')
            continue
        print(f'  {result!r}')


if __name__ == '__main__':
    main()
