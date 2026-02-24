"""Minimal IRC parsing helpers extracted from original project."""
from typing import Tuple, List


def parse_msg(line: str) -> Tuple[str, str, List[str]]:
    line = line.strip('\r\n')
    prefix = ""
    trailing = []
    if not line:
        return prefix, "", []
    if line.startswith(":"):
        prefix, line = line[1:].split(" ", 1)
    if " :" in line:
        line, trailing = line.split(" :", 1)
        args = line.split()
        args.append(trailing)
    else:
        args = line.split()
    command = args.pop(0) if args else ""
    return prefix, command, args
