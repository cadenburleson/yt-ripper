# yt-ripper Multi-User SaaS Implementation

## What's Been Implemented

### 1. **Database Layer** (`db.py`)
- SQLite wrapper with row factory for dict-like access
- Tables: `users`, `upload_jobs`, `scheduler_config`
- Functions for user management, scheduler configuration, and job tracking
- Support for multiple users with isolated state

### 2. **YouTube Integration** (`youtube_uploader.py`)
- Google OAuth 2.0 authentication flow
- Scopes: `openid`, `userinfo.email`, `youtube.upload`
- Token refresh logic with automatic expiry handling
- Video upload with metadata (title, description, category=People & Blogs, privacy=public)
- Automatic "#Shorts" tag addition to descriptions

### 3. **Flask Web Application** (`web.py`)
Enhanced with:
- **Session Management**: Flask signed cookies for user persistence
- **Authentication Routes**:
  - `/auth/google` - Redirect to Google consent screen
  - `/auth/callback` - Handle OAuth token exchange, upsert user, set session
  - `/auth/logout` - Clear session
- **API Routes**:
  - `/api/me` - Get current user info + scheduler status
  - `/api/upload/status` - List user's jobs + config
  - `/api/upload/scan` - Scan output directory for new `short_*.mp4` files
  - `/api/upload/schedule` - Enable scheduler with config
  - `/api/upload/stop` - Disable scheduler
- **Background Scheduler**:
  - Single global thread checking all active users every minute
  - Uploads queued jobs when interval elapsed
  - Auto-refreshes tokens, handles errors gracefully

### 4. **Frontend UI** (`templates/index.html`)
- Auth header with "Connect YouTube" button / "Connected as..." badge
- New "Auto-Post to YouTube" section (shows when authenticated):
  - Output folder input
  - Post interval (hours) with fine granularity (0.01 hours = 36 seconds for testing)
  - Description field
  - "Scan for Videos" button - finds new `short_*.mp4` files
  - "Start/Stop Scheduling" toggle button
  - Upload queue table showing:
    - Filename, title, status badge (queued/uploaded/failed)
    - Last upload date
    - Link to watch on YouTube
  - Polls status every 15 seconds when scheduler is active

### 5. **Configuration**
- `.gitignore` - Updated to exclude `app.db`, `token*.json`, `client_secret*.json`
- `requirements.txt` - All dependencies listed

## Setup Instructions

### Prerequisites
1. **Google Cloud Project**
   - Create project at console.cloud.google.com
   - Enable YouTube Data API v3
   - Create OAuth 2.0 consent screen
   - Create OAuth 2.0 credentials (Desktop app)
   - Download as JSON and save as `client_secret.json`

2. **Environment**
   ```bash
   export GOOGLE_CLIENT_SECRETS=./client_secret.json
   export FLASK_SECRET_KEY=your-secret-key-here  # Optional, changes in production
   ```

### Installation
```bash
pip install -r requirements.txt
python3 web.py
# Visit http://localhost:5001
```

### First Run
1. Click "Connect YouTube" → authorize with Google account
2. Confirm channel name appears as "Connected as ..."
3. Run short ripper to create `short_*.mp4` files in output folder
4. Click "Scan for Videos" → see videos enqueued
5. Set interval (e.g., 0.01 for testing = 36 seconds) and description
6. Click "Start Scheduling" → scheduler begins polling every minute
7. First upload happens immediately if due, then on schedule

## Database Schema

```sql
users
  id TEXT PRIMARY KEY                -- YouTube channel ID
  email TEXT                         -- User email
  channel_name TEXT                  -- YouTube channel name
  access_token TEXT                  -- OAuth access token
  refresh_token TEXT                 -- OAuth refresh token (never expires)
  token_expiry INTEGER               -- Unix timestamp of access token expiry

upload_jobs
  id INTEGER PRIMARY KEY AUTOINCREMENT
  user_id TEXT                       -- FK to users
  filename TEXT                      -- e.g. "short_abc123.mp4"
  filepath TEXT                      -- Full path on disk
  youtube_id TEXT                    -- YouTube video ID (null until uploaded)
  title TEXT                         -- Video title
  status TEXT                        -- "queued" | "uploaded" | "failed"
  created_at TEXT                    -- ISO timestamp
  uploaded_at TEXT                   -- ISO timestamp

scheduler_config
  user_id TEXT PRIMARY KEY           -- FK to users
  enabled INTEGER                    -- 0 or 1
  interval_hours REAL                -- Hours between uploads (e.g., 24.0)
  title_template TEXT                -- Template for video titles (unused for now)
  description TEXT                   -- Description appended to all uploads + "#Shorts"
  output_dir TEXT                    -- Path to scan for videos
```

