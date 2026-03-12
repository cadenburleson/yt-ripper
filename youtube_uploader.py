"""YouTube upload and OAuth handling for yt-ripper."""

import json
import os
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/youtube.upload",
]


def get_auth_url(client_secrets_path, redirect_uri, state=None):
    """Return Google OAuth consent URL."""
    flow = Flow.from_client_secrets_file(
        client_secrets_path, scopes=SCOPES, redirect_uri=redirect_uri
    )
    auth_url, auth_state = flow.authorization_url(state=state)
    return auth_url, auth_state


def exchange_code(client_secrets_path, redirect_uri, code):
    """Exchange authorization code for credentials."""
    flow = Flow.from_client_secrets_file(
        client_secrets_path, scopes=SCOPES, redirect_uri=redirect_uri
    )
    # Disable PKCE for local development (can be re-enabled for production)
    flow.code_verifier = None
    flow.fetch_token(code=code)
    credentials = flow.credentials

    return {
        "access_token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_expiry": int(credentials.expiry.timestamp())
        if credentials.expiry
        else None,
    }


def get_service_from_credentials(
    access_token, refresh_token, token_expiry, client_secrets_path
):
    """Rebuild Credentials object and return YouTube service."""
    # Load client ID and secret from client_secrets_path
    with open(client_secrets_path, "r") as f:
        client_config = json.load(f)

    # Handle both "web" and "installed" credential types
    cred_type = "web" if "web" in client_config else "installed"
    client_id = client_config[cred_type]["client_id"]
    client_secret = client_config[cred_type]["client_secret"]

    # Rebuild credentials
    credentials = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )

    # Check if expired and refresh if needed
    if credentials.expired:
        credentials.refresh(Request())

    # Build and return YouTube service
    return build("youtube", "v3", credentials=credentials)


def upload_short(service, video_path, title, description="", made_for_kids=False):
    """
    Upload a video to YouTube as a Short.
    Returns the YouTube video ID on success, raises exception on failure.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    # Append #Shorts to description
    if description:
        description = description + "\n\n#Shorts"
    else:
        description = "#Shorts"

    request_body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": "22",  # People & Blogs
            "tags": ["shorts"],
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": bool(made_for_kids),
        },
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)

    request = service.videos().insert(
        part="snippet,status", body=request_body, media_body=media
    )

    response = None
    while response is None:
        status, response = request.next_chunk()

    return response["id"]
