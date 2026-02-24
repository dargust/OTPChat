"""Small CLI tools for key generation and management.

This module supports being executed both as a package module (`python -m
otpchat.tools`) and directly as a script (`python otpchat/tools.py`). When
run directly the import path is adjusted so the package imports still work.
"""
import argparse
import os
import sys
from typing import Optional

try:
    # normal package-style import
    from .storage import EncryptedStorage
    from .crypto import OTPManager
except (ImportError, SystemError):
    # fallback when executed as a script (no package context)
    pkg_root = os.path.dirname(os.path.dirname(__file__))
    if pkg_root not in sys.path:
        sys.path.insert(0, pkg_root)
    from otpchat.storage import EncryptedStorage
    from otpchat.crypto import OTPManager


def generate_keys(path: str, outkeyfile: str, alphabet: str, msglen: int, numkeys: int) -> None:
    # generate a fernet key and write encrypted key store
    key = EncryptedStorage.generate_key()
    storage = EncryptedStorage(path, key)
    manager = OTPManager(storage)
    manager.generate(alphabet, msglen, numkeys)
    # persist the fernet key next to store for demo purposes (in real use, store key securely)
    with open(outkeyfile, "wb") as f:
        f.write(key)
    print(f"Generated {numkeys} keys (msglen={msglen}) and wrote store to {path}")


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(prog="otp-tools")
    sub = parser.add_subparsers(dest="cmd")
    g = sub.add_parser("generate")
    g.add_argument("--store", default="key.store", help="Path to encrypted key store file")
    g.add_argument("--keyout", default="store.key", help="Path to write Fernet key (protect this!)")
    g.add_argument("--alphabet", default=None, help="Alphabet to use for keys")
    g.add_argument("--msglen", type=int, default=64)
    g.add_argument("--numkeys", type=int, default=256)

    args = parser.parse_args(argv)
    if args.cmd == "generate":
        alphabet = args.alphabet or "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!@#$%^&*()[]{}<>?,./;:'\"\\| `~"
        generate_keys(args.store, args.keyout, alphabet, args.msglen, args.numkeys)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
