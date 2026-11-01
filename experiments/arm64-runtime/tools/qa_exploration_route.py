"""Offline validation for the existing runtime_pad recording and frame CSV.

No game launch, cache mutation, visual capture or runtime instrumentation.
Movement means stick input; it does not by itself prove a map/streaming boundary.
"""
from pathlib import Path
import bisect
import math
import statistics

MAX_BYTES = 2 * 1024 * 1024


def pad_samples(path):
    path = Path(path)
    if not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError('bounded nonempty route required')
    rows = []
    for line in path.read_text().splitlines():
        values = tuple(int(x) for x in line.split())
        if (len(values) != 8 or not 0 <= values[0] <= 120000
                or not 0 <= values[1] <= 0xffffffff
                or any(not 0 <= x <= 255 for x in values[2:])):
            raise ValueError('invalid existing pad recording')
        if rows and values[0] < rows[-1][0]:
            raise ValueError('route clock moved backwards')
        rows.append(values)
    if len(rows) < 2 or rows[0][0] > 100 or not 5000 <= rows[-1][0] <= 120000:
        raise ValueError('route duration/start is not qualified')
    if rows[-1][1] or any(abs(x - 128) > 8 for x in rows[-1][2:6]) or any(rows[-1][6:]):
        raise ValueError('route must end with released controls')
    return rows


def input_phases(samples):
    result = []
    for row, following in zip(samples, samples[1:]):
        moving = any(abs(x - 128) > 24 for x in row[2:4])
        turning = any(abs(x - 128) > 24 for x in row[4:6])
        kind = 'walking-and-turning' if moving and turning else 'walking' if moving else 'turning' if turning else 'neutral'
        start, end = row[0] / 1000, following[0] / 1000
        if end == start:
            continue
        if result and result[-1]['kind'] == kind and result[-1]['end'] == start:
            result[-1]['end'] = end
        else:
            result.append(dict(kind=kind, start=start, end=end))
    return result


def interval(rows, start, end):
    """Use original frame times, retaining stalls even without a camera sample."""
    if not all(math.isfinite(x) for x in (start, end)) or end <= start:
        raise ValueError('invalid actual frame-clock interval')
    selected = []; previous = None
    for row in rows:
        t, ms = float(row['t_s']), float(row['frame_ms'])
        if not all(math.isfinite(x) for x in (t, ms)) or ms < 0:
            raise ValueError('invalid frame clock')
        if previous is not None and t < previous:
            raise ValueError('frame clock moved backwards')
        previous = t
        if start < t <= end and ms > 0:
            selected.append(row)
    if not selected:
        raise ValueError('no real route frames')
    times = sorted(float(row['frame_ms']) for row in selected)
    result = dict(frames=len(times), fps=len(times)/(end-start),
                  p95_ms=times[int((len(times)-1)*.95)], p99_ms=times[int((len(times)-1)*.99)],
                  max_frame_ms=times[-1],
                  long_frames={str(bound): sum(ms > bound for ms in times) for bound in (33.333, 50, 100, 200)},
                  above_50ms_excess_ms=sum(max(0, ms-50) for ms in times))
    for name in ('compiles', 'metal_source_calls', 'metal_local_calls', 'metal_strict_pso_hits',
                 'metal_source_api_ms', 'metal_pso_api_ms'):
        if all(name in row for row in selected):
            result[name] = sum(float(row[name]) for row in selected)
    return result


def camera_path(rows, start, end):
    points = []
    for row in rows:
        t = float(row['t_s'])
        if start <= t <= end:
            try:
                xyz = tuple(float(row[k]) for k in ('cam_x', 'cam_y', 'cam_z'))
            except (ValueError, KeyError):
                continue
            if all(math.isfinite(x) for x in xyz):
                points.append((t-start, xyz))
    if len(points) < 2 or points[0][0] > .5 or end-start-points[-1][0] > .5:
        raise ValueError('camera does not cover the route')
    if any(b[0]-a[0] > .5 for a, b in zip(points, points[1:])):
        raise ValueError('camera gap prevents route equivalence proof')
    return points


def match_paths(a, b, duration, *, horizontal=1.5, vertical=.6):
    """Time-aligned camera corridor; a fall/collision drift invalidates a pair."""
    def at(path, t):
        keys = [p[0] for p in path]
        i = min(max(bisect.bisect_right(keys, t)-1, 0), len(path)-2)
        ta, pa = path[i]; tb, pb = path[i+1]
        if tb <= ta:
            raise ValueError('camera clock is not strictly increasing')
        weight = min(1., max(0., (t-ta)/(tb-ta)))
        return tuple(x+(y-x)*weight for x, y in zip(pa, pb))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('invalid paired route duration')
    distances = []; heights = []
    for i in range(129):
        pa, pb = at(a, duration*i/128), at(b, duration*i/128)
        distances.append(math.hypot(pa[0]-pb[0], pa[2]-pb[2]))
        heights.append(abs(pa[1]-pb[1]))
    return dict(qualified=max(distances)<=horizontal and max(heights)<=vertical,
                max_horizontal=max(distances), max_vertical=max(heights),
                rms_horizontal=math.sqrt(statistics.mean(x*x for x in distances)),
                horizontal_limit=horizontal, vertical_limit=vertical,
                player_and_map_identity_claim=False)
