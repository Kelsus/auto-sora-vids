from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass
from typing import Optional

import boto3
from botocore.exceptions import ClientError
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError


logger = logging.getLogger(__name__)


@dataclass
class DriveUploader:
    service_account_parameter: str
    folder_identifier: str

    def __post_init__(self) -> None:
        self._ssm = boto3.client("ssm")
        self._credentials = None
        self._folder_id: str | None = None
        self._drive_id: str | None = None

    def upload(self, file_name: str, data: bytes, folder_name: str | None = None) -> None:
        credentials = self._load_credentials()
        service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        base_folder_id = self._resolve_base_folder(service)
        target_folder_id = base_folder_id
        if folder_name:
            child_id = self._ensure_child_folder(service, base_folder_id, folder_name)
            if child_id:
                target_folder_id = child_id
            else:
                logger.warning(
                    "Unable to prepare Drive subfolder '%s' under folder %s; uploading to base folder instead",
                    folder_name,
                    base_folder_id,
                )
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype="video/mp4", resumable=False)
        metadata = {"name": file_name, "parents": [target_folder_id]}
        service.files().create(
            body=metadata,
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        ).execute()
        logger.info("Uploaded %s to Google Drive folder %s", file_name, target_folder_id)

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

    def _find_child_folder_id(self, service, parent_id: str, folder_name: str) -> Optional[str]:
        escaped_name = folder_name.replace("'", "\\'")
        query = (
            f"'{parent_id}' in parents and name = '{escaped_name}' "
            "and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        )
        params = dict(
            q=query,
            fields="files(id,name)",
            pageSize=10,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora="allDrives",
            spaces="drive",
        )
        if self._drive_id:
            params["driveId"] = self._drive_id
            params["corpora"] = "drive"
        response = service.files().list(**params).execute()
        files = response.get("files", [])
        if not files:
            return None
        if len(files) > 1:
            logger.warning(
                "Multiple Drive folders named '%s' under parent %s; using the first match",
                folder_name,
                parent_id,
            )
        return files[0]["id"]

    def _ensure_child_folder(self, service, parent_id: str, folder_name: str) -> Optional[str]:
        existing_id = self._find_child_folder_id(service, parent_id, folder_name)
        if existing_id:
            return existing_id
        logger.info("Drive subfolder '%s' not found under %s; creating it", folder_name, parent_id)
        request_kwargs = dict(
            body={
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            },
            fields="id",
            supportsAllDrives=True,
        )
        try:
            new_folder = service.files().create(**request_kwargs).execute()
            return new_folder.get("id")
        except HttpError as err:
            if err.resp.status == 409:
                logger.info("Drive reported conflict creating '%s'; retrying lookup", folder_name)
                return self._find_child_folder_id(service, parent_id, folder_name)
            logger.exception("Failed to create Drive subfolder '%s' under %s", folder_name, parent_id)
            return None

    def _resolve_base_folder(self, service) -> str:
        folder_id = self._ensure_folder_id()
        if self._drive_id is None:
            try:
                response = (
                    service.files()
                    .get(fileId=folder_id, fields="id, driveId", supportsAllDrives=True)
                    .execute()
                )
                self._drive_id = response.get("driveId")
            except HttpError:
                logger.exception("Failed to resolve drive context for folder %s", folder_id)
        return folder_id

    def _ensure_folder_id(self) -> str:
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
