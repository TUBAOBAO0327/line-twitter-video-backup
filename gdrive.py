import os
import json
import logging
from typing import Dict, Any
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]


def get_drive_service():
    """
    建立並回傳 Google Drive API Service。
    優先使用 OAuth 2.0 (GOOGLE_REFRESH_TOKEN)，因為個人 Google Drive 的 Service Account 沒有儲存空間配額。
    若未設定 OAuth，則嘗試讀取 GOOGLE_SERVICE_ACCOUNT_JSON。
    """
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN", "").strip()
    sa_json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

    creds = None

    if client_id and client_secret and refresh_token:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )
        creds.refresh(Request())
    elif sa_json_str:
        sa_info = json.loads(sa_json_str)
        creds = service_account.Credentials.from_service_account_info(
            sa_info, scopes=SCOPES
        )
    elif os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
    elif os.path.exists("service_account.json"):
        creds = service_account.Credentials.from_service_account_file(
            "service_account.json", scopes=SCOPES
        )
    else:
        raise RuntimeError(
            "尚未設定 Google Drive 憑證！請設定 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN "
            "或執行 python auth_gdrive.py 產生憑證。"
        )

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_to_drive(
    local_file_path: str,
    filename: str,
    folder_id: str | None = None,
    description: str = "",
) -> Dict[str, Any]:
    """
    將本地影片檔案上傳至 Google Drive 指定資料夾，並回傳檔案檢視連結。
    """
    service = get_drive_service()
    target_folder = folder_id or os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()

    file_metadata: Dict[str, Any] = {
        "name": filename,
        "description": description,
    }
    if target_folder:
        file_metadata["parents"] = [target_folder]

    media = MediaFileUpload(
        local_file_path,
        mimetype="video/mp4",
        resumable=True,
    )

    uploaded_file = (
        service.files()
        .create(
            body=file_metadata,
            media_body=media,
            fields="id, name, webViewLink, webContentLink, size",
            supportsAllDrives=True,
        )
        .execute()
    )

    return {
        "file_id": uploaded_file.get("id"),
        "name": uploaded_file.get("name"),
        "web_view_link": uploaded_file.get("webViewLink"),
        "web_content_link": uploaded_file.get("webContentLink"),
        "size_bytes": int(uploaded_file.get("size", 0)),
    }
