#!/usr/bin/env python3
"""
yt-ripper: Rip the first few seconds of YouTube videos and brand them as shorts.

Usage:
    python3 yt_ripper.py --source "https://youtube.com/@channel" \
        --logo logo.png \
        --top-text "download Fitnit now!" \
        --bottom-text "link in bio" \
        --clip-duration 3 \
        --max-videos 10
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw


# ─── Defaults ────────────────────────────────────────────────────────────────

SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920
BANNER_HEIGHT = 320
VIDEO_AREA_HEIGHT = SHORTS_HEIGHT - BANNER_HEIGHT  # 1660
FPS = 30
DEFAULT_CLIP_DURATION = 3
DEFAULT_LOGO_DURATION = 2
DEFAULT_MAX_VIDEOS = 10


def run(cmd, check=True, capture=True):
    """Run a shell command and return stdout."""
    result = subprocess.run(
        cmd, capture_output=capture, text=True, check=check
    )
    return result.stdout.strip() if capture else None


def fetch_video_urls(source, max_videos):
    """Use yt-dlp to get video URLs from a channel, playlist, or single video."""
    print(f"Fetching video URLs from: {source}")
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--print", "id",
        "--playlist-end", str(max_videos),
        source,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error fetching videos: {result.stderr}")
        sys.exit(1)

    video_ids = [vid.strip() for vid in result.stdout.strip().split("\n") if vid.strip()]
    print(f"Found {len(video_ids)} videos")
    return video_ids


def download_clip(video_id, output_path, duration):
    """Download just the first N seconds of a YouTube video."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"  Downloading first {duration}s of {video_id}...")

    # Download full video (yt-dlp doesn't support partial), then we'll trim with ffmpeg
    # Use a format that's reasonable quality but not huge
    temp_full = output_path.replace(".mp4", "_full.mp4")
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--sleep-interval", "2",  # Minimum 2s between requests to avoid rate limiting
        "--max-sleep-interval", "5",  # Random jitter up to 5s
        "-o", temp_full,
        # Download only what we need using --download-sections
        "--download-sections", f"*0-{duration}",
        "--force-keyframes-at-cuts",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    Warning: download failed for {video_id}: {result.stderr[-200:]}")
        return False

    # Trim to exact duration with ffmpeg (download-sections can be slightly off)
    trim_cmd = [
        "ffmpeg", "-y",
        "-i", temp_full,
        "-t", str(duration),
        "-c", "copy",
        output_path,
    ]
    subprocess.run(trim_cmd, capture_output=True, text=True, check=False)

    # Clean up full download
    if os.path.exists(temp_full):
        os.remove(temp_full)

    return os.path.exists(output_path)


