"""Non-destructive time exclusions, expressed along cumulative active time."""
from datetime import datetime, timedelta
import math


def union(ranges):
    result = []
    for start, end in sorted(ranges):
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
            raise ValueError('Некорректный интервал поправки.')
        if result and start <= result[-1][1] + 1e-7:
            result[-1][1] = max(end, result[-1][1])
        else:
            result.append([float(start), float(end)])
    return result


def subtract(ranges, excluded):
    pieces = [list(item) for item in ranges]
    for left, right in union(excluded):
        next_pieces = []
        for start, end in pieces:
            if right <= start or left >= end:
                next_pieces.append([start, end])
            else:
                if start < left:
                    next_pieces.append([start, left])
                if right < end:
                    next_pieces.append([right, end])
        pieces = next_pieces
    return pieces


def duration(ranges):
    return sum(end - start for start, end in union(ranges)) if ranges else 0.0


def remove_latest(total, seconds, excluded=()):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('Укажите положительную длительность.')
    available = subtract([[0, total]], excluded) if total else []
    if seconds > duration(available) + 1e-6:
        raise ValueError('Поправка больше учтённого времени.')
    ranges, rest = [], seconds
    for start, end in reversed(available):
        take = min(rest, end - start)
        if take > 0:
            ranges.append([end - take, end])
            rest -= take
        if rest <= 1e-6:
            break
    return union(ranges)


def wall_range(record, start, end):
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError('Укажите начало и конец перерыва.')
    offset, ranges = 0.0, []
    for interval in record['intervals']:
        left, right = datetime.fromisoformat(interval['start']), datetime.fromisoformat(interval['end'])
        seconds = interval['seconds']
        span = (right - left).total_seconds()
        if span > 0:
            a, b = max(start, left), min(end, right)
            if b > a:
                ranges.append([offset + seconds * (a-left).total_seconds()/span,
                               offset + seconds * (b-left).total_seconds()/span])
        offset += seconds
    return union(ranges)


def adjusted_record(record, excluded):
    """Preserve original intervals; return proportional retained wall-time slices."""
    excluded = union(excluded)
    offset, intervals = 0.0, []
    for interval in record['intervals']:
        seconds = interval['seconds']
        if seconds <= 0:
            continue
        left, right = datetime.fromisoformat(interval['start']), datetime.fromisoformat(interval['end'])
        span = right - left
        for a, b in subtract([[offset, offset+seconds]], excluded):
            start = left + span * ((a-offset)/seconds)
            end = left + span * ((b-offset)/seconds)
            intervals.append({'start': start.isoformat(), 'end': end.isoformat(), 'seconds': b-a})
        offset += seconds
    effective = max(0.0, record['active_seconds'] - duration(excluded))
    return {**record, 'active_seconds': effective, 'intervals': intervals}
