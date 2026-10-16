import os
import re
import uuid
import base64
import logging
from pathlib import Path
from typing import Dict, Any, List
import yt_dlp

logger = logging.getLogger(__name__)

# 支援 x.com, twitter.com, vxtwitter.com, fxtwitter.com, fixupx.com 等網址
TWITTER_URL_PATTERN = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:twitter\.com|x\.com|fxtwitter\.com|vxtwitter\.com|fixupx\.com)/[a-zA-Z0-9_]+/status/\d+(?:\?[^\s]*)?"
)


def extract_twitter_urls(text: str) -> List[str]:
    """從文字訊息中擷取所有 Twitter / X 的推文連結，並統一轉為標準網址以供下載"""
    raw_urls = TWITTER_URL_PATTERN.findall(text)
    normalized_urls = []
    for url in raw_urls:
        # 將第三方預覽網域轉回 x.com 讓 yt-dlp 處理
        clean_url = re.sub(
            r"https?://(?:www\.|mobile\.)?(?:fxtwitter\.com|vxtwitter\.com|fixupx\.com)",
            "https://x.com",
            url,
        )
        # 移除多餘的追蹤參數
        clean_url = clean_url.split("?")[0]
        if clean_url not in normalized_urls:
            normalized_urls.append(clean_url)
    return normalized_urls


def _prepare_cookies_file(temp_dir: Path) -> str | None:
    """如果環境變數有設定 TWITTER_COOKIES_BASE64，將其還原為暫存 cookies.txt"""
    cookies_b64 = os.getenv("TWITTER_COOKIES_BASE64", "").strip()
    if not cookies_b64:
        return None
    try:
        cookies_content = base64.b64decode(cookies_b64).decode("utf-8")
        cookie_path = temp_dir / "cookies.txt"
        cookie_path.write_text(cookies_content, encoding="utf-8")
        return str(cookie_path)
    except Exception as e:
        logger.warning(f"解析 TWITTER_COOKIES_BASE64 失敗: {e}")
        return None


def download_twitter_video(url: str, output_dir: str = "temp_downloads") -> Dict[str, Any]:
    """
    使用 yt-dlp 下載 X/Twitter 影片（若單則推文有多部影片也會一併處理）。
    回傳包含檔案路徑、標題、作者等資訊的字典。
    """
    temp_path = Path(output_dir)
    temp_path.mkdir(parents=True, exist_ok=True)

    task_id = uuid.uuid4().hex[:8]
    output_template = str(temp_path / f"%(uploader_id)s_%(id)s_{task_id}.%(ext)s")

    ydl_opts: Dict[str, Any] = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "writethumbnail": False,
        "noplaylist": False,  # 允許同一則推文中的多個影片
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            )
        },
    }

    cookie_file = _prepare_cookies_file(temp_path)
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file

    downloaded_files: List[Dict[str, Any]] = []

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            raise ValueError("無法取得推文影片資訊。")

        entries = info.get("entries", [info])
        for entry in entries:
            if not entry:
                continue
            file_path = ydl.prepare_filename(entry)
            # 若發生合併，副檔名會是 mp4
            if not os.path.exists(file_path):
                mp4_path = os.path.splitext(file_path)[0] + ".mp4"
                if os.path.exists(mp4_path):
                    file_path = mp4_path

            if os.path.exists(file_path):
                uploader = entry.get("uploader_id") or entry.get("uploader") or "unknown"
                tweet_id = entry.get("id") or task_id
                title = (entry.get("title") or entry.get("description") or f"Twitter_{tweet_id}").strip()
                # 整理安全的雲端檔案名稱
                safe_filename = f"@{uploader}_{tweet_id}.mp4"

                downloaded_files.append(
                    {
                        "local_path": file_path,
                        "filename": safe_filename,
                        "uploader": uploader,
                        "tweet_id": tweet_id,
                        "title": title[:100],
                        "thumbnail": entry.get("thumbnail"),
                        "duration": entry.get("duration") or 0,
                    }
                )

    if not downloaded_files:
        raise ValueError("這則推文中找不到可下載的影片（可能是純圖片或文字推文）。")

    return {
        "original_url": url,
        "files": downloaded_files,
    }
