import math
import secrets
from typing import Dict, Tuple, List

from .storage import EncryptedStorage, StorageError


class OTPError(Exception):
    pass


class OTPManager:
    """One-time-pad manager that loads/stores keys via EncryptedStorage.

    The storage format is a JSON object with metadata at key "meta" and
    keys in a list under "keys". Keys are base64-encoded strings representing
    the per-message pad.
    """

    def __init__(self, storage: EncryptedStorage):
        self.storage = storage
        self._load_or_init()
        self.secure_random = secrets.SystemRandom()

    def _load_or_init(self):
        try:
            data = self.storage.load()
            meta = data.get("meta", {})
            self.alphabet = meta.get("alphabet")
            self.msg_len = int(meta.get("msg_len"))
            self.max_keys = int(meta.get("max_keys"))
            self.keys: Dict[int, str] = {int(k): v for k, v in data.get("keys", {}).items()}
            print(f"Loaded OTP keys: {len(self.keys)} available")
        except StorageError as e:
            # initialize defaults
            self.alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!@#$%^&*()[]{}<>?,./;:'\"\\| `~"
            self.msg_len = 64
            self.max_keys = 0xFFFF
            self.keys = {}
            print(f"Failed to load OTP keys: {e}")

    def key_count(self) -> int:
        return len(self.keys)
    
    def has_keys(self) -> bool:
        if self.keys is None:
            return False
        return len(self.keys) > 0

    def generate(self, alphabet: str, msg_len: int, num_keys: int) -> None:
        self.alphabet = alphabet
        self.msg_len = int(msg_len)
        self.max_keys = int(num_keys)
        keys: Dict[int, str] = {}
        for i in range(self.max_keys):
            # generate a key string from alphabet
            keys[i] = "".join(self.secure_random.choice(self.alphabet) for _ in range(self.msg_len))
        payload = {"meta": {"alphabet": self.alphabet, "msg_len": self.msg_len, "max_keys": self.max_keys},
                   "keys": {str(k): v for k, v in keys.items()}}
        self.storage.save(payload)
        self.keys = {int(k): v for k, v in payload["keys"].items()}

    def _get_key_len_hex(self) -> int:
        # number of hex digits needed to represent indices up to max_keys
        return max(1, math.ceil(math.log(self.max_keys + 1, 16)))

    # --- helper to wipe bytearrays (best-effort) ---
    def _wipe_bytearray(self, b: bytearray) -> None:
        for i in range(len(b)):
            b[i] = 0

    def encode(self, message: str) -> Tuple[str, bool]:
        # pad message
        m = message.ljust(self.msg_len)
        if len(m) > self.msg_len:
            return (f"Unable to encode message: length > {self.msg_len}", False)
        if not self.keys:
            return ("Unable to encode message: no keys available", False)
        # choose random key index
        try:
            idx = self.secure_random.choice(list(self.keys.keys()))
        except IndexError:
            return ("Unable to encode message: no keys available", False)
        k = self.keys.pop(idx)
        # create a bytearray copy of the key for wiping after use
        try:
            k_bytes = bytearray(k.encode("utf-8"))
        except Exception:
            k_bytes = None

        # persist updated key store
        payload = {"meta": {"alphabet": self.alphabet, "msg_len": self.msg_len, "max_keys": self.max_keys},
                   "keys": {str(kid): v for kid, v in self.keys.items()}}
        self.storage.save(payload)
        # produce cipher text with hex prefix index
        keyprefix = self._get_key_len_hex()
        prefix = f"{idx:0{keyprefix}x}"
        try:
            cipher = ''.join(self.alphabet[(self.alphabet.index(m[i]) + self.alphabet.index(k[i])) % len(self.alphabet)] for i in range(self.msg_len))
        except ValueError:
            # message or key contains characters outside alphabet
            # restore popped key and persist store, then return failure
            self.keys[idx] = k
            payload = {"meta": {"alphabet": self.alphabet, "msg_len": self.msg_len, "max_keys": self.max_keys},
                       "keys": {str(kid): v for kid, v in self.keys.items()}}
            self.storage.save(payload)
            return ("Unable to encode message: character outside alphabet", False)
        # wipe in-memory copy of key (best-effort)
        if k_bytes is not None:
            self._wipe_bytearray(k_bytes)
            del k_bytes
        # remove reference
        del k
        return (prefix + cipher, True)

    def decode(self, data: str) -> Tuple[str, bool]:
        keyprefix = self._get_key_len_hex()
        try:
            idx = int(data[:keyprefix], 16)
            k = self.keys.pop(idx)
        except Exception:
            return ("Unable to decode data", False)

        # create a bytearray copy of the key for wiping after use
        try:
            k_bytes = bytearray(k.encode("utf-8"))
        except Exception:
            k_bytes = None

        payload = {"meta": {"alphabet": self.alphabet, "msg_len": self.msg_len, "max_keys": self.max_keys},
                   "keys": {str(kid): v for kid, v in self.keys.items()}}
        self.storage.save(payload)
        d = data[keyprefix:]
        try:
            m = ''.join(self.alphabet[(self.alphabet.index(d[i]) - self.alphabet.index(k[i])) % len(self.alphabet)] for i in range(self.msg_len))
        except ValueError:
            # invalid characters; restore key and persist
            self.keys[idx] = k
            payload = {"meta": {"alphabet": self.alphabet, "msg_len": self.msg_len, "max_keys": self.max_keys},
                       "keys": {str(kid): v for kid, v in self.keys.items()}}
            self.storage.save(payload)
            return ("Unable to decode data: character outside alphabet", False)
        # wipe in-memory copy of key (best-effort)
        if k_bytes is not None:
            self._wipe_bytearray(k_bytes)
            del k_bytes
        del k
        return (m.rstrip(), True)
