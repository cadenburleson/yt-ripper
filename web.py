#!/usr/bin/env python3
"""
yt-ripper web interface.
Run with: python3 web.py
Then open http://localhost:5001
"""

import json
import os
import queue
import shutil
import tempfile
import threading
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

import yt_ripper

app = Flask(__name__, template_folder="templates", static_folder="static")

# Store job progress per session
jobs = {}


class Job:
    def __init__(self):
        self.id = str(uuid.uuid4())[:8]
        self.queue = queue.Queue()
        self.running = False
        self.cancelled = False

    def send(self, event, data):
        self.queue.put(f"event: {event}\ndata: {json.dumps(data)}\n\n")

    def log(self, message):
        self.send("log", {"message": message})

    def progress(self, current, total, video_id=""):
        self.send("progress", {"current": current, "total": total, "video_id": video_id})

    def done(self, success_count, total):
        self.send("done", {"success": success_count, "total": total})
        self.running = False

    def error(self, message):
        self.send("error", {"message": message})
        self.running = False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def start_job():
    data = request.form
    logo_file = request.files.get("logo")

    source = data.get("source", "").strip()
    if not source:
        return jsonify({"error": "Source URL is required"}), 400

    output_dir = data.get("output_dir", "").strip() or "./output"
    output_dir = os.path.expanduser(output_dir)

    top_text = data.get("top_text", "").strip() or "Download Fitnit now!"
    bottom_text = data.get("bottom_text", "").strip()
    clip_duration = float(data.get("clip_duration", 3))
    logo_duration = float(data.get("logo_duration", 2))
    max_videos = int(data.get("max_videos", 10))
    logo_position = data.get("logo_position", "center").strip()
    text_anim = data.get("text_anim", "drop-in").strip()
    logo_anim = data.get("logo_anim", "drop-in").strip()
    bt_anim = data.get("bt_anim", "drop-in").strip()

    # Save uploaded logo to temp file
    logo_path = None
    if logo_file and logo_file.filename:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        logo_file.save(tmp.name)
        logo_path = tmp.name

    job = Job()
    jobs[job.id] = job
    job.running = True

    def run():
        try:
            os.makedirs(output_dir, exist_ok=True)

            job.log(f"Top text: {top_text}")
            job.log(f"Bottom text: {bottom_text}")
            job.log(f"Logo position: {logo_position}")
            job.log(f"Fetching videos from: {source}")
            video_ids = yt_ripper.fetch_video_urls(source, max_videos)

            if not video_ids:
                job.error("No videos found!")
                return

            job.log(f"Found {len(video_ids)} videos")

            tmp_dir = Path(tempfile.mkdtemp(prefix="yt_ripper_"))
            success_count = 0

            try:
                for i, video_id in enumerate(video_ids, 1):
                    if job.cancelled:
                        job.log("Cancelled by user.")
                        break

                    job.progress(i, len(video_ids), video_id)
                    job.log(f"[{i}/{len(video_ids)}] Downloading {video_id}...")

                    clip_path = str(tmp_dir / f"{video_id}.mp4")
                    if not yt_ripper.download_clip(video_id, clip_path, clip_duration):
                        job.log(f"  Skipped {video_id}: download failed")
                        continue

                    job.log(f"  Building short for {video_id}...")
                    out_path = str(Path(output_dir) / f"short_{video_id}.mp4")
                    if yt_ripper.build_short(
                        clip_path=clip_path,
                        output_path=out_path,
                        logo_path=logo_path,
                        top_text=top_text,
                        bottom_text=bottom_text,
                        clip_duration=clip_duration,
                        logo_duration=logo_duration,
                        logo_position=logo_position,
                        text_anim=text_anim,
                        logo_anim=logo_anim,
                        bt_anim=bt_anim,
                    ):
                        success_count += 1
                        job.log(f"  Done: {out_path}")
                    else:
                        job.log(f"  Failed to build short for {video_id}")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

            job.done(success_count, len(video_ids))

        except Exception as e:
            job.error(str(e))
        finally:
            if logo_path:
                os.unlink(logo_path)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return jsonify({"job_id": job.id})


@app.route("/api/stream/<job_id>")
def stream(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    def generate():
        while True:
            try:
                msg = job.queue.get(timeout=30)
                yield msg
            except queue.Empty:
                yield "event: ping\ndata: {}\n\n"
                if not job.running:
                    break

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/cancel/<job_id>", methods=["POST"])
def cancel(job_id):
    job = jobs.get(job_id)
    if job:
        job.cancelled = True
        return jsonify({"ok": True})
    return jsonify({"error": "Job not found"}), 404


if __name__ == "__main__":
    os.makedirs("templates", exist_ok=True)
    os.makedirs("static", exist_ok=True)
    print("Starting yt-ripper web UI at http://localhost:5001")
    app.run(host="127.0.0.1", port=5001, debug=False)
