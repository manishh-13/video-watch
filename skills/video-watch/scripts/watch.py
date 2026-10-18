#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["yt-dlp", "imageio-ffmpeg", "pillow", "faster-whisper", "numpy"]
# ///
"""Turn a video (URL or local file) into things an LLM can read:
frames at scene cuts + even intervals, labelled contact sheets, a timestamped
transcript, and source metadata. Usage:
  uv run watch.py <url-or-path> [--out DIR] [--max-frames 36] [--no-transcript]
                  [--model base] [--cookies-from-browser chrome]
"""
import argparse, json, math, os, re, shutil, subprocess, sys
from pathlib import Path


def ffmpeg_bin():
    return shutil.which("ffmpeg") or __import__("imageio_ffmpeg").get_ffmpeg_exe()


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def fmt(t):
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def download(src, out, cookies):
    """Return (video_path, metadata dict). Local files pass through."""
    if Path(src).expanduser().exists():
        p = Path(src).expanduser().resolve()
        return p, {"source": str(p), "title": p.name}
    import yt_dlp
    opts = {
        "outtmpl": str(out / "video.%(ext)s"),
        "format": "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
        "merge_output_format": "mp4",
        "ffmpeg_location": ffmpeg_bin(),
        "quiet": True, "no_warnings": True, "noplaylist": True,
    }
    if cookies:
        opts["cookiesfrombrowser"] = (cookies,)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(src, download=True)
        path = Path(ydl.prepare_filename(info))
    if not path.exists():
        path = next(out.glob("video.*"))
    keep = ["title", "uploader", "uploader_id", "channel", "upload_date", "timestamp",
            "duration", "view_count", "like_count", "comment_count", "description",
            "webpage_url", "extractor", "tags", "width", "height", "fps"]
    meta = {k: info.get(k) for k in keep if info.get(k) not in (None, "", [])}
    meta["source"] = src
    return path, meta


def probe(ff, video):
    err = run([ff, "-hide_banner", "-i", str(video)]).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    res = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    return dur, (int(res[1]), int(res[2])) if res else None, "Audio:" in err


def scene_times(ff, video, thresh, start, end):
    win = ["-ss", str(start), "-to", str(end)]
    r = run([ff, "-hide_banner", *win, "-i", str(video), "-an", "-vf",
             f"select='gt(scene,{thresh})',showinfo", "-f", "null", "-"])
    return [start + float(x) for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]


def group_cuts(scenes, gap=0.5):
    """A wipe or flash shows up as several 'cuts' a few frames apart. Group them
    into one shot change: (first, last) per group."""
    groups = []
    for t in scenes:
        if groups and t - groups[-1][1] < gap:
            groups[-1][1] = t
        else:
            groups.append([t, t])
    return [tuple(g) for g in groups]


def pick_times(start, end, scenes, max_frames):
    """Always include 0s, the first 3s (the hook), every scene cut, and an even
    grid; then thin to max_frames while keeping spread."""
    span = end - start
    even_n = max(4, min(max_frames, int(span / 2) + 1))
    even = [start + span * i / even_n for i in range(even_n)]
    hook = [start + t for t in (0.0, 1.0, 2.0, 3.0) if t < span]
    # Sample each shot 0.3 s after its transition ends, so the frame shows the
    # settled new shot instead of a half-finished wipe.
    shots = [min(last + 0.3, end - 0.1) for _, last in group_cuts(scenes)]
    cand = sorted(set(round(t, 2) for t in hook + shots + even if start <= t < end - 0.05))
    picked = []
    for t in cand:  # drop near-duplicates
        if not picked or t - picked[-1] >= 0.4:
            picked.append(t)
    if len(picked) > max_frames:
        step = len(picked) / max_frames
        picked = [picked[int(i * step)] for i in range(max_frames)]
    return picked


def settle(ff, video, t, limit, span=0.8, step=0.1, still=1.5):
    """Move t forward to the first moment the picture stops changing (end of a
    wipe, zoom or flash). Reads tiny greyscale frames, so it is cheap. Never
    moves past limit (the next shot change)."""
    span = min(span, limit - t)
    if span < step * 2:
        return t
    w, h = 48, 27
    r = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-ss", f"{t}", "-t", f"{span}",
                        "-i", str(video), "-vf", f"fps={1/step},scale={w}:{h},format=gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    n = len(r.stdout) // (w * h)
    if n < 2:
        return t
    fr = [r.stdout[i * w * h:(i + 1) * w * h] for i in range(n)]
    diffs = [sum(abs(a - b) for a, b in zip(fr[i], fr[i + 1])) / (w * h) for i in range(n - 1)]
    for i, d in enumerate(diffs):
        if d < still:
            return round(t + i * step, 2)
    return round(t + diffs.index(min(diffs)) * step, 2)


def grab(ff, video, times, fdir):
    files = []
    for i, t in enumerate(times):
        f = fdir / f"f{i:03d}_{t:07.2f}s.jpg"
        run([ff, "-hide_banner", "-loglevel", "error", "-ss", f"{t}", "-i", str(video),
             "-frames:v", "1", "-vf", "scale='min(720,iw)':-2", "-q:v", "3", "-y", str(f)])
        if f.exists():
            files.append((t, f))
    return files


