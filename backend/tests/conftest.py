from __future__ import annotations

import sys
import types
from pathlib import Path


class _StubBoto3(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("boto3")

    def resource(self, *args, **kwargs):  # pragma: no cover - defensive
        raise RuntimeError("boto3.resource should be mocked in tests")

    def client(self, *args, **kwargs):  # pragma: no cover - defensive
        raise RuntimeError("boto3.client should be mocked in tests")


def pytest_configure(config) -> None:  # pragma: no cover - pytest hook
    root = Path(__file__).resolve().parents[2]
    src_path = root / "src"
    if src_path.exists() and str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    shared_path = root / "infra" / "lambda_src" / "common_layer" / "python"
    if str(shared_path) not in sys.path:
        sys.path.insert(0, str(shared_path))
    if "boto3" not in sys.modules:
        boto3_stub = _StubBoto3()
        sys.modules["boto3"] = boto3_stub
        dynamodb_module = types.ModuleType("boto3.dynamodb")

        class _Key:
            def __init__(self, _name):
                pass

            def eq(self, _value):
                return self

            def lte(self, _value):
                return self

            def __and__(self, other):
                return self

        conditions_module = types.ModuleType("boto3.dynamodb.conditions")
        conditions_module.Key = _Key
        dynamodb_module.conditions = conditions_module
        sys.modules["boto3.dynamodb"] = dynamodb_module
        sys.modules["boto3.dynamodb.conditions"] = conditions_module

    if "botocore" not in sys.modules:
        botocore_module = types.ModuleType("botocore")
        exceptions_module = types.ModuleType("botocore.exceptions")

        class _ClientError(Exception):
            pass

        class _ConditionalCheckFailedException(Exception):
            pass

        exceptions_module.ClientError = _ClientError
        exceptions_module.ConditionalCheckFailedException = _ConditionalCheckFailedException
        botocore_module.exceptions = exceptions_module
        sys.modules["botocore"] = botocore_module
        sys.modules["botocore.exceptions"] = exceptions_module

    if "googleapiclient" not in sys.modules:
        google_module = types.ModuleType("googleapiclient")
        discovery_module = types.ModuleType("googleapiclient.discovery")
        http_module = types.ModuleType("googleapiclient.http")

        def _build(*args, **kwargs):  # pragma: no cover - defensive
            raise RuntimeError("googleapiclient.discovery.build should be mocked in tests")

        discovery_module.build = _build
        class _MediaUpload:
            def __init__(self, *args, **kwargs):
                pass

        http_module.MediaIoBaseUpload = _MediaUpload
        google_module.discovery = discovery_module
        google_module.http = http_module
        sys.modules["googleapiclient"] = google_module
        sys.modules["googleapiclient.discovery"] = discovery_module
        sys.modules["googleapiclient.http"] = http_module