def get_video_info(video_path):
    """Get video dimensions using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    info = json.loads(result.stdout)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    return None, None


# ─── Animation Presets ────────────────────────────────────────────────────
# Each preset defines timing + expression generators for text, logo, and bottom text.
# Expressions use ffmpeg syntax. `d` = delay before start, `dur` = animation duration.

ANIMATION_PRESETS = {
    "drop-in": {
        "label": "Drop In",
        "text_dur": 0.12,
        "text_stagger": 0.06,
        "logo_dur": 0.2,
        # Text: fade in + slide down from above
        "text_alpha": lambda d, dur: f"if(lt(t,{d}),0,min(1,(t-{d})/{dur}))",
        "text_y": lambda y, d, dur: f"if(lt(t,{d}),{y}-30,if(lt(t,{d}+{dur}),{y}-30*(1-(t-{d})/{dur}),{y}))",
        # Logo: fade in + drop from above with ease-out
        "logo_y": lambda y, d, dur: f"if(lt(t,{d}),-200,if(lt(t,{d}+{dur}),{y}-120*(1-(t-{d})/{dur})*(1-(t-{d})/{dur}),{y}))",
        # Bottom text: simple fade
        "bt_alpha": lambda d, dur: f"if(lt(t,{d}),0,min(1,(t-{d})/{dur}))",
    },
    "slam": {
        "label": "Slam",
        "text_dur": 0.08,
        "text_stagger": 0.04,
        "logo_dur": 0.15,
        # Text: instant pop (near-zero fade)
        "text_alpha": lambda d, dur: f"if(lt(t,{d}),0,1)",
        "text_y": lambda y, d, dur: f"{y}",
        # Logo: scale slam — starts way off screen, slams into place
        "logo_y": lambda y, d, dur: f"if(lt(t,{d}),-300,if(lt(t,{d}+{dur}),{y}-300*(1-(t-{d})/{dur}),{y}))",
        # Bottom text: instant
        "bt_alpha": lambda d, dur: f"if(lt(t,{d}),0,1)",
    },
    "slide-up": {
        "label": "Slide Up",
        "text_dur": 0.15,
        "text_stagger": 0.06,
        "logo_dur": 0.25,
        # Text: fade in + slide up from below
        "text_alpha": lambda d, dur: f"if(lt(t,{d}),0,min(1,(t-{d})/{dur}))",
        "text_y": lambda y, d, dur: f"if(lt(t,{d}),{y}+25,if(lt(t,{d}+{dur}),{y}+25*(1-(t-{d})/{dur}),{y}))",
        # Logo: slide up from below
        "logo_y": lambda y, d, dur: f"if(lt(t,{d}),{y}+200,if(lt(t,{d}+{dur}),{y}+200*(1-(t-{d})/{dur})*(1-(t-{d})/{dur}),{y}))",
        # Bottom text: slide up
        "bt_alpha": lambda d, dur: f"if(lt(t,{d}),0,min(1,(t-{d})/{dur}))",
    },
    "bounce": {
        "label": "Bounce",
        "text_dur": 0.12,
        "text_stagger": 0.05,
        "logo_dur": 0.3,
        # Text: fade in
        "text_alpha": lambda d, dur: f"if(lt(t,{d}),0,min(1,(t-{d})/{dur}))",
        "text_y": lambda y, d, dur: f"if(lt(t,{d}),{y}-20,if(lt(t,{d}+{dur}),{y}-20*cos((t-{d})/{dur}*3.14159),{y}))",
        # Logo: bounce — overshoot then settle (sine bounce)
        "logo_y": lambda y, d, dur: f"if(lt(t,{d}),-200,if(lt(t,{d}+{dur}),{y}-150*cos((t-{d})/{dur}*3.14159)*(1-(t-{d})/{dur}),{y}))",
        # Bottom text: fade
        "bt_alpha": lambda d, dur: f"if(lt(t,{d}),0,min(1,(t-{d})/{dur}))",
    },
    "typewriter": {
        "label": "Typewriter",
        "text_dur": 0.01,
        "text_stagger": 0.15,
        "logo_dur": 0.2,
        # Text: instant appear, one line at a time with longer stagger
        "text_alpha": lambda d, dur: f"if(lt(t,{d}),0,1)",
        "text_y": lambda y, d, dur: f"{y}",
        # Logo: fade in
        "logo_y": lambda y, d, dur: f"if(lt(t,{d}),-200,if(lt(t,{d}+{dur}),{y}-200*(1-(t-{d})/{dur}),{y}))",
        # Bottom text: instant
        "bt_alpha": lambda d, dur: f"if(lt(t,{d}),0,1)",
    },
    "none": {
        "label": "None (instant)",
        "text_dur": 0.01,
        "text_stagger": 0.0,
        "logo_dur": 0.01,
        "text_alpha": lambda d, dur: "1",
        "text_y": lambda y, d, dur: f"{y}",
        "logo_y": lambda y, d, dur: f"{y}",
        "bt_alpha": lambda d, dur: "1",
    },
}


def _logo_positions(lw, lh, banner_h):
    """Return logo (x, y) for each named position, offset below the banner.
    Uses main_w/main_h (W/H) since these are used in overlay filter expressions."""
    vid_center_y = f"({banner_h}+(main_h-{banner_h}-{lh})/2)"
    return {
        "center":        (f"(main_w-{lw})/2", vid_center_y),
        "top-center":    (f"(main_w-{lw})/2", f"({banner_h}+60)"),
        "bottom-center": (f"(main_w-{lw})/2", f"(main_h-{lh}-80)"),
        "bottom-left":   (f"60",               f"(main_h-{lh}-80)"),
        "bottom-right":  (f"(main_w-{lw}-60)", f"(main_h-{lh}-80)"),
        "top-left":      (f"60",               f"({banner_h}+60)"),
        "top-right":     (f"(main_w-{lw}-60)", f"({banner_h}+60)"),
    }


def _wrap_text(text, max_chars_per_line=30):
    """Word-wrap text to fit within a given character width."""
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        test = f"{current_line} {word}".strip() if current_line else word
        if len(test) <= max_chars_per_line:
            current_line = test
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return lines


def _auto_fontsize(text, max_width, base_size=52, min_size=28, padding=80):
    """Pick a font size and line-wrap that fits within max_width with padding."""
    available = max_width - padding * 2
    # Approximate: each character is ~0.55x the font size for Arial Bold
    for size in range(base_size, min_size - 1, -2):
        chars_per_line = int(available / (size * 0.55))
        lines = _wrap_text(text, chars_per_line)
        # Check that no single line overflows
        max_line_len = max(len(line) for line in lines)
        estimated_width = max_line_len * size * 0.55
        if estimated_width <= available and len(lines) <= 4:
            return size, lines
    # Fallback: smallest size with aggressive wrapping
    chars_per_line = int(available / (min_size * 0.55))
    return min_size, _wrap_text(text, chars_per_line)


def _round_corners(image_path, radius=30):
    """Apply rounded corners to a logo image. Returns path to new image."""
    try:
        img = Image.open(image_path).convert("RGBA")

        # Create a mask with rounded corners
        size = img.size
        mask = Image.new("L", size, 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle([(0, 0), size], radius=radius, fill=255)

        # Apply mask and save to temp file
        img.putalpha(mask)
        temp_path = image_path.replace(".png", "_rounded.png").replace(".jpg", "_rounded.png").replace(".jpeg", "_rounded.png")
        img.save(temp_path, "PNG")
        return temp_path
    except Exception as e:
        print(f"    Warning: failed to round logo corners: {e}")
        return image_path


def build_short(
    clip_path,
    output_path,
    logo_path,
    top_text,
    bottom_text,
    clip_duration,
    logo_duration,
    font_path=None,
    logo_position="center",
    text_anim="drop-in",
    logo_anim="drop-in",
    bt_anim="drop-in",
    banner_padding=20,
    banner_color="#ffffff",
    text_color="#000000",
):
    """
    Build a single branded short using a two-pass approach:
    Pass 1: Create Part 1 — full-screen ripped clip (no overlays)
    Pass 2: Create Part 2 — end card with white banner, blurred bg, animated logo
    Pass 3: Concat both parts into the final short
    """
    w, h = get_video_info(clip_path)
    if not w:
        print(f"    Skipping {clip_path}: cannot read video info")
        return False

    bold_font = (
        f"fontfile={font_path}:"
        if font_path
        else "fontfile='/System/Library/Fonts/Supplemental/Arial Bold.ttf':"
    )

    has_logo = logo_path and os.path.exists(logo_path)

    # Apply rounded corners to logo
    if has_logo:
        logo_path = _round_corners(logo_path, radius=30)

    # Auto-size and wrap the top text to fit within the banner
    fontsize_top, text_lines = _auto_fontsize(top_text, SHORTS_WIDTH)
    wrapped_text = "\n".join(text_lines)

    # Calculate banner height based on number of lines
    line_height = int(fontsize_top * 1.3)
    text_block_height = line_height * len(text_lines)
    banner_h = max(BANNER_HEIGHT, text_block_height + 120)
    # Ensure even dimensions (required by libx264)
    if banner_h % 2 != 0:
        banner_h += 1
    video_area_h = SHORTS_HEIGHT - banner_h

    # Temp files
    base = output_path.rsplit(".", 1)[0]
    part1_path = f"{base}_p1.mp4"
    part2_path = f"{base}_p2.mp4"
    top_text_file = f"{base}_toptext.txt"

    # Write wrapped top text to file for ffmpeg textfile= (avoids escaping issues)
    with open(top_text_file, "w") as f:
        f.write(wrapped_text)

    try:
        # ─── Pass 1: Full-screen clip ─────────────────────────────────
        p1_filter = (
            f"[0:v]scale={SHORTS_WIDTH}:{SHORTS_HEIGHT}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={SHORTS_WIDTH}:{SHORTS_HEIGHT},"
            f"setsar=1,fps={FPS}[outv]"
        )
        cmd1 = [
            "ffmpeg", "-y",
            "-i", clip_path,
            "-filter_complex", p1_filter,
            "-map", "[outv]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(clip_duration),
            "-r", str(FPS), "-pix_fmt", "yuv420p",
            part1_path,
        ]
        r1 = subprocess.run(cmd1, capture_output=True, text=True)
        if r1.returncode != 0:
            print(f"    FFmpeg Part1 error: {r1.stderr[-400:]}")
            return False

        # ─── Pass 2: End card ─────────────────────────────────────────
        p2_filters = []

        # Per-element animation presets
        # Sequence: text drops in → logo animates → bottom text appears
        t_anim = ANIMATION_PRESETS.get(text_anim, ANIMATION_PRESETS["drop-in"])
        l_anim = ANIMATION_PRESETS.get(logo_anim, ANIMATION_PRESETS["drop-in"])
        b_anim = ANIMATION_PRESETS.get(bt_anim, ANIMATION_PRESETS["drop-in"])

        text_dur = t_anim["text_dur"]
        text_stagger = t_anim["text_stagger"]
        logo_delay = len(text_lines) * text_stagger + text_dur + 0.05
        logo_dur = l_anim["logo_dur"]
        bt_delay = logo_delay + logo_dur + 0.05

        # Blurred background from last frame of clip
        p2_filters.append(
            f"[0:v]scale={SHORTS_WIDTH}:{video_area_h}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={SHORTS_WIDTH}:{video_area_h},"
            f"setsar=1,"
            f"trim=start={max(0, clip_duration - 0.1)}:end={clip_duration},"
            f"setpts=PTS-STARTPTS,"
            f"tpad=stop_mode=clone:stop_duration={logo_duration},"
            f"trim=0:{logo_duration},setpts=PTS-STARTPTS,"
            f"fps={FPS},"
            f"gblur=sigma=40[bg]"
        )

        # White banner — draw each line separately, each centered
        banner_filter = f"color=white:s={SHORTS_WIDTH}x{banner_h}:d={logo_duration}:r={FPS}"
        line_spacing = int(fontsize_top * 0.35)
        total_text_h = len(text_lines) * fontsize_top + (len(text_lines) - 1) * line_spacing
        start_y = int((banner_h - total_text_h) * 0.6)

        for i, line in enumerate(text_lines):
            escaped_line = _escape_ffmpeg_text(line)
            line_y = start_y + i * (fontsize_top + line_spacing)
            delay = i * text_stagger
            text_alpha = t_anim["text_alpha"](delay, text_dur)
            text_y_expr = t_anim["text_y"](line_y, delay, text_dur)
            banner_filter += (
                f",drawtext={bold_font}"
                f"text='{escaped_line}':"
                f"fontsize={fontsize_top}:fontcolor=black:"
                f"alpha='{text_alpha}':"
                f"x=(w-text_w)/2:y='{text_y_expr}'"
            )

        p2_filters.append(f"{banner_filter}[banner]")

        # Stack banner + blurred bg
        p2_filters.append(f"[banner][bg]vstack[card]")
        last_label = "card"

        # Logo animation
        if has_logo:
            logo_size = 160
            positions = _logo_positions(logo_size, logo_size, banner_h)
            logo_x, logo_y = positions.get(logo_position, positions["center"])

            p2_filters.append(
                f"[1:v]loop=loop={int(logo_duration * FPS)}:size=1:start=0,"
                f"scale={logo_size}:{logo_size},"
                f"format=rgba,"
                f"fade=in:st={logo_delay}:d={logo_dur}:alpha=1,"
                f"trim=0:{logo_duration},setpts=PTS-STARTPTS[logo_ready]"
            )

            logo_y_expr = l_anim["logo_y"](logo_y, logo_delay, logo_dur)
            p2_filters.append(
                f"[{last_label}][logo_ready]overlay="
                f"x={logo_x}:"
                f"y='{logo_y_expr}':"
                f"eof_action=pass[withlogo]"
            )
            last_label = "withlogo"

        # Bottom text
        if bottom_text:
            bottom_y = SHORTS_HEIGHT - 200
            bt_alpha = b_anim["bt_alpha"](bt_delay, b_anim["text_dur"])
            p2_filters.append(
                f"[{last_label}]drawtext={bold_font}"
                f"text='{_escape_ffmpeg_text(bottom_text)}':"
                f"fontsize=44:fontcolor=white:"
                f"borderw=3:bordercolor=black:"
                f"alpha='{bt_alpha}':"
                f"x=(w-text_w)/2:y={bottom_y}[final]"
            )
            last_label = "final"

        p2_filter_graph = ";\n".join(p2_filters)

        cmd2 = ["ffmpeg", "-y", "-i", clip_path]
        if has_logo:
            cmd2 += ["-i", logo_path]
        cmd2 += [
            "-filter_complex", p2_filter_graph,
            "-map", f"[{last_label}]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-t", str(logo_duration),
            "-r", str(FPS), "-pix_fmt", "yuv420p",
            "-an",
            part2_path,
        ]

        r2 = subprocess.run(cmd2, capture_output=True, text=True)
        if r2.returncode != 0:
            print(f"    FFmpeg Part2 error: {r2.stderr[-400:]}")
            return False

        # ─── Pass 3: Concat ───────────────────────────────────────────
        concat_list = f"{base}_concat.txt"
        with open(concat_list, "w") as f:
            f.write(f"file '{os.path.abspath(part1_path)}'\n")
            f.write(f"file '{os.path.abspath(part2_path)}'\n")

        cmd3 = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-r", str(FPS),
            "-movflags", "+faststart",
            output_path,
        ]
        r3 = subprocess.run(cmd3, capture_output=True, text=True)
        if r3.returncode != 0:
            print(f"    FFmpeg Concat error: {r3.stderr[-400:]}")
            return False

        return True

    finally:
        # Clean up temp parts
        for f in [part1_path, part2_path, f"{base}_concat.txt", top_text_file]:
            if os.path.exists(f):
                os.remove(f)


def _escape_ffmpeg_text(text):
    """Escape special characters for ffmpeg drawtext."""
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\u2019")  # Use curly apostrophe
    text = text.replace(":", "\\:")
    text = text.replace("%", "%%")
    text = text.replace("\n", "\\n")
    return text


# ─── Remotion Renderer ──────────────────────────────────────────────────

REMOTION_DIR = Path(__file__).parent / "remotion"


def _check_remotion():
    """Check if the Remotion project is set up (node_modules installed)."""
    if not (REMOTION_DIR / "node_modules").exists():
        print("Remotion not installed. Running npm install...")
        result = subprocess.run(
            ["npm", "install"],
            cwd=str(REMOTION_DIR),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"npm install failed: {result.stderr[-400:]}")
            return False
    return True


def render_endcard_remotion(
    output_path,
    top_text,
    bottom_text="",
    logo_path=None,
    background_path=None,
    logo_duration=2,
    logo_entrance="spring",
    show_device_mockup=False,
    device_screen_path=None,
    device_scale=1.0,
    device_entrance="up",
    banner_padding=20,
    banner_color="#ffffff",
    text_color="#000000",
):
    """Render a branded end card using Remotion instead of FFmpeg filters."""
    if not _check_remotion():
        print("    Falling back to FFmpeg renderer")
        return None

    # Copy assets into remotion/public/ so Remotion can serve them
    public_dir = REMOTION_DIR / "public"
    public_dir.mkdir(exist_ok=True)

    props = {
        "topText": top_text,
        "bottomText": bottom_text,
        "logoSrc": "",
        "backgroundSrc": "",
        "logoEntrance": logo_entrance,
        "showDeviceMockup": show_device_mockup,
        "deviceScreenSrc": "",
        "deviceScale": device_scale,
        "deviceEntrance": device_entrance,
        "bannerPadding": banner_padding,
        "bannerColor": banner_color,
        "textColor": text_color,
    }

    copied_files = []
    if logo_path and os.path.exists(logo_path):
        dest = str(public_dir / "input_logo.png")
        shutil.copy2(logo_path, dest)
        copied_files.append(dest)
        props["logoSrc"] = "input_logo.png"
    if background_path and os.path.exists(background_path):
        dest = str(public_dir / "input_bg.png")
        shutil.copy2(background_path, dest)
        copied_files.append(dest)
        props["backgroundSrc"] = "input_bg.png"
    if device_screen_path and os.path.exists(device_screen_path):
        dest = str(public_dir / "input_device_screen.png")
        shutil.copy2(device_screen_path, dest)
        copied_files.append(dest)
        props["deviceScreenSrc"] = "input_device_screen.png"

    duration_frames = int(logo_duration * FPS)

    cmd = [
        "node", str(REMOTION_DIR / "render.mjs"),
        "--comp", "BrandedEndCard",
        "--output", os.path.abspath(output_path),
        "--props", json.dumps(props),
        "--duration", str(duration_frames),
    ]

    print(f"    Rendering end card with Remotion...")
    result = subprocess.run(cmd, capture_output=True, text=True)

    # Clean up copied assets
    for f in copied_files:
        if os.path.exists(f):
            os.remove(f)

    if result.returncode != 0:
        error_output = (result.stderr or "") + (result.stdout or "")
        print(f"    Remotion render failed: {error_output[-600:]}")
        return None

    if not os.path.exists(output_path):
        print(f"    Remotion render produced no output file")
        return None

    return output_path


def build_short_remotion(
    clip_path,
    output_path,
    logo_path,
    top_text,
    bottom_text,
    clip_duration,
    logo_duration,
    logo_entrance="spring",
    show_device_mockup=False,
    device_screen_path=None,
    device_scale=1.0,
    device_entrance="up",
    banner_padding=20,
    banner_color="#ffffff",
    text_color="#000000",
):
    """
    Build a branded short using Remotion for the end card.
    Pass 1: FFmpeg — full-screen ripped clip (same as before)
    Pass 2: Remotion — animated end card with spring physics
    Pass 3: FFmpeg — concat both parts
    """
    w, h = get_video_info(clip_path)
    if not w:
        print(f"    Skipping {clip_path}: cannot read video info")
        return False

    base = output_path.rsplit(".", 1)[0]
    part1_path = f"{base}_p1.mp4"
    part2_path = f"{base}_p2.mp4"

    # Apply rounded corners to logo
    if logo_path and os.path.exists(logo_path):
        logo_path = _round_corners(logo_path, radius=30)

    # Extract a background frame from the clip for the end card
    bg_frame_path = f"{base}_bg.png"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(max(0, clip_duration - 0.1)),
            "-i", clip_path,
            "-frames:v", "1",
            bg_frame_path,
        ],
        capture_output=True, text=True, check=False,
    )

    try:
        # ─── Pass 1: Full-screen clip (reuses FFmpeg) ─────────────────
        p1_filter = (
            f"[0:v]scale={SHORTS_WIDTH}:{SHORTS_HEIGHT}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={SHORTS_WIDTH}:{SHORTS_HEIGHT},"
            f"setsar=1,fps={FPS}[outv]"
        )
        cmd1 = [
            "ffmpeg", "-y",
            "-i", clip_path,
            "-filter_complex", p1_filter,
            "-map", "[outv]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(clip_duration),
            "-r", str(FPS), "-pix_fmt", "yuv420p",
            part1_path,
        ]
        r1 = subprocess.run(cmd1, capture_output=True, text=True)
        if r1.returncode != 0:
            print(f"    FFmpeg Part1 error: {r1.stderr[-400:]}")
            return False

        if not os.path.exists(part1_path):
            print(f"    Part 1 was not created")
            return False

        # ─── Pass 2: Remotion end card ────────────────────────────────
        bg_path = bg_frame_path if os.path.exists(bg_frame_path) else None
        rendered = render_endcard_remotion(
            output_path=part2_path,
            top_text=top_text,
            bottom_text=bottom_text,
            logo_path=logo_path,
            background_path=bg_path,
            logo_duration=logo_duration,
            logo_entrance=logo_entrance,
            show_device_mockup=show_device_mockup,
            device_screen_path=device_screen_path,
            device_scale=device_scale,
            device_entrance=device_entrance,
            banner_padding=banner_padding,
            banner_color=banner_color,
            text_color=text_color,
        )

        if not rendered:
            print("    Remotion render failed")
            return False

        # ─── Normalize Part 2: add silent audio + force yuv420p ──────
        part2_norm = f"{base}_p2_norm.mp4"
        norm_cmd = [
            "ffmpeg", "-y",
            "-i", part2_path,
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            "-r", str(FPS),
            part2_norm,
        ]
        rn = subprocess.run(norm_cmd, capture_output=True, text=True)
        if rn.returncode != 0:
            print(f"    FFmpeg normalize error: {rn.stderr[-400:]}")
            part2_norm = part2_path

        # ─── Pass 3: Concat ───────────────────────────────────────────
        concat_list = f"{base}_concat.txt"
        with open(concat_list, "w") as f:
            f.write(f"file '{os.path.abspath(part1_path)}'\n")
            f.write(f"file '{os.path.abspath(part2_norm)}'\n")

        cmd3 = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-r", str(FPS),
            "-movflags", "+faststart",
            output_path,
        ]
        r3 = subprocess.run(cmd3, capture_output=True, text=True)
        if r3.returncode != 0:
            print(f"    FFmpeg Concat error: {r3.stderr[-400:]}")
            return False

        return True

    finally:
        for f in [
            part1_path, part2_path, bg_frame_path,
            f"{base}_p2_norm.mp4", f"{base}_concat.txt",
        ]:
            if os.path.exists(f):
                os.remove(f)


def main():
    parser = argparse.ArgumentParser(
        description="Rip YouTube clips and brand them as shorts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # From a channel (grabs latest videos):
  python3 yt_ripper.py --source "https://youtube.com/@ZachKing" \\
      --logo logo.png --top-text "Get Fitnit now!" --max-videos 5

  # From a playlist:
  python3 yt_ripper.py --source "https://youtube.com/playlist?list=PLxxx" \\
      --logo logo.png --top-text "Download Fitnit"

  # Single video:
  python3 yt_ripper.py --source "https://youtube.com/watch?v=xxxxx" \\
      --logo logo.png --top-text "Try Fitnit"
        """,
    )
    parser.add_argument(
        "--source", required=True,
        help="YouTube channel URL, playlist URL, or video URL"
    )
    parser.add_argument(
        "--logo", default=None,
        help="Path to logo image (PNG with transparency recommended)"
    )
    parser.add_argument(
        "--top-text", default="Download Fitnit now!",
        help="Text for the white banner at the top"
    )
    parser.add_argument(
        "--bottom-text", default="",
        help="Text overlay near the bottom of the video"
    )
    parser.add_argument(
        "--clip-duration", type=float, default=DEFAULT_CLIP_DURATION,
        help=f"Seconds to rip from each video (default: {DEFAULT_CLIP_DURATION})"
    )
    parser.add_argument(
        "--logo-duration", type=float, default=DEFAULT_LOGO_DURATION,
        help=f"Seconds for the end card with logo (default: {DEFAULT_LOGO_DURATION})"
    )
    parser.add_argument(
        "--max-videos", type=int, default=DEFAULT_MAX_VIDEOS,
        help=f"Max videos to process (default: {DEFAULT_MAX_VIDEOS})"
    )
    parser.add_argument(
        "--output-dir", default="./output",
        help="Directory for output shorts (default: ./output)"
    )
    parser.add_argument(
        "--font", default=None,
        help="Path to a .ttf font file (optional, defaults to Arial)"
    )
    parser.add_argument(
        "--logo-position", default="center",
        choices=["center", "top-center", "bottom-center", "bottom-left", "bottom-right", "top-left", "top-right"],
        help="Where to place the logo on the end card (default: center)"
    )
    parser.add_argument(
        "--text-anim", default="drop-in",
        choices=list(ANIMATION_PRESETS.keys()),
        help="Top text animation (default: drop-in)"
    )
    parser.add_argument(
        "--logo-anim", default="drop-in",
        choices=list(ANIMATION_PRESETS.keys()),
        help="Logo animation (default: drop-in)"
    )
    parser.add_argument(
        "--bt-anim", default="drop-in",
        choices=list(ANIMATION_PRESETS.keys()),
        help="Bottom text animation (default: drop-in)"
    )
    parser.add_argument(
        "--renderer", default="ffmpeg",
        choices=["ffmpeg", "remotion"],
        help="Renderer for the end card: ffmpeg (classic) or remotion (spring animations)"
    )
    parser.add_argument(
        "--logo-entrance", default="spring",
        choices=["spring", "spin", "bounce", "fade"],
        help="Logo entrance animation for Remotion renderer (default: spring)"
    )

    args = parser.parse_args()

    # Validate
    if args.logo and not os.path.exists(args.logo):
        print(f"Error: Logo file not found: {args.logo}")
        sys.exit(1)

    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create temp directory for downloads
    tmp_dir = Path(tempfile.mkdtemp(prefix="yt_ripper_"))
    print(f"Temp directory: {tmp_dir}")

    try:
        # Fetch video IDs
        video_ids = fetch_video_urls(args.source, args.max_videos)

        if not video_ids:
            print("No videos found!")
            sys.exit(1)

        success_count = 0

        for i, video_id in enumerate(video_ids, 1):
            print(f"\n[{i}/{len(video_ids)}] Processing {video_id}")

            # Download clip
            clip_path = str(tmp_dir / f"{video_id}.mp4")
            if not download_clip(video_id, clip_path, args.clip_duration):
                print(f"  Skipping {video_id}: download failed")
                continue

            # Build branded short
            output_path = str(output_dir / f"short_{video_id}.mp4")
            print(f"  Building branded short ({args.renderer})...")

            if args.renderer == "remotion":
                success = build_short_remotion(
                    clip_path=clip_path,
                    output_path=output_path,
                    logo_path=args.logo,
                    top_text=args.top_text,
                    bottom_text=args.bottom_text,
                    clip_duration=args.clip_duration,
                    logo_duration=args.logo_duration,
                    logo_entrance=args.logo_entrance,
                )
            else:
                success = build_short(
                    clip_path=clip_path,
                    output_path=output_path,
                    logo_path=args.logo,
                    top_text=args.top_text,
                    bottom_text=args.bottom_text,
                    clip_duration=args.clip_duration,
                    logo_duration=args.logo_duration,
                    font_path=args.font,
                    logo_position=args.logo_position,
                    text_anim=args.text_anim,
                    logo_anim=args.logo_anim,
                    bt_anim=args.bt_anim,
                )

            if success:
                success_count += 1
                print(f"  Done: {output_path}")
            else:
                print(f"  Failed to build short for {video_id}")

        print(f"\nComplete! {success_count}/{len(video_ids)} shorts created in {output_dir}/")

    finally:
        # Clean up temp files
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
