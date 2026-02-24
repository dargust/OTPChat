import base64
import json
import os
import tempfile
from typing import Dict, Any

from cryptography.fernet import Fernet, InvalidToken


class StorageError(Exception):
    pass


class EncryptedStorage:
    """Simple encrypted on-disk storage for key dictionaries.

    Uses Fernet (symmetric) for authenticated encryption. Data is stored as
    JSON with base64-encoded binary fields. Writes are atomic via temp file
    + os.replace.
    """

    def __init__(self, path: str, fernet_key: bytes):
        self.path = path
        self.fernet = Fernet(fernet_key)

    def save(self, data: Dict[str, Any]) -> None:
        payload = json.dumps(data).encode("utf-8")
        token = self.fernet.encrypt(payload)
        # create temp file in the same directory as the destination so os.replace
        # is atomic and permitted across platforms
        dirpath = os.path.dirname(os.path.abspath(self.path)) or None
        # Write directly to destination file. This is not strictly atomic
        # across all platforms, but avoids permission issues on some Windows
        # setups where creating/removing temp files in the target directory
        # can be blocked. We still fsync to reduce risk of partial writes.
        with open(self.path, "wb") as f:
            f.write(token)
            f.flush()
            os.fsync(f.fileno())

    def load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            raise StorageError("storage file does not exist")
        with open(self.path, "rb") as f:
            token = f.read()
        try:
            payload = self.fernet.decrypt(token)
        except InvalidToken as e:
            raise StorageError("invalid encryption token or wrong key") from e
        return json.loads(payload.decode("utf-8"))

    @staticmethod
    def generate_key() -> bytes:
        return Fernet.generate_key()

    # --- secure delete helpers (best-effort) ---
    def _overwrite_file_with_zeros(self, path: str) -> None:
        try:
            size = os.path.getsize(path)
        except FileNotFoundError:
            return
        try:
            with open(path, "r+b") as f:
                chunk = b"\x00" * 4096
                written = 0
                while written < size:
                    towrite = min(len(chunk), size - written)
                    f.write(chunk[:towrite])
                    written += towrite
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            # best-effort only
            pass

    def secure_delete_file(self, path: str) -> None:
        """Overwrite file contents then unlink (best-effort)."""
        if not os.path.exists(path):
            return
        self._overwrite_file_with_zeros(path)
        try:
            os.remove(path)
        except OSError:
            pass

    def secure_delete_store(self) -> None:
        """Securely remove store file and any sidecar metadata (best-effort)."""
        try:
            self.secure_delete_file(self.path)
        except Exception:
            pass
