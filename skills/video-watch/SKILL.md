---
name: video-watch
description: "Watch and understand a video by turning it into frames, contact sheets, a timestamped transcript and source metadata. Use when the user shares a video link (Instagram reel, YouTube or Shorts, TikTok, X, Facebook, Vimeo, any yt-dlp site) or a local video file and asks what happens in it, to summarise it, to fact-check it (is this real, is this true, is it AI or edited), or to break it down as an ad or creative reference (hook, shot list, pacing, text overlays, CTA)."
license: MIT
compatibility: "Needs uv (https://docs.astral.sh/uv/) and network access to fetch Python packages, the Whisper model and online videos. Works in Claude Code and any agent with a shell. In sandboxes without network, pass a local video file."
metadata:
  author: manishh-13
  version: "1.0.0"
---

# Video Watch

You cannot play video, but you can read images and text. `scripts/watch.py` converts a video into what you can read:

- **frames** at every scene cut, the first 3 s (the hook) and an even grid, capped at `--max-frames`
- **contact sheets**: 3x3 grids, each tile labelled `#frame mm:ss` (one image = 9 moments, cheap to view)
- **transcript** with timestamps (local Whisper, any language)
- **metadata**: uploader, upload date, views, caption/description, tags
- **report.md** tying it together, plus shot-change times and shot changes per second

## 1. Digest the video

```bash
uv run --script <skill-dir>/scripts/watch.py "<url-or-path>" --out ./video-watch-out/<short-name>
```

`<skill-dir>` is the folder containing this SKILL.md. Progress goes to stderr; the only stdout line is the path to `report.md`.

Needs only `uv` (it pulls yt-dlp, a bundled ffmpeg, Pillow and faster-whisper itself). First run downloads the Whisper model (about 150 MB for `base`). Run it with a long timeout or in the background.

| Flag | When |
|---|---|
| `--start S --end E` | Zoom into a window (seconds). Use for long videos, or to re-check a moment densely |
| `--max-frames N` | Default 36. Raise for fast-cut reels you need shot by shot, lower for talking heads |
| `--scene 0.2` | More sensitive cut detection (default 0.3); use 0.4+ if handheld footage creates false cuts |
| `--model small` | Better transcript for accents, music-heavy or non-English audio (`tiny` is fastest) |
| `--no-transcript` | Silent video, or visuals only |
| `--cookies-from-browser chrome` | Instagram/Facebook/TikTok refuses ("login required", "rate limit"). Uses the user's own logged-in browser session; ask first |

If a download fails, tell the user plainly and ask them to download the video and give you the file path instead.

## 2. Watch it

1. Read `report.md` (metadata, cut times, transcript).
2. View **every** contact sheet in order (open each image file with your image-reading tool, one at a time). Note what changes between tiles, not just what each shows.
3. View individual frames from `frames/` (file names carry the timestamp) when you need detail: on-screen text, a face, a logo, a product label, a suspicious artefact.
4. If something important happens between sampled frames, re-run with `--start/--end` around it and `--max-frames 30` (dense sampling of a few seconds).
5. Treat the transcript as a draft: Whisper mishears names and odd words, so check key words against on-screen text and context.
6. Line up transcript timestamps with frame timestamps so you know what is said over what is shown.

Be honest about the sampling: you saw N stills plus the audio transcript, not continuous motion. Say so when a conclusion depends on motion you could not see (lip sync, smooth physics, a fast trick).

## 3. Answer the question

### Summary / "what is this"
What happens, in order, with timestamps. Who or what is on screen, the setting, what is said, the on-screen text, and the point of the video.

### Fact-check / "is this true / real"
Split the problem into two separate questions and answer both:

**A. Is the footage authentic?** Look for:
- AI generation: warped hands or text, melting edges, inconsistent reflections or shadows, objects that change between frames, too-smooth skin, gibberish signage
- Editing: jump cuts at the exact moment of the "impossible" thing, mismatched lighting or grain between shots, cropped watermarks, green-screen halos
- Recycled footage: web search distinctive details (event, place, on-screen text, quoted line) to find an older or original upload; compare dates with `upload_date`
- Metadata: the uploader can edit the caption/description at any time, so it is a clue, not proof. The upload date and uploader are stronger signals
- Audio: voice over not matching lip movement, cloned-voice flatness, music masking edits

**B. Are the claims true?** List each factual claim made in speech, on-screen text or caption (with timestamp), then check each against reliable sources with web search. Cite sources inline.

Give a verdict per claim (True / Misleading / False / Unverifiable) and an overall one, with a confidence level and the evidence for it. Never call something fake or real with certainty from stills alone; say what would settle it.

### Ad / creative reference
Break it into something the user can reproduce:
- **Hook (0 to 3 s)**: what grabs attention, first line spoken, first text overlay
- **Shot list**: table of time | shot (framing, subject, action) | on-screen text | audio/VO
- **Pacing**: shot changes per second (from report.md), where the rhythm speeds up or slows
- **Structure**: problem, agitation, demo/proof, offer, CTA, and where each lands in time
- **Style**: aspect ratio, lighting, colour, caption style, music/sound, UGC vs produced
- **Why it works** and **how to adapt it** for the user's product, as a concrete beat-by-beat script

## Cleanup
Outputs (including the downloaded video) stay in `--out`. Delete the folder when done unless the user wants to keep it.
