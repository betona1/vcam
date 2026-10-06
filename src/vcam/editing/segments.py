"""구간 계산: 정리·여집합(구간 제거)·나누기 지점·키프레임 맞춤(빠른 모드). 모든 시간은 초."""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

MIN_SEGMENT = 0.05  # 이보다 짧은 구간은 버린다


@dataclass(frozen=True, order=True)
class Segment:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def clamp(self, duration: float) -> Segment:
        return Segment(min(max(0.0, self.start), duration), min(max(0.0, self.end), duration))


def normalize(segments: Iterable[Segment], duration: float) -> list[Segment]:
    """영상 길이 안으로 자르고, 정렬하고, 겹치거나 맞닿은 구간을 합친다."""
    cleaned = sorted(s.clamp(duration) for s in segments)
    merged: list[Segment] = []
    for seg in cleaned:
        if seg.duration < MIN_SEGMENT:
            continue
        if merged and seg.start <= merged[-1].end + 1e-6:
            merged[-1] = Segment(merged[-1].start, max(merged[-1].end, seg.end))
        else:
            merged.append(seg)
    return merged


def complement(remove: Iterable[Segment], duration: float) -> list[Segment]:
    """지울 구간을 뺀 나머지(남길 구간)."""
    keep: list[Segment] = []
    cursor = 0.0
    for seg in normalize(remove, duration):
        if seg.start - cursor >= MIN_SEGMENT:
            keep.append(Segment(cursor, seg.start))
        cursor = seg.end
    if duration - cursor >= MIN_SEGMENT:
        keep.append(Segment(cursor, duration))
    return keep


def split_at(points: Iterable[float], duration: float) -> list[Segment]:
    """지점들에서 나눈 연속 구간들."""
    cuts = sorted({round(p, 3) for p in points if MIN_SEGMENT <= p <= duration - MIN_SEGMENT})
    edges = [0.0, *cuts, duration]
    return [Segment(a, b) for a, b in zip(edges, edges[1:], strict=False) if b - a >= MIN_SEGMENT]


def split_equal(parts: int, duration: float) -> list[Segment]:
    parts = max(1, int(parts))
    return split_at([duration * i / parts for i in range(1, parts)], duration)


def split_every(seconds: float, duration: float) -> list[Segment]:
    if seconds <= 0:
        return [Segment(0.0, duration)]
    count = int(duration // seconds)
    return split_at([seconds * i for i in range(1, count + 1)], duration)


def snap_to_keyframes(segments: Sequence[Segment], keyframes: Sequence[float]) -> list[Segment]:
    """빠른(무손실) 모드: 시작점을 그 앞의 키프레임으로 당긴다. 복사 방식은 키프레임에서만 시작할 수 있다."""
    if not keyframes:
        return list(segments)
    keys = sorted(keyframes)
    snapped = []
    for seg in segments:
        i = bisect.bisect_right(keys, seg.start + 1e-3) - 1
        start = keys[i] if i >= 0 else 0.0
        snapped.append(Segment(start, seg.end))
    return snapped


def total_duration(segments: Iterable[Segment]) -> float:
    return sum(s.duration for s in segments)


def format_time(seconds: float, millis: bool = True) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    text = f"{int(m):02d}:{s:06.3f}" if millis else f"{int(m):02d}:{int(s):02d}"
    return f"{int(h)}:{text}" if h >= 1 else text