def sheets(frames, sdir, per=9, cols=3, tile_w=360):
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype("Arial.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    out = []
    for s in range(0, len(frames), per):
        chunk = frames[s:s + per]
        ims = [Image.open(f).convert("RGB") for _, f in chunk]
        th = int(tile_w * ims[0].height / ims[0].width)
        rows = math.ceil(len(ims) / cols)
        sheet = Image.new("RGB", (cols * tile_w, rows * th), "black")
        d = ImageDraw.Draw(sheet)
        for i, ((t, _), im) in enumerate(zip(chunk, ims)):
            x, y = (i % cols) * tile_w, (i // cols) * th
            sheet.paste(im.resize((tile_w, th)), (x, y))
            label = f"#{s + i} {fmt(t)}"
            d.rectangle([x, y, x + 150, y + 28], fill="black")
            d.text((x + 5, y + 3), label, fill="yellow", font=font)
        p = sdir / f"sheet_{s // per + 1:02d}.jpg"
        sheet.save(p, quality=85)
        out.append(p)
    return out


def transcribe(ff, video, out, model):
    # Decode with ffmpeg straight to 16 kHz mono float32 so faster-whisper never
    # touches PyAV (version mismatches there break decode_audio).
    import numpy as np
    r = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-i", str(video), "-vn",
                        "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True)
    audio = np.frombuffer(r.stdout, dtype=np.float32)
    if audio.size < 16000 * 0.5:
        return None, None
    from faster_whisper import WhisperModel
    m = WhisperModel(model, device="cpu", compute_type="int8")
    segs, info = m.transcribe(audio, vad_filter=True)
    # Whisper invents words over music or silence ("You", "Thank you"). Drop
    # segments it is itself unsure contain speech.
    lines = [f"[{fmt(s.start)} -> {fmt(s.end)}] {s.text.strip()}" for s in segs
             if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0 and s.text.strip()]
    return lines, info.language


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out")
    ap.add_argument("--max-frames", type=int, default=36)
    ap.add_argument("--scene", type=float, default=0.3, help="scene-cut sensitivity 0-1")
    ap.add_argument("--no-transcript", action="store_true")
    ap.add_argument("--model", default="base", help="whisper size: tiny|base|small|medium")
    ap.add_argument("--cookies-from-browser", dest="cookies")
    ap.add_argument("--start", type=float, default=0.0, help="window start, seconds")
    ap.add_argument("--end", type=float, help="window end, seconds (default: end of video)")
    a = ap.parse_args()

    out = Path(a.out or Path.cwd() / "video-watch-out").expanduser().resolve()
    (out / "frames").mkdir(parents=True, exist_ok=True)
    (out / "sheets").mkdir(exist_ok=True)
    ff = ffmpeg_bin()

    print("1/4 fetching video...", file=sys.stderr)
    video, meta = download(a.src, out, a.cookies)
    dur, res, has_audio = probe(ff, video)
    start, end = max(0.0, a.start), min(a.end or dur, dur)
    print(f"2/4 detecting scene cuts ({fmt(start)} to {fmt(end)} of {dur:.1f}s)...", file=sys.stderr)
    scenes = scene_times(ff, video, a.scene, start, end)
    times = pick_times(start, end, scenes, a.max_frames)
    starts = [a for a, _ in group_cuts(scenes)] + [end]
    times = sorted(set(t if t == start else settle(ff, video, t, min(x for x in starts if x > t) - 0.05)
                       for t in times))
    frames = grab(ff, video, times, out / "frames")
    print(f"3/4 {len(frames)} frames, building contact sheets...", file=sys.stderr)
    sh = sheets(frames, out / "sheets") if frames else []

    lines, lang = None, None
    if has_audio and not a.no_transcript:
        print("4/4 transcribing audio (first run downloads the model)...", file=sys.stderr)
        lines, lang = transcribe(ff, video, out, a.model)
    if lines is not None:
        (out / "transcript.txt").write_text("\n".join(lines) or "(no speech detected)")

    shots = group_cuts(scenes)
    cps = len(shots) / (end - start) if end > start else 0
    r = [f"# Video digest: {meta.get('title', video.name)}", "",
         f"- Video file: {video}",
         f"- Duration: {dur:.1f}s | Resolution: {res[0]}x{res[1] if res else '?'}" if res else f"- Duration: {dur:.1f}s",
         f"- Window analysed: {fmt(start)} to {fmt(end)}",
         f"- Shot changes: {len(shots)} ({cps:.2f}/s; {len(scenes)} raw cuts, a wipe counts once) at: "
         + (", ".join(fmt(a) for a, _ in shots[:60]) if shots else "none (single continuous shot)"),
         f"- Audio: {'yes' if has_audio else 'none'}" + (f", speech language: {lang}" if lang else ""),
         f"- Frames: {len(frames)} in {out / 'frames'}",
         "- Contact sheets (view these first, labels are #frame mm:ss):"]
    r += [f"  - {p}" for p in sh]
    r += ["", "## Source metadata", "```json", json.dumps(meta, indent=2, ensure_ascii=False, default=str), "```", ""]
    r += ["## Transcript"] + ((lines or ["(no speech detected)"]) if lines is not None
                              else ["(skipped or no audio track)"])
    (out / "report.md").write_text("\n".join(r))
    print(out / "report.md")


if __name__ == "__main__":
    main()
