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
    service_account_parameter: str
    folder_identifier: str

    def __post_init__(self) -> None:
        self._ssm = boto3.client("ssm")
        self._credentials = None
        self._folder_id: str | None = None

    def upload(self, file_name: str, data: bytes) -> None:
        credentials = self._load_credentials()
        folder_id = self._resolve_folder_id()
        service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype="video/mp4", resumable=False)
        metadata = {"name": file_name, "parents": [folder_id]}
        service.files().create(
            body=metadata,
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        ).execute()
        logger.info("Uploaded %s to Google Drive folder %s", file_name, folder_id)

    def _load_credentials(self):
        if self._credentials:
            return self._credentials
        try:
            param_value = self._ssm.get_parameter(Name=self.service_account_parameter, WithDecryption=True)
        except ClientError:
            logger.exception("Failed to read Google Drive service account from %s", self.service_account_parameter)
            raise
        payload = json.loads(param_value["Parameter"]["Value"])
        scopes = ["https://www.googleapis.com/auth/drive.file"]
        self._credentials = service_account.Credentials.from_service_account_info(payload, scopes=scopes)
        return self._credentials

    def _resolve_folder_id(self) -> str:
        if self._folder_id:
            return self._folder_id
        identifier = self.folder_identifier
        if identifier.startswith("/"):
            try:
                param_value = self._ssm.get_parameter(Name=identifier, WithDecryption=False)
            except ClientError:
                logger.exception("Failed to read Google Drive folder id from %s", identifier)
                raise
            folder_id = param_value["Parameter"]["Value"]
        else:
            folder_id = identifier
        self._folder_id = folder_id
        return folder_id
