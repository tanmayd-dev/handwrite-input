"""Experimental online handwriting recognition via Google Input Tools.

This is the free, undocumented endpoint used by Google's handwriting demo. It
takes **raw pen strokes**, not images, and is reasonably good at normal English
words. It is not a supported Google API and can disappear or rate-limit at any
time.

Only strokes passed to :func:`recognize_groups` are uploaded. There is no local
recognition fallback.
"""

import json
import math
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ENDPOINT = (
    'https://inputtools.google.com/request?itc=en-t-i0-handwrit&app=demopage'
)
REQUEST_TIMEOUT = 15

# A tiny "v" sent by check_endpoint(): enough to exercise the real request
# path (bbox, ink normalisation, JSON payload, response parsing) without
# uploading anything the user wrote.
CHECK_STROKES = [[(0.0, 0.0, 1.0), (12.0, 20.0, 1.0), (24.0, 0.0, 1.0)]]


class CheckFailed(RuntimeError):
    """The recognizer endpoint could not be reached or answered unusably."""


def _points(stroke):
    return [point for point in stroke if len(point) >= 2]


def bbox(strokes):
    points = [point for stroke in strokes for point in _points(stroke)]
    if not points:
        raise ValueError('empty stroke group')
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _median(values):
    values = sorted(values)
    if not values:
        return 0.0
    return values[len(values) // 2]


def group_lines(strokes):
    """Group strokes into visual lines using pen movement.

    A new line is assumed when the pen jumps back to the left *and* the next
    stroke's centre is clearly below the current line's top. Using the stroke
    centre rather than its first point avoids treating a tall ascender at the
    start of the next line as part of the previous line.
    """
    strokes = [stroke for stroke in strokes if len(_points(stroke)) >= 2]
    if not strokes:
        return []

    x0, y0, x1, y1 = bbox(strokes)
    page_width = max(1.0, x1 - x0)
    groups = []
    current = []

    for stroke in strokes:
        points = _points(stroke)
        if current:
            cur_x0, cur_y0, cur_x1, cur_y1 = bbox(current)
            line_height = max(1.0, cur_y1 - cur_y0)
            start_x = min(point[0] for point in points)
            centre_y = _median([point[1] for point in points])
            previous_end_x = max(point[0] for point in _points(current[-1]))
            back_left = start_x < previous_end_x - 0.15 * page_width
            below = centre_y > cur_y0 + 0.55 * line_height
            if back_left and below:
                groups.append(current)
                current = []
        current.append(stroke)

    if current:
        groups.append(current)

    # Pen order is normally top-to-bottom, but sorting makes the result stable
    # if the writer went back to add a word above an earlier line.
    groups.sort(key=lambda group: bbox(group)[1])
    return groups


def _terms(lexicon):
    if isinstance(lexicon, str):
        raw = re.split(r'[\s,]+', lexicon)
    else:
        raw = list(lexicon or ())
    return [str(term).strip().lower() for term in raw if str(term).strip()]


def _term_pattern(term):
    return re.compile(
        r'(?<![a-z0-9_])' + re.escape(term) + r'(?![a-z0-9_])',
        re.IGNORECASE,
    )


def _split_term_pattern(term):
    characters = (re.escape(character) for character in term)
    return re.compile(
        r'(?<![a-z0-9_])' + r'\s*'.join(characters) + r'(?![a-z0-9_])',
        re.IGNORECASE,
    )


def _term_hit(text, term):
    if _term_pattern(term).search(text):
        return True
    # Also accept the term when the recogniser inserted word gaps, e.g.
    # "way bar" for "waybar". Do not count an ordinary word such as "dwelling"
    # merely because it starts with "dwl".
    for match in _split_term_pattern(term).finditer(text):
        if any(character.isspace() for character in match.group(0)):
            return True
    return False


def _choose_candidate(candidates, lexicon):
    """Prefer a candidate containing the user's technical vocabulary.

    Google often returns several plausible readings (for example
    "window in owl" vs "window in dwl"). A small project lexicon lets the
    correct technical term win without building a general language model.
    Ties still go to Google's first candidate.
    """
    if not candidates:
        return ''
    terms = _terms(lexicon)
    if not terms:
        return candidates[0]

    def score(candidate):
        return sum(1 for term in terms if _term_hit(candidate.lower(), term))

    best_index = 0
    best_score = score(candidates[0])
    for index, candidate in enumerate(candidates[1:], 1):
        candidate_score = score(candidate)
        if candidate_score > best_score:
            best_index = index
            best_score = candidate_score
    return candidates[best_index]


def _normalise_terms(text, lexicon):
    """Collapse split forms of configured terms, e.g. "way bar" -> "waybar".

    Only gaps between the characters of a known term are affected; normal
    prose is left alone. Exact occurrences keep their original capitalisation.
    """
    for term in _terms(lexicon):
        pattern = re.compile(
            r'(?<![a-z0-9_])'
            + (r'\s*'.join(re.escape(character) for character in term))
            + r'(?![a-z0-9_])',
            re.IGNORECASE,
        )

        def replace(match):
            if match.group(0).lower() == term:
                return match.group(0)
            return term

        text = pattern.sub(replace, text)
    return text


def _request_group(strokes, language='en', timeout=REQUEST_TIMEOUT):
    x0, y0, x1, y1 = bbox(strokes)
    width = max(1, int(math.ceil(x1 - x0 + 20)))
    height = max(1, int(math.ceil(y1 - y0 + 20)))

    ink = []
    clock = 0
    for stroke in strokes:
        points = _points(stroke)
        xs = [int(round(point[0] - x0 + 10)) for point in points]
        ys = [int(round(point[1] - y0 + 10)) for point in points]
        times = []
        for point in points:
            # The daemon currently records (x, y, pressure). If a timestamp is
            # ever added, use it; otherwise make a monotonically increasing
            # clock because the endpoint expects stroke timing.
            if len(point) > 3 and isinstance(point[3], (int, float)):
                times.append(int(point[3]))
            else:
                clock += 16
                times.append(clock)
        ink.append([xs, ys, times])

    payload = {
        'app_version': 0.4,
        'api_level': '537.36',
        'device': 'handwrite-input',
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
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode())

    if not isinstance(result, list) or len(result) < 2 or result[0] != 'SUCCESS':
        raise RuntimeError(f'unexpected Google response: {result!r}')
    entries = result[1]
    if not entries:
        return ''
    entry = entries[0]
    if not isinstance(entry, list) or len(entry) < 2 or not entry[1]:
        return ''
    return [str(candidate) for candidate in entry[1]]


def _parse_path_terms(path_terms):
    pairs = []
    for item in re.split(r'[\n,]+', str(path_terms or '')):
        source, separator, target = item.partition('=')
        source = source.strip()
        target = target.strip()
        if separator and source and target:
            pairs.append((source, target))
    return pairs


def _apply_path_terms(text, path_terms):
    """Replace words directly after "in" with configured path spellings."""
    for source, target in _parse_path_terms(path_terms):
        pattern = re.compile(
            r'(?<![a-z0-9_])(in)\s+'
            + re.escape(source)
            + r'(?![a-z0-9_])',
            re.IGNORECASE,
        )
        text = pattern.sub(lambda match: f'{match.group(1)} {target}', text)
    return text


def recognize_groups(groups, lexicon='', path_terms='', language='en'):
    """Recognise stroke groups and return ``(text, line_count)``.

    Requests are sent concurrently: the endpoint has one request per line, and
    doing five in series added about a second of avoidable latency before the
    text appeared. Results are consumed in line order.
    """
    groups = [group for group in groups if group]
    if not groups:
        return '', 0

    parts = []
    workers = min(5, len(groups))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_request_group, group, language) for group in groups
        ]
        for future in futures:
            candidates = future.result()
            text = _choose_candidate(candidates, lexicon)
            text = _normalise_terms(text, lexicon)
            text = _apply_path_terms(text, path_terms)
            text = ' '.join(text.split())
            if text:
                parts.append(text)
    return ' '.join(parts), len(groups)


