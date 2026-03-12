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
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_from_directory, session, redirect, url_for

import yt_ripper
import db
import youtube_uploader

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-in-production")

# Store job progress per session
jobs = {}

# Initialize database
db.init_db()


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


# ─── Scheduler ──────────────────────────────────────────────────────────────

def scheduler_loop():
    """Background scheduler: upload queued videos on a schedule."""
    client_secrets_path = os.getenv("GOOGLE_CLIENT_SECRETS", "./client_secret.json")

    # If GOOGLE_CLIENT_SECRETS is JSON content, write to a temp file
    if client_secrets_path.strip().startswith("{"):
        _tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        _tmp.write(client_secrets_path)
        _tmp.flush()
        client_secrets_path = _tmp.name

    while True:
        try:
            configs = db.get_active_schedulers()

            for config in configs:
                user_id = config["user_id"]
                interval_hours = config["interval_hours"]
                description = config["description"]
                made_for_kids = config.get("made_for_kids", 0)

                # Check if it's time to upload
                last_upload = db.get_last_upload_time(user_id)
                now = time.time()
                due = (
                    last_upload is None
                    or (now - last_upload) >= interval_hours * 3600
                )

                if due:
                    job = db.get_next_queued_job(user_id)
                    if job:
                        try:
                            # Rebuild service and upload
                            user = db.get_user(user_id)
                            if not user:
                                continue

                            service = youtube_uploader.get_service_from_credentials(
                                user["access_token"],
                                user["refresh_token"],
                                user["token_expiry"],
                                client_secrets_path,
                            )

                            # Use description as both title and description
                            title = description
                            yt_id = youtube_uploader.upload_short(
                                service, job["filepath"], title, description, made_for_kids
                            )

                            db.update_upload_job(
                                job["id"], "uploaded", yt_id, datetime.utcnow().isoformat()
                            )
                            print(f"[Scheduler] Uploaded job {job['id']} as {yt_id}")

                        except Exception as e:
                            print(f"[Scheduler] Error uploading job {job['id']}: {e}")
                            db.update_upload_job(job["id"], "failed", None)

        except Exception as e:
            print(f"[Scheduler] Error in scheduler loop: {e}")

        time.sleep(60)  # Check every minute


# Start scheduler thread
scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
scheduler_thread.start()


@app.route("/")
def index():
    return render_template("index.html")


# ─── Authentication Routes ──────────────────────────────────────────────────

@app.route("/auth/google")
def auth_google():
    """Redirect to Google OAuth consent screen."""
    client_secrets_path = os.getenv("GOOGLE_CLIENT_SECRETS", "./client_secret.json")

    # If GOOGLE_CLIENT_SECRETS is JSON content, write to a temp file
    if client_secrets_path.strip().startswith("{"):
        _tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        _tmp.write(client_secrets_path)
        _tmp.flush()
        client_secrets_path = _tmp.name

    if not os.path.exists(client_secrets_path):
        return (
            jsonify(
                {
                    "error": "Google credentials not configured. Set GOOGLE_CLIENT_SECRETS env var."
                }
            ),
            400,
        )

    import json as json_lib
    import secrets
    import urllib.parse

    # Load client secrets
    with open(client_secrets_path) as f:
        client_config = json_lib.load(f)

    cred_type = "web" if "web" in client_config else "installed"
    client_id = client_config[cred_type]["client_id"]

    # Build auth URL manually (avoids PKCE issues)
    redirect_uri = url_for("auth_callback", _external=True)
    state = secrets.token_urlsafe(32)

    scopes = " ".join(youtube_uploader.SCOPES)
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={urllib.parse.quote(client_id)}&"
        f"redirect_uri={urllib.parse.quote(redirect_uri)}&"
        f"response_type=code&"
        f"scope={urllib.parse.quote(scopes)}&"
        f"state={state}&"
        f"access_type=offline"
    )

    session["oauth_state"] = state
    return redirect(auth_url)