## How It Works

### User Flow
1. **Auth**: User clicks "Connect YouTube" → Google OAuth → channel info extracted → stored in DB + session
2. **Upload Queue**: User runs short ripper, gets `short_*.mp4` files
3. **Scan**: User clicks "Scan for Videos" → API finds new files → adds to `upload_jobs` table as "queued"
4. **Schedule**: User sets interval and description → saves to `scheduler_config` + enables (enabled=1)
5. **Upload**: Background scheduler checks every user with active schedule once per minute:
   - If `now - last_upload >= interval_hours * 3600`, mark as due
   - If due, take oldest "queued" job and upload to YouTube
   - Update job status to "uploaded" + set youtube_id + set uploaded_at timestamp
6. **Monitor**: UI polls `/api/upload/status` every 15s, shows upload queue in real-time

### Scheduler Details
- **Thread**: Single background thread, runs forever, checks every 60 seconds
- **Per-User Check**: Gets all scheduler configs where `enabled=1`, loops through each
- **Timing**: Uses `uploaded_at` timestamps to determine if due (falls back to creating new uploads if no prior uploads)
- **Token Refresh**: Credentials are rebuilt from `access_token` + `refresh_token` + `expiry`; Google SDK auto-refreshes if expired
- **Error Handling**: Job moves to "failed" status if upload throws; doesn't block other users

### Multi-User Isolation
- Each user has isolated rows in all tables (via `user_id` FK)
- Each user's tokens are stored separately (can revoke at any time)
- Scheduler iterates all active users independently
- No shared state between users

## Production Considerations

1. **OAuth Verification**: For >100 users, submit app for Google OAuth verification (remove "unverified app" warning)
2. **Quota**: YouTube Data API = 10,000 units/day; each upload = 1,600 units → ~6 uploads/day
   - Monitor usage at console.cloud.google.com, request quota increase as needed
3. **Database**: SQLite works great for small deployments; migrate to PostgreSQL/MySQL for larger scale
4. **Secrets**: Store `client_secret.json` securely (env var or secrets manager); rotate regularly
5. **Rate Limiting**: Consider rate-limiting `/api/upload/scan` to prevent abuse
6. **Logging**: Add structured logging for debugging; log all uploads + errors
7. **Monitoring**: Track scheduler health (e.g., check last heartbeat time); alert if scheduler dies
8. **Session Security**: Change `FLASK_SECRET_KEY` in production; use `session.permanent_lifetime` to control session duration

## Verification Checklist

- [ ] `pip install -r requirements.txt` succeeds
- [ ] `export GOOGLE_CLIENT_SECRETS=./client_secret.json` (ensure file exists)
- [ ] `python3 web.py` starts without errors
- [ ] Visit localhost:5001, see "Connect YouTube" button
- [ ] Click button, complete Google OAuth flow
- [ ] See channel name + auto-post section appears
- [ ] Generate a few `short_*.mp4` files in output folder
- [ ] Click "Scan for Videos" → see videos in queue
- [ ] Set interval to 0.01 hours (36 seconds for testing)
- [ ] Click "Start Scheduling" → button text changes to "Stop Scheduling" (red)
- [ ] Wait ~1 minute for scheduler to run
- [ ] First video should upload → status changes to "uploaded"
- [ ] Check `app.db` with `sqlite3` to see uploaded_at + youtube_id populated
- [ ] Repeat: create 2-3 more videos, scan, watch them upload on schedule

## Files Created/Modified

| File | Change |
|------|--------|
| `db.py` | NEW |
| `youtube_uploader.py` | NEW |
| `requirements.txt` | NEW |
| `web.py` | Modified (added auth, API routes, scheduler) |
| `templates/index.html` | Modified (added auth UI, auto-post section, poll logic) |
| `.gitignore` | Modified (added app.db, token*.json, client_secret*.json) |
| `app.db` | AUTO-CREATED on first run |
