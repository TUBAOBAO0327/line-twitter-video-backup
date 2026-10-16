import os
import logging
import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    AsyncApiClient,
    AsyncMessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from downloader import extract_twitter_urls, download_twitter_video
from gdrive import upload_to_drive

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("line-twitter-backup-bot")

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()

app = FastAPI(title="LINE Bot Twitter/X Video Backup to Google Drive")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
async_api_client = AsyncApiClient(configuration)
line_bot_api = AsyncMessagingApi(async_api_client)
parser = WebhookParser(LINE_CHANNEL_SECRET)


@app.get("/")
async def health_check():
    """提供雲端平台 (Render / Railway / Zeabur) 健康檢查用的端點"""
    return {
        "status": "ok",
        "service": "LINE Bot Twitter Video Backup",
        "gdrive_folder_configured": bool(GOOGLE_DRIVE_FOLDER_ID),
    }


def _sync_download_and_upload(url: str) -> list[dict]:
    """在背景執行緒中執行同步的 yt-dlp 下載與 Google Drive 上傳，並自動清理暫存檔"""
    result = download_twitter_video(url)
    uploaded_results = []

    for item in result["files"]:
        local_path = item["local_path"]
        try:
            drive_info = upload_to_drive(
                local_file_path=local_path,
                filename=item["filename"],
                folder_id=GOOGLE_DRIVE_FOLDER_ID or None,
                description=f"Source: {url}\nTitle: {item['title']}",
            )
            uploaded_results.append(
                {
                    "filename": item["filename"],
                    "title": item["title"],
                    "uploader": item["uploader"],
                    "web_view_link": drive_info["web_view_link"],
                    "size_mb": round(drive_info["size_bytes"] / (1024 * 1024), 2),
                }
            )
        finally:
            # 確保上傳完成或失敗後都刪除本地暫存檔，避免塞爆雲端容器空間
            if os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except OSError as e:
                    logger.warning(f"刪除暫存檔失敗 {local_path}: {e}")

    return uploaded_results


async def process_twitter_urls_task(user_id: str, urls: list[str]):
    """非同步背景任務：處理一或多個 Twitter 網址並透過 Push Message 或後續通知回報結果"""
    for url in urls:
        try:
            uploaded_items = await asyncio.to_thread(_sync_download_and_upload, url)
            lines = ["✅ 推特影片備份成功！"]
            for idx, info in enumerate(uploaded_items, start=1):
                prefix = f"\n📹 影片 {idx}：" if len(uploaded_items) > 1 else "\n📹 檔案："
                lines.append(f"{prefix}{info['filename']} ({info['size_mb']} MB)")
                lines.append(f"📝 內容：{info['title']}")
                lines.append(f"☁️ 雲端連結：\n{info['web_view_link']}")

            reply_text = "\n".join(lines)
        except Exception as e:
            logger.exception(f"處理網址失敗 {url}: {e}")
            reply_text = f"❌ 備份失敗：\n{url}\n原因：{str(e)}"

        try:
            await line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[TextMessage(text=reply_text)],
                )
            )
        except Exception as push_err:
            logger.error(f"發送 LINE Push 訊息失敗: {push_err}")


@app.post("/callback")
async def callback(request: Request, background_tasks: BackgroundTasks):
    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode("utf-8")

    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid LINE signature")

    for event in events:
        if not isinstance(event, MessageEvent):
            continue
        if not isinstance(event.message, TextMessageContent):
            continue

        text = event.message.text.strip()
        urls = extract_twitter_urls(text)

        if not urls:
            # 如果在私訊中傳了非推特網址，給予提示
            if event.source.type == "user":
                await line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[
                            TextMessage(
                                text="請傳送包含影片的 X (Twitter) 推文連結給我，我會自動幫你下載並備份到 Google Drive！\n\n範例：https://x.com/username/status/123456789"
                            )
                        ],
                    )
                )
            continue

        # 決定通知傳送對象 (支援個人聊天、群組、多人聊天室)
        target_id = (
            getattr(event.source, "group_id", None)
            or getattr(event.source, "room_id", None)
            or getattr(event.source, "user_id", None)
        )

        # 先立刻使用免費的 Reply Token 回覆「處理中」，避免下載太久導致 Reply Token 過期
        await line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text=f"⏳ 收到 {len(urls)} 個推文連結！正在下載影片並上傳至 Google Drive，請稍候..."
                    )
                ],
            )
        )

        if target_id:
            background_tasks.add_task(process_twitter_urls_task, target_id, urls)

    return "OK"
