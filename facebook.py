"""
Facebook Graph API wrapper.
Handles photo, video, text posting + analytics fetching.
"""
import logging
import requests
from config import FB_PAGE_ID, FB_ACCESS_TOKEN

log = logging.getLogger(__name__)
BASE = "https://graph.facebook.com/v19.0"


def _p(**extra) -> dict:
    return {"access_token": FB_ACCESS_TOKEN, **extra}


def post_photo(image_path: str, caption: str) -> str | None:
    try:
        with open(image_path, "rb") as f:
            r = requests.post(
                f"{BASE}/{FB_PAGE_ID}/photos",
                data=_p(caption=caption),
                files={"source": f},
                timeout=60,
            )
        if r.ok:
            data = r.json()
            return data.get("post_id") or data.get("id")
        log.error(f"FB photo {r.status_code}: {r.text}")
    except Exception as e:
        log.error(f"FB photo exception: {e}")
    return None


def post_video(video_path: str, description: str) -> str | None:
    try:
        with open(video_path, "rb") as f:
            r = requests.post(
                f"{BASE}/{FB_PAGE_ID}/videos",
                data=_p(description=description),
                files={"source": f},
                timeout=180,
            )
        if r.ok:
            return r.json().get("id")
        log.error(f"FB video {r.status_code}: {r.text}")
    except Exception as e:
        log.error(f"FB video exception: {e}")
    return None


def post_text(message: str) -> str | None:
    try:
        r = requests.post(
            f"{BASE}/{FB_PAGE_ID}/feed",
            data=_p(message=message),
            timeout=30,
        )
        if r.ok:
            return r.json().get("id")
        log.error(f"FB text {r.status_code}: {r.text}")
    except Exception as e:
        log.error(f"FB text exception: {e}")
    return None


def fetch_analytics(fb_post_id: str) -> dict:
    """Fetch likes, comments, shares for a post."""
    try:
        r = requests.get(
            f"{BASE}/{fb_post_id}",
            params={
                "fields": "likes.summary(true),comments.summary(true),shares",
                "access_token": FB_ACCESS_TOKEN,
            },
            timeout=30,
        )
        if not r.ok:
            log.warning(f"Analytics {r.status_code}: {r.text}")
            return {}
        data = r.json()
        return {
            "likes":    data.get("likes",    {}).get("summary", {}).get("total_count", 0),
            "comments": data.get("comments", {}).get("summary", {}).get("total_count", 0),
            "shares":   data.get("shares",   {}).get("count", 0),
            "reach":    0,
            "impressions": 0,
        }
    except Exception as e:
        log.error(f"FB analytics exception: {e}")
        return {}
