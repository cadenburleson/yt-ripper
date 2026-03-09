# yt-ripper

Rip the first few seconds of YouTube videos and turn them into branded shorts — white banner with text at the top, your logo centered on the clip, and a blurred end card.

## Requirements

- Python 3.9+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) (`brew install yt-dlp`)
- [FFmpeg](https://ffmpeg.org/) (`brew install ffmpeg`)

## Usage

```bash
python3 yt_ripper.py --source <URL> [options]
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `--source` | YouTube channel, playlist, or video URL | **(required)** |
| `--logo` | Path to logo PNG (transparency recommended) | none |
| `--top-text` | Bold text in the white banner at the top | `"Download Fitnit now!"` |
| `--bottom-text` | Text overlay near the bottom of the video | none |
| `--clip-duration` | Seconds to rip from each video | `3` |
| `--logo-duration` | Seconds for the end card (blurred + logo) | `2` |
| `--max-videos` | Max number of videos to process | `10` |
| `--output-dir` | Where to save the output shorts | `./output` |
| `--font` | Path to a custom `.ttf` font file | Arial Bold |

### Examples

**From a channel** (grabs the latest videos):
```bash
python3 yt_ripper.py \
    --source "https://youtube.com/@ZachKing" \
    --logo logo.png \
    --top-text "Get Fitnit now!" \
    --bottom-text "link in bio" \
    --max-videos 5
```

**From a playlist**:
```bash
python3 yt_ripper.py \
    --source "https://youtube.com/playlist?list=PLxxxxx" \
    --logo logo.png \
    --top-text "Download Fitnit - it's free!" \
    --max-videos 20
```

**Single video**:
```bash
python3 yt_ripper.py \
    --source "https://youtube.com/watch?v=dQw4w9WgXcQ" \
    --logo logo.png \
    --top-text "Try Fitnit today"
```

## Output

Each video produces a single 1080x1920 MP4 short in the output directory:

```
output/
  short_<video_id>.mp4
  short_<video_id>.mp4
  ...
```

Each short contains:
1. **White banner** at the top with your custom text (bold)
2. **Clip** — first N seconds of the source video, cropped to fill 9:16
3. **Logo** centered on the video (if provided)
4. **Bottom text** overlay (if provided)
5. **End card** — frozen last frame with heavy blur + logo for the logo duration
