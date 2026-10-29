import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from backend.app.core.config import get_settings
from services.storage import LocalStorage, S3Storage, create_storage


class LocalStorageTests(unittest.TestCase):
    def test_save_and_read_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalStorage(directory)
            key = "resumes/test.txt"
            content = b"Resume content"

            self.assertEqual(storage.save(key, content), key)
            self.assertEqual(storage.read(key), content)

    def test_rejects_keys_outside_storage_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalStorage(directory)

            with self.assertRaises(ValueError):
                storage.save("../outside.txt", b"content")

    def test_factory_defaults_to_local_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"STORAGE_BACKEND": "local", "UPLOAD_DIR": directory}):
                get_settings.cache_clear()
                try:
                    self.assertIsInstance(create_storage(), LocalStorage)
                finally:
                    get_settings.cache_clear()

    def test_factory_rejects_unknown_backend(self) -> None:
        with patch.dict(os.environ, {"STORAGE_BACKEND": "unknown"}):
            get_settings.cache_clear()
            try:
                with self.assertRaises(ValueError):
                    create_storage()
            finally:
                get_settings.cache_clear()

    def test_factory_configures_s3_compatible_endpoint(self) -> None:
        environment = {
            "STORAGE_BACKEND": "s3",
            "S3_BUCKET": "test-bucket",
            "S3_ENDPOINT_URL": "http://localhost:9000",
            "S3_ACCESS_KEY_ID": "local-user",
            "S3_SECRET_ACCESS_KEY": "local-password",
        }
        with patch.dict(os.environ, environment), patch("boto3.client") as client:
            get_settings.cache_clear()
            try:
                storage = create_storage()
            finally:
                get_settings.cache_clear()

        self.assertIsInstance(storage, S3Storage)
        self.assertEqual(client.call_args.kwargs["endpoint_url"], "http://localhost:9000")
        self.assertEqual(client.call_args.kwargs["aws_access_key_id"], "local-user")
        self.assertEqual(client.call_args.kwargs["aws_secret_access_key"], "local-password")

    def test_s3_storage_uses_shared_save_read_delete_interface(self) -> None:
        with patch("boto3.client") as client_factory:
            storage = S3Storage("test-bucket")
            client = client_factory.return_value
            client.get_object.return_value = {"Body": BytesIO(b"resume")}

            self.assertEqual(storage.save("resumes/1.txt", b"resume"), "resumes/1.txt")
            self.assertEqual(storage.read("resumes/1.txt"), b"resume")
            storage.delete("resumes/1.txt")

        client.put_object.assert_called_once_with(
            Bucket="test-bucket", Key="resumes/1.txt", Body=b"resume"
        )
        client.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="resumes/1.txt"
        )
        client.delete_object.assert_called_once_with(
            Bucket="test-bucket", Key="resumes/1.txt"
        )


if __name__ == "__main__":
    unittest.main()
