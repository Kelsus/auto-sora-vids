from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


logger = logging.getLogger(__name__)


@dataclass
class DriveUploader:
    parameter_name: str
    folder_id: str

    def __post_init__(self) -> None:
        self._ssm = boto3.client("ssm")
        self._credentials = None

    def upload(self, file_name: str, data: bytes) -> None:
        credentials = self._load_credentials()
        service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype="video/mp4", resumable=False)
        metadata = {"name": file_name, "parents": [self.folder_id]}
        service.files().create(body=metadata, media_body=media, fields="id").execute()
        logger.info("Uploaded %s to Google Drive folder %s", file_name, self.folder_id)

    def _load_credentials(self):
        if self._credentials:
            return self._credentials
        try:
            param_value = self._ssm.get_parameter(Name=self.parameter_name, WithDecryption=True)
        except ClientError:
            logger.exception("Failed to read Google Drive service account from %s", self.parameter_name)
            raise
        payload = json.loads(param_value["Parameter"]["Value"])
        scopes = ["https://www.googleapis.com/auth/drive.file"]
        self._credentials = service_account.Credentials.from_service_account_info(payload, scopes=scopes)
        return self._credentials