@app.route("/auth/callback")
def auth_callback():
    """Handle OAuth token exchange."""
    code = request.args.get("code")

    if not code:
        return jsonify({"error": "No authorization code provided"}), 400

    client_secrets_path = os.getenv("GOOGLE_CLIENT_SECRETS", "./client_secret.json")

    try:
        import requests
        import json as json_lib

        # Load client secrets
        with open(client_secrets_path) as f:
            client_config = json_lib.load(f)

        # Handle both "web" and "installed" credential types
        cred_type = "web" if "web" in client_config else "installed"
        client_id = client_config[cred_type]["client_id"]
        client_secret = client_config[cred_type]["client_secret"]

        # Exchange code for tokens using direct HTTP request (avoids PKCE issues)
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": url_for("auth_callback", _external=True),
            "grant_type": "authorization_code",
        }

        print(f"[AUTH] Requesting tokens with code: {code[:20]}...")
        print(f"[AUTH] Client ID: {client_id}")
        print(f"[AUTH] Redirect URI: {token_data['redirect_uri']}")

        token_response = requests.post(token_url, data=token_data)
        token_json = token_response.json()

        print(f"[AUTH] Token response status: {token_response.status_code}")
        print(f"[AUTH] Token response: {token_json}")

        if not token_response.ok:
            error_msg = token_json.get('error_description', token_json.get('error', str(token_json)))
            print(f"[AUTH] Token exchange error: {error_msg}")
            return jsonify({"error": f"Token exchange failed: {error_msg}"}), 400

        creds = {
            "access_token": token_json.get("access_token"),
            "refresh_token": token_json.get("refresh_token"),
            "token_expiry": token_json.get("expires_in"),
        }

        # Convert expires_in to unix timestamp
        if creds["token_expiry"]:
            creds["token_expiry"] = int(time.time()) + creds["token_expiry"]

        # Decode ID token to get user info (avoids needing YouTube API scope)
        import base64
        id_token = token_json.get("id_token")
        if id_token:
            # Decode JWT (skip signature verification for now in dev)
            parts = id_token.split(".")
            # Add padding if needed
            payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
            decoded = json_lib.loads(base64.urlsafe_b64decode(payload))

            email = decoded.get("email", "")
            user_id = decoded.get("sub", "")

            # Get channel name from email or use a default
            channel_name = email.split("@")[0] if email else f"User {user_id[:8]}"
        else:
            # Fallback: use YouTube API if no ID token
            service = youtube_uploader.get_service_from_credentials(
                creds["access_token"],
                creds["refresh_token"],
                creds["token_expiry"],
                client_secrets_path,
            )
            channels = service.channels().list(part="snippet", mine=True).execute()
            channel = channels["items"][0]
            channel_name = channel["snippet"]["title"]
            email = channel["snippet"]["description"]
            user_id = channel["id"]

        # Upsert user
        db.upsert_user(
            user_id,
            email,
            channel_name,
            creds["access_token"],
            creds["refresh_token"],
            creds["token_expiry"],
        )

        # Set session
        session["user_id"] = user_id
        session.permanent = True

        return redirect("/")

    except Exception as e:
        print(f"Auth callback error: {e}")
        return jsonify({"error": str(e)}), 400


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    """Clear session."""
    session.clear()
    return jsonify({"ok": True})


# ─── API Routes ─────────────────────────────────────────────────────────────

@app.route("/api/me")
def api_me():
    """Return current user info and auth status."""
    user_id = session.get("user_id")

    if not user_id:
        return jsonify({"authenticated": False})

    user = db.get_user(user_id)
    if not user:
        return jsonify({"authenticated": False})

    config = db.get_scheduler_config(user_id)

    return jsonify(
        {
            "authenticated": True,
            "user_id": user_id,
            "email": user["email"],
            "channel_name": user["channel_name"],
            "scheduler": {
                "enabled": bool(config["enabled"]) if config else False,
                "interval_hours": config["interval_hours"] if config else None,
                "output_dir": config["output_dir"] if config else None,
                "description": config["description"] if config else None,
            },
        }
    )


@app.route("/api/upload/status")
def api_upload_status():
    """Return user's jobs and scheduler config."""
    user_id = session.get("user_id")

    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    jobs = db.get_upload_jobs(user_id)
    config = db.get_scheduler_config(user_id)

    return jsonify(
        {
            "jobs": [dict(job) for job in jobs],
            "config": dict(config) if config else None,
        }
    )


