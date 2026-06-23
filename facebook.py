"""
Facebook Graph API wrapper.
Handles photo, video, text posting + analytics fetching.
"""
import logging
import requests
from config import FB_PAGE_ID, FB_ACCESS_TOKEN

log = logging.getLogger(__name__)
BASE = "https://graph.facebook.com/v25.0"


def _p(**extra) -> dict:
    return {"access_token": FB_ACCESS_TOKEN, **extra}


def post_photo(image_path: str, caption: str) -> tuple[str | None, str | None]:
    try:
        with open(image_path, "rb") as f:
            r = requests.post(
                f"{BASE}/{FB_PAGE_ID}/photos",
                data=_p(message=caption),
                files={"source": f},
                timeout=60,
            )
        if r.ok:
            data = r.json()
            return (data.get("post_id") or data.get("id"), None)
        err = f"FB photo {r.status_code}: {r.text}"
        log.error(err)
        return (None, err)
    except Exception as e:
        err = f"FB photo exception: {e}"
        log.error(err)
        return (None, err)


def post_video(video_path: str, description: str) -> tuple[str | None, str | None]:
    try:
        with open(video_path, "rb") as f:
            r = requests.post(
                f"{BASE}/{FB_PAGE_ID}/videos",
                data=_p(description=description),
                files={"source": f},
                timeout=180,
            )
        if r.ok:
            return (r.json().get("id"), None)
        err = f"FB video {r.status_code}: {r.text}"
        log.error(err)
        return (None, err)
    except Exception as e:
        err = f"FB video exception: {e}"
        log.error(err)
        return (None, err)


def post_text(message: str) -> tuple[str | None, str | None]:
    try:
        r = requests.post(
            f"{BASE}/{FB_PAGE_ID}/feed",
            data=_p(message=message),
            timeout=30,
        )
        if r.ok:
            return (r.json().get("id"), None)
        err = f"FB text {r.status_code}: {r.text}"
        log.error(err)
        return (None, err)
    except Exception as e:
        err = f"FB text exception: {e}"
        log.error(err)
        return (None, err)


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
