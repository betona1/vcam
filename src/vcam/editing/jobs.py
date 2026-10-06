"""편집 작업 실행: 자르기·구간 제거·나누기·합치기·소리 추출/제거·변환.

모든 작업은 "조각(파일 + 구간)" 목록으로 표현한다.
- 합치기 옵션이 켜져 있으면 모든 조각을 이어 한 파일로, 꺼져 있으면 조각마다 한 파일로 저장한다.
- 빠른 모드: 다시 인코딩하지 않고 스트림을 복사한다(무손실·빠름). 시작점은 키프레임에 맞춰진다.
- 인코딩 모드: 프레임 단위로 정확히 자르고, 형식·코덱·해상도·속도 등을 바꿀 수 있다.
결과는 임시 이름으로 만든 뒤 ffprobe로 확인하고, 기존 파일을 덮어쓰지 않는 이름으로 옮긴다.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from vcam.editing.formats import (
    AUDIO_FORMATS,
    CONTAINERS,
    VIDEO_CODECS,
    EncodeSettings,
    audio_args,
    pick_video_encoder,
    video_quality_args,
)
from vcam.editing.probe import MediaFile, ProbeError, keyframes, probe_file
from vcam.editing.segments import Segment, format_time, snap_to_keyframes
from vcam.encoding.ffmpeg import CREATE_NO_WINDOW, FfmpegPaths
from vcam.util.paths import cache_dir, sanitize_filename, unique_path

log = logging.getLogger(__name__)

Progress = Callable[[float, str], None]


class EditError(Exception):
    pass


class Cancelled(EditError):
    def __init__(self) -> None:
        super().__init__("작업을 취소했습니다")


@dataclass(frozen=True)
class Piece:
    media: MediaFile
    segment: Segment


@dataclass(frozen=True)
class EditJob:
    pieces: tuple[Piece, ...]
    output_dir: Path
    suffix: str
    mode: Literal["fast", "encode"] = "fast"
    merge: bool = True
    encode: EncodeSettings = field(default_factory=EncodeSettings)
    save_video: bool = True
    extract_audio: str | None = None  # AUDIO_FORMATS 키
    remove_audio: bool = False
    save_timestamps: bool = False
    base_name: str | None = None


@dataclass
class JobResult:
    outputs: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ── FFmpeg 실행 ─────────────────────────────────────────────────────────────
class Runner:
    def __init__(self, ffmpeg: FfmpegPaths, cancel: threading.Event, progress: Progress | None) -> None:
        self.ffmpeg = ffmpeg
        self.cancel = cancel
        self.progress = progress or (lambda f, s: None)

    def run(self, args: list[str], duration: float, label: str, span: tuple[float, float]) -> None:
        if self.cancel.is_set():
            raise Cancelled()
        cmd = [str(self.ffmpeg.ffmpeg), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
               "-progress", "pipe:1", "-nostats", *args]  # fmt: skip
        log.info("ffmpeg: %s", " ".join(cmd[1:])[:600])
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                creationflags=CREATE_NO_WINDOW, text=True, encoding="utf-8", errors="replace")  # fmt: skip
        err_lines: list[str] = []
        reader = threading.Thread(target=lambda: err_lines.extend(proc.stderr), daemon=True)
        reader.start()
        lo, hi = span
        self.progress(lo, label)
        assert proc.stdout is not None
        for line in proc.stdout:
            if self.cancel.is_set():
                proc.terminate()
                break
            key, _, value = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and value.isdigit() and duration > 0:
                frac = min(1.0, int(value) / 1e6 / duration)
                self.progress(lo + (hi - lo) * frac, label)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        reader.join(timeout=5)
        if self.cancel.is_set():
            raise Cancelled()
        if proc.returncode != 0:
            tail = "".join(err_lines[-8:]).strip()
            raise EditError(f"FFmpeg 처리에 실패했습니다: {tail[-500:]}")
        self.progress(hi, label)


# ── 필터 그래프 ─────────────────────────────────────────────────────────────
def _atempo_chain(speed: float) -> list[str]:
    parts = []
    while speed > 2.0:
        parts.append("atempo=2.0")
        speed /= 2.0
    while speed < 0.5:
        parts.append("atempo=0.5")
        speed /= 0.5
    parts.append(f"atempo={speed:.4f}")
    return parts


def _rotate_filters(s: EncodeSettings) -> list[str]:
    f = {90: ["transpose=1"], 180: ["hflip", "vflip"], 270: ["transpose=2"]}.get(s.rotate % 360, [])
    if s.flip_h:
        f.append("hflip")
    if s.flip_v:
        f.append("vflip")
    return f


def _scale_filters(s: EncodeSettings, gif: bool) -> list[str]:
    if s.resolution in ("preset", "custom"):
        w, h = s.width - s.width % 2, s.height - s.height % 2
        return [f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos",
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2", "setsar=1"]  # fmt: skip
    if s.resolution == "fit_width":
        return [f"scale={s.width - s.width % 2}:-2:flags=lanczos"]
    if s.resolution == "fit_height":
        return [f"scale=-2:{s.height - s.height % 2}:flags=lanczos"]
    if gif:
        return ["scale='min(640,iw)':-2:flags=lanczos"]
    return ["scale=trunc(iw/2)*2:trunc(ih/2)*2"]


@dataclass
class Graph:
    inputs: list[list[str]]
    filters: list[str]
    video_out: str | None
    audio_out: str | None
    duration: float


def build_graph(pieces: Sequence[Piece], s: EncodeSettings, want_video: bool, want_audio: bool) -> Graph:
    """조각들을 잘라 이어 붙이고(trim/concat) 회전·크기·속도 등을 적용하는 filter_complex."""
    inputs: list[list[str]] = []
    index: dict[Path, int] = {}
    for p in pieces:
        if p.media.path not in index:
            index[p.media.path] = len(inputs)
            inputs.append(["-i", str(p.media.path)])
    want_video = want_video and all(p.media.has_video for p in pieces)
    gif = want_video and s.video_codec == "gif"
    want_audio = want_audio and not gif and any(p.media.has_audio for p in pieces)
    distinct = len({p.media.path for p in pieces}) > 1
    first = next((p.media for p in pieces if p.media.has_video), None)
    tw = (first.width - first.width % 2) if first else 0
    th = (first.height - first.height % 2) if first else 0
    tfps = s.fps or (round(first.fps, 3) if first and first.fps else 30)
    deint = {"auto": ["yadif=deint=interlaced"], "always": ["yadif"], "off": []}[s.deinterlace]
    filters: list[str] = []
    labels: list[str] = []
    for i, p in enumerate(pieces):
        src = index[p.media.path]
        st, en = f"{p.segment.start:.6f}", f"{p.segment.end:.6f}"
        if want_video:
            chain = [f"trim=start={st}:end={en}", "setpts=PTS-STARTPTS", *deint]
            if distinct:  # 크기·fps가 다른 파일을 이어 붙이려면 맞춰야 한다
                chain += [f"scale={tw}:{th}:force_original_aspect_ratio=decrease", f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2",
                          "setsar=1", f"fps={tfps}", "format=yuv420p"]  # fmt: skip
            filters.append(f"[{src}:v:0]{','.join(chain)}[v{i}]")
            labels.append(f"[v{i}]")
        if want_audio:
            if p.media.has_audio:
                filters.append(f"[{src}:a:0]atrim=start={st}:end={en},asetpts=PTS-STARTPTS,"
                               f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")  # fmt: skip
            else:
                silent = len(inputs)
                inputs.append(["-f", "lavfi", "-t", f"{p.segment.duration:.6f}", "-i", "anullsrc=r=48000:cl=stereo"])
                filters.append(f"[{silent}:a]aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
            labels.append(f"[a{i}]")
    n = len(pieces)
    v_cat, a_cat = "vcat", "acat"
    if n > 1:
        filters.append(f"{''.join(labels)}concat=n={n}:v={int(want_video)}:a={int(want_audio)}"
                       + (f"[{v_cat}]" if want_video else "") + (f"[{a_cat}]" if want_audio else ""))  # fmt: skip
    else:
        if want_video:
            filters.append(f"[v0]null[{v_cat}]")
        if want_audio:
            filters.append(f"[a0]anull[{a_cat}]")
    video_out = audio_out = None
    if want_video:
        post = [*_rotate_filters(s), *_scale_filters(s, gif)]
        fps = s.fps or (15 if gif else 0)
        if fps:
            post.append(f"fps={fps}")
        if abs(s.speed - 1.0) > 1e-3:
            post.append(f"setpts=PTS/{s.speed:.4f}")
        if gif:
            filters.append(f"[{v_cat}]{','.join(post)},split[g1][g2]")
            filters.append("[g1]palettegen=stats_mode=diff[pal]")
            filters.append("[g2][pal]paletteuse=dither=bayer:bayer_scale=5[vout]")
        else:
            post.append(f"format={VIDEO_CODECS[s.video_codec].pix_fmt}")
            filters.append(f"[{v_cat}]{','.join(post)}[vout]")
        video_out = "[vout]"
    if want_audio:
        post_a = []
        if abs(s.speed - 1.0) > 1e-3:
            post_a += _atempo_chain(s.speed)
        if s.normalize:
            post_a.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        filters.append(f"[{a_cat}]{','.join(post_a) or 'anull'}[aout]")
        audio_out = "[aout]"
    duration = sum(p.segment.duration for p in pieces) / (s.speed or 1.0)
    return Graph(inputs, filters, video_out, audio_out, duration)


def encode_args(ffmpeg: FfmpegPaths, graph: Graph, s: EncodeSettings, out: Path, audio_only_codec: str | None = None) -> list[str]:
    args = [a for inp in graph.inputs for a in inp]
    args += ["-filter_complex", ";".join(graph.filters)]
    if audio_only_codec:
        return [*args, "-map", graph.audio_out, "-vn", *audio_args(s, audio_only_codec), str(out)]
    container = CONTAINERS[s.container]
    if graph.video_out:
        enc = pick_video_encoder(ffmpeg, s.video_codec)
        args += ["-map", graph.video_out, "-c:v", enc, *video_quality_args(enc, s)]
        if s.video_codec == "xvid" and enc == "mpeg4":
            args += ["-vtag", "XVID"]
        if s.video_codec == "hevc" and container.key in ("mp4", "mov", "m4v"):
            args += ["-tag:v", "hvc1"]
        if enc in ("libx264", "libx265") or enc.endswith(("_nvenc", "_qsv", "_amf")):
            args += ["-g", "60"]
    if graph.audio_out:
        args += ["-map", graph.audio_out, *audio_args(s)]
    if container.key in ("mp4", "mov", "m4v"):
        args += ["-movflags", "+faststart"]
    return [*args, "-f", container.muxer, str(out)]


# ── 실행 ────────────────────────────────────────────────────────────────────
def _finalize(ffmpeg: FfmpegPaths, tmp: Path, final_dir: Path, stem: str, ext: str, need_video: bool) -> Path:
    try:
        info = probe_file(ffmpeg, tmp)
    except ProbeError as exc:
        tmp.unlink(missing_ok=True)
        raise EditError(f"만든 파일을 확인하지 못했습니다: {exc}") from exc
    if need_video and not info.has_video:
        tmp.unlink(missing_ok=True)
        raise EditError("만든 파일에 영상이 없습니다")
    final = unique_path(final_dir, stem, ext)
    os.replace(tmp, final)
    return final


def execute(job: EditJob, ffmpeg: FfmpegPaths, cancel: threading.Event | None = None,
            progress: Progress | None = None) -> JobResult:  # fmt: skip
    cancel = cancel or threading.Event()
    runner = Runner(ffmpeg, cancel, progress)
    result = JobResult()
    if not job.pieces:
        raise EditError("처리할 구간이 없습니다")
    job.output_dir.mkdir(parents=True, exist_ok=True)
    work = cache_dir() / "edit" / uuid.uuid4().hex[:10]
    work.mkdir(parents=True)
    s = job.encode.fixed()
    groups = [list(job.pieces)] if job.merge else [[p] for p in job.pieces]
    base = sanitize_filename(job.base_name or job.pieces[0].media.path.stem)
    steps_total = len(groups) * (int(job.save_video) + int(bool(job.extract_audio)))
    done = 0

    def span() -> tuple[float, float]:
        return done / steps_total, (done + 1) / steps_total

    try:
        if job.mode == "fast" and job.merge and len({p.media.path for p in job.pieces}) > 1:
            first = job.pieces[0].media
            if not all(first.copy_compatible(p.media) for p in job.pieces):
                raise EditError("형식(코덱·해상도·소리)이 서로 다른 파일은 빠른 모드로 합칠 수 없습니다. 인코딩 모드를 선택해 주세요.")
        snapped = _snap(job, ffmpeg, result) if job.mode == "fast" else {}
        for gi, group in enumerate(groups):
            stem = f"{base}{job.suffix}" + (f"_{gi + 1:02d}" if len(groups) > 1 else "")
            label = f"{gi + 1}/{len(groups)}" if len(groups) > 1 else ""
            if job.save_video:
                has_video = all(p.media.has_video for p in group)
                if job.mode == "fast":
                    out = _fast(job, ffmpeg, runner, [snapped.get(id(p), p) for p in group], work, stem, span(), label)
                else:
                    out = _encode(job, ffmpeg, runner, s, group, work, stem, span(), label, has_video)
                result.outputs.append(out)
                done += 1
            if job.extract_audio:
                if not any(p.media.has_audio for p in group):
                    result.notes.append(f"{stem}: 소리가 없어 추출하지 못했습니다")
                else:
                    result.outputs.append(_extract(job, ffmpeg, runner, s, group, work, stem, span(), label))
                done += 1
            if job.save_timestamps:
                result.outputs.append(_timestamps(job, group, stem))
        if progress:
            progress(1.0, "완료")
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)
        # 실패·취소로 남은 임시 결과 파일 정리
        for leftover in job.output_dir.glob(f".{base}{job.suffix}*.vcam-tmp*"):
            leftover.unlink(missing_ok=True)


def _snap(job: EditJob, ffmpeg: FfmpegPaths, result: JobResult) -> dict[int, Piece]:
    snapped: dict[int, Piece] = {}
    cache: dict[Path, list[float]] = {}
    for p in job.pieces:
        if not p.media.has_video or p.segment.start <= 0:
            continue
        keys = cache.setdefault(p.media.path, keyframes(ffmpeg, p.media.path))
        seg = snap_to_keyframes([p.segment], keys)[0]
        if seg.start < p.segment.start - 0.02:
            result.notes.append(f"빠른 모드: {format_time(p.segment.start)} → {format_time(seg.start)} (키프레임)에서 시작")
        snapped[id(p)] = Piece(p.media, seg)
    return snapped


def _copy_args(p: Piece, out: Path, remove_audio: bool) -> list[str]:
    args = ["-ss", f"{p.segment.start:.6f}", "-i", str(p.media.path), "-t", f"{p.segment.duration:.6f}",
            "-map", "0:v:0?", "-map", "0:a?", "-c", "copy", "-avoid_negative_ts", "make_zero"]  # fmt: skip
    if remove_audio:
        args.append("-an")
    return [*args, str(out)]


def _fast(job: EditJob, ffmpeg: FfmpegPaths, runner: Runner, group: list[Piece], work: Path, stem: str,
          span: tuple[float, float], label: str) -> Path:  # fmt: skip
    ext = group[0].media.path.suffix.lower() or ".mp4"
    tmp = job.output_dir / f".{stem}.vcam-tmp{ext}"
    total = sum(p.segment.duration for p in group)
    title = f"빠른 저장 {label}".strip()
    if len(group) == 1:
        runner.run(_copy_args(group[0], tmp, job.remove_audio), total, title, span)
    else:
        lo, hi = span
        parts = []
        for i, p in enumerate(group):
            part = work / f"part{i:03d}.mkv"
            a = lo + (hi - lo) * 0.9 * i / len(group)
            b = lo + (hi - lo) * 0.9 * (i + 1) / len(group)
            runner.run(_copy_args(p, part, job.remove_audio), p.segment.duration, title, (a, b))
            parts.append(part)
        listing = work / "concat.txt"
        listing.write_text("".join(f"file '{pp.as_posix()}'\n" for pp in parts), encoding="utf-8")
        runner.run(["-f", "concat", "-safe", "0", "-i", str(listing), "-map", "0", "-c", "copy", str(tmp)],
                   total, title, (lo + (hi - lo) * 0.9, hi))  # fmt: skip
    return _finalize(ffmpeg, tmp, job.output_dir, stem, ext, group[0].media.has_video)


def _encode(job: EditJob, ffmpeg: FfmpegPaths, runner: Runner, s: EncodeSettings, group: list[Piece], work: Path,
            stem: str, span: tuple[float, float], label: str, has_video: bool) -> Path:  # fmt: skip
    if not has_video:  # 소리 파일 편집: 소리 형식으로 저장
        fmt = job.extract_audio or "mp3"
        _lbl, ext, codec = AUDIO_FORMATS[fmt]
        graph = build_graph(group, s, want_video=False, want_audio=True)
        tmp = job.output_dir / f".{stem}.vcam-tmp{ext}"
        runner.run(encode_args(ffmpeg, graph, s, tmp, audio_only_codec=codec), graph.duration, f"인코딩 {label}".strip(), span)
        return _finalize(ffmpeg, tmp, job.output_dir, stem, ext, False)
    container = CONTAINERS[s.container]
    graph = build_graph(group, s, want_video=True, want_audio=not job.remove_audio and bool(container.audio))
    tmp = job.output_dir / f".{stem}.vcam-tmp{container.ext}"
    try:
        args = encode_args(ffmpeg, graph, s, tmp)
    except ValueError as exc:
        raise EditError(str(exc)) from exc
    runner.run(args, graph.duration, f"인코딩 {label}".strip(), span)
    return _finalize(ffmpeg, tmp, job.output_dir, stem, container.ext, True)


def _extract(job: EditJob, ffmpeg: FfmpegPaths, runner: Runner, s: EncodeSettings, group: list[Piece], work: Path,
             stem: str, span: tuple[float, float], label: str) -> Path:  # fmt: skip
    _lbl, ext, codec = AUDIO_FORMATS[job.extract_audio or "mp3"]
    audio_settings = s if job.mode == "encode" else EncodeSettings(audio_bitrate_kbps=s.audio_bitrate_kbps)
    graph = build_graph(group, audio_settings, want_video=False, want_audio=True)
    tmp = job.output_dir / f".{stem}.vcam-tmp{ext}"
    runner.run(encode_args(ffmpeg, graph, audio_settings, tmp, audio_only_codec=codec), graph.duration,
               f"소리 추출 {label}".strip(), span)  # fmt: skip
    return _finalize(ffmpeg, tmp, job.output_dir, stem, ext, False)


def _timestamps(job: EditJob, group: list[Piece], stem: str) -> Path:
    lines, pos = [], 0.0
    for p in group:
        lines.append(f"{format_time(pos)}\t{p.media.path.name}\t{format_time(p.segment.start)} ~ {format_time(p.segment.end)}")
        pos += p.segment.duration / (job.encode.speed if job.mode == "encode" else 1.0)
    path = unique_path(job.output_dir, f"{stem}_타임스탬프", ".txt")
    path.write_text("결과 위치\t원본 파일\t원본 구간\n" + "\n".join(lines) + "\n", encoding="utf-8-sig")
    return path


def capture_frame(ffmpeg: FfmpegPaths, media: MediaFile, at: float, output_dir: Path) -> Path:
    """현재 프레임을 원본 화질 PNG로 저장한다."""
    out = unique_path(output_dir, f"{media.path.stem}_{format_time(at).replace(':', '-')}", ".png")
    cmd = [str(ffmpeg.ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.6f}", "-i", str(media.path),
           "-frames:v", "1", str(out)]  # fmt: skip
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW, timeout=60)
    if r.returncode != 0 or not out.exists():
        raise EditError(f"프레임을 저장하지 못했습니다: {r.stderr.strip()[:200]}")
    return out


def load_media(ffmpeg: FfmpegPaths, path: Path) -> MediaFile:
    try:
        return probe_file(ffmpeg, path)
    except ProbeError as exc:
        raise EditError(str(exc)) from exc