@app.route("/api/upload/scan", methods=["POST"])
def api_upload_scan():
    """Scan user's output_dir and enqueue new short_*.mp4 files."""
    user_id = session.get("user_id")

    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    output_dir = request.json.get("output_dir", "./output")
    output_dir = os.path.expanduser(output_dir)

    if not os.path.isdir(output_dir):
        return jsonify({"error": f"Directory not found: {output_dir}"}), 400

    # Get existing jobs to avoid duplicates
    existing_jobs = db.get_upload_jobs(user_id)
    existing_files = {job["filename"] for job in existing_jobs}

    # Scan for new short_*.mp4 files
    new_jobs = []
    for filepath in Path(output_dir).glob("short_*.mp4"):
        filename = filepath.name
        if filename not in existing_files:
            job_id = db.add_upload_job(
                user_id, filename, str(filepath), filename.replace(".mp4", "")
            )
            new_jobs.append({"id": job_id, "filename": filename})

    return jsonify({"enqueued": len(new_jobs), "jobs": new_jobs})


@app.route("/api/upload/schedule", methods=["POST"])
def api_upload_schedule():
    """Save scheduler config and enable it."""
    user_id = session.get("user_id")

    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.json
    interval_hours = float(data.get("interval_hours", 24))
    title_template = data.get("title_template", "")
    description = data.get("description", "")
    output_dir = os.path.expanduser(data.get("output_dir", "./output"))
    made_for_kids = 1 if data.get("made_for_kids") else 0

    db.upsert_scheduler_config(
        user_id, 1, interval_hours, title_template, description, output_dir, made_for_kids
    )

    return jsonify({"ok": True})


@app.route("/api/upload/stop", methods=["POST"])
def api_upload_stop():
    """Disable scheduler for this user."""
    user_id = session.get("user_id")

    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    config = db.get_scheduler_config(user_id)
    if config:
        db.upsert_scheduler_config(
            user_id,
            0,
            config["interval_hours"],
            config["title_template"],
            config["description"],
            config["output_dir"],
            config.get("made_for_kids", 0),
        )

    return jsonify({"ok": True})


@app.route("/api/start", methods=["POST"])
def start_job():
    data = request.form
    logo_file = request.files.get("logo")
    device_screen_file = request.files.get("device_screen")

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
    renderer = data.get("renderer", "ffmpeg").strip()
    logo_entrance = data.get("logo_entrance", "spring").strip()
    show_device_mockup = data.get("show_device_mockup") == "on"
    device_scale = float(data.get("device_scale", "1.0"))
    device_entrance = data.get("device_entrance", "up").strip()

    # Save uploaded logo to temp file
    logo_path = None
    if logo_file and logo_file.filename:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        logo_file.save(tmp.name)
        logo_path = tmp.name

    # Save uploaded device screen to temp file
    device_screen_path = None
    if device_screen_file and device_screen_file.filename:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        device_screen_file.save(tmp.name)
        device_screen_path = tmp.name

    job = Job()
    jobs[job.id] = job
    job.running = True

    def run():
        try:
            os.makedirs(output_dir, exist_ok=True)

            job.log(f"Renderer: {renderer}")
            job.log(f"Top text: {top_text}")
            job.log(f"Bottom text: {bottom_text}")
            if renderer == "ffmpeg":
                job.log(f"Logo position: {logo_position}")
            else:
                job.log(f"Logo entrance: {logo_entrance}")
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

                    job.log(f"  Building short for {video_id} ({renderer})...")
                    out_path = str(Path(output_dir) / f"short_{video_id}.mp4")

                    if renderer == "remotion":
                        build_ok = yt_ripper.build_short_remotion(
                            clip_path=clip_path,
                            output_path=out_path,
                            logo_path=logo_path,
                            top_text=top_text,
                            bottom_text=bottom_text,
                            clip_duration=clip_duration,
                            logo_duration=logo_duration,
                            logo_entrance=logo_entrance,
                            show_device_mockup=show_device_mockup,
                            device_screen_path=device_screen_path,
                            device_scale=device_scale,
                            device_entrance=device_entrance,
                        )
                    else:
                        build_ok = yt_ripper.build_short(
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
                        )

                    if build_ok:
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
            if device_screen_path:
                os.unlink(device_screen_path)

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
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5001"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    print(f"Starting yt-ripper web UI at http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)