def check_endpoint(timeout=REQUEST_TIMEOUT):
    """Probe the recognizer with a tiny stroke; return its candidates.

    Raises :class:`CheckFailed` with a message that can be shown to the user
    when the endpoint is unreachable, rejects the request, or stops answering
    in the shape the recognizer expects.
    """
    try:
        return _request_group(CHECK_STROKES, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise CheckFailed(f'HTTP {exc.code} {exc.reason} from {ENDPOINT}') from exc
    except urllib.error.URLError as exc:
        raise CheckFailed(f'cannot reach {ENDPOINT}: {exc.reason}') from exc
    except (OSError, ValueError) as exc:
        # Timeouts, resets and unparseable bodies all land here.
        raise CheckFailed(f'cannot reach {ENDPOINT}: {exc}') from exc
    except RuntimeError as exc:
        raise CheckFailed(str(exc)) from exc


def check_cli(program='handwrite-input', timeout=REQUEST_TIMEOUT):
    """Print an endpoint report; return a process exit code (0 = usable)."""
    print(f'{program}: checking the recognition endpoint')
    print(f'  endpoint: {ENDPOINT}')
    try:
        candidates = check_endpoint(timeout=timeout)
    except CheckFailed as exc:
        print('  result:   FAILED')
        print(f'  reason:   {exc}')
        print(
            'Google Input Tools is the only recognizer; there is no offline '
            'fallback, so handwriting will not be recognized until the '
            'endpoint is reachable again.',
            file=sys.stderr,
        )
        return 1
    sample = candidates[0] if candidates else '(no candidates)'
    print(f'  result:   reachable ({len(candidates)} candidate(s), e.g. {sample!r})')
    print(
        '  note:     undocumented and rate-limited; it may change or disappear '
        'without notice, and only works online.'
    )
    return 0
