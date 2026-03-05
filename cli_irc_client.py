#!/usr/bin/env python3
"""Command-line IRC client wrapper for OTPChatClient.

Provides an interactive prompt with commands for joining channels,
sending messages, OTP operations, and simple config handling.
"""
import argparse
import threading
import queue
import os
import json
import sys
import time
from contextlib import nullcontext
try:
    from prompt_toolkit.shortcuts import prompt
    from prompt_toolkit.patch_stdout import patch_stdout
except Exception:
    prompt = input
    patch_stdout = nullcontext

from otpchat.client import OTPChatClient
from otpchat.storage import EncryptedStorage


system_channel = "SYSTEM"


class ChannelMessageManager:
    def __init__(self):
        self.channel_messages = {}

    def add_message(self, channel: str, message: str):
        if channel is None:
            channel = system_channel
        if channel not in self.channel_messages:
            self.channel_messages[channel] = []
        self.channel_messages[channel].append(message)
        # keep last 200 messages per channel
        if len(self.channel_messages[channel]) > 200:
            self.channel_messages[channel] = self.channel_messages[channel][-200:]
        return 1

    def remove_channel(self, channel: str):
        if channel in self.channel_messages:
            try:
                del self.channel_messages[channel]
                return 1
            except Exception:
                return 0
        return 0

    def get_recent_messages(self, channel: str, limit: int = 20):
        return self.channel_messages.get(channel, [])[-limit:]


class CLIClient:
    def __init__(self, args):
        self.args = args
        self.config_path = os.path.abspath(args.config or "otpchat_config.json")
        self.config = self._load_config()

        self.msg_q = queue.Queue()
        self.chan_mgr = ChannelMessageManager()

        # track the last command typed (used to suppress server echoes)
        self._last_sent_command = None
        self._last_sent_time = 0

        key_bytes = None
        if args.key_file:
            try:
                with open(os.path.abspath(args.key_file), "rb") as f:
                    key_bytes = f.read()
            except Exception:
                key_bytes = None

        self.client = OTPChatClient(self.msg_q, args.key_store, key_bytes)
        self.client.system_channel = system_channel
        nick = args.nick or self.config.get("nick") or f"user{os.urandom(3).hex()}"
        user = args.user or self.config.get("user") or nick
        self.client.set_user_data(user, user)
        self.client.set_nick(nick)

        self.running = True

    def _load_config(self):
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_config(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f)
        except Exception:
            pass

    def start(self):
        # connect in background so receive_message starts
        t = threading.Thread(target=self._connect_background, daemon=True)
        t.start()

        # start printer loop
        printer = threading.Thread(target=self._printer_loop, daemon=True)
        printer.start()

        # run input loop
        try:
            self._input_loop()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
        finally:
            self.running = False
            try:
                self.client.client_socket.close()
            except Exception:
                pass
            # wake printer thread
            try:
                self.msg_q.put(None)
            except Exception:
                pass

    def _connect_background(self):
        server = self.args.server or self.config.get("server", "127.0.0.1")
        port = int(self.args.port or self.config.get("port", 6667))
        try:
            self.client.connect(server, port)
        except Exception as e:
            print("Failed to connect:", e)

    def _printer_loop(self):
        while self.running:
            try:
                item = self.msg_q.get()
            except Exception:
                break
            if item is None:
                break
            # reuse GUI parsing rules: channel-prefixed messages begin with '#'
            try:
                if isinstance(item, bytes):
                    item = item.decode('utf-8', errors='replace')
            except Exception:
                pass
            s = str(item)
            # channel messages often contain leading channel text
            if s.startswith("#"):
                try:
                    raw_chan = s.split()[0]
                    channel = raw_chan.lstrip('#')
                    message = s[len(raw_chan) + 1 :]
                    # suppress echoed user commands (server sometimes echoes the raw command)
                    try:
                        nick = getattr(self.client, 'nickname', None) or getattr(self.client, 'nick', None)
                    except Exception:
                        nick = None
                    if self._last_sent_command:
                        # check for either '/cmd ...' or 'cmd ...' in the echoed message
                        cmd_plain = self._last_sent_command
                        cmd_slash = f"/{cmd_plain}"
                        if (nick and (f"<{nick}>" in message or nick in message)) and (
                            cmd_plain in message or cmd_slash in message
                        ) and (time.time() - self._last_sent_time) < 3:
                            # skip this echoed message
                            continue
                    # handle join/part notifications
                    parts = message.split()
                    if len(parts) >= 2 and parts[-1] in ("joined", "left"):
                        user = parts[0]
                        action = parts[-1]
                        if action == "left" and getattr(self.client, 'nickname', None) == user:
                            removed = self.chan_mgr.remove_channel(channel)
                            if removed:
                                self.chan_mgr.add_message(system_channel, f"#{channel} {message}")
                                # print system notification
                                print(f"[SYSTEM] #{channel} {message}")
                                continue
                    # normal channel message
                    self.chan_mgr.add_message(channel, message)
                    self.chan_mgr.add_message(system_channel, f"#{channel} {message}")
                    print(f"#{channel} {message}")
                except Exception:
                    print(s)
            else:
                # regular system or server messages
                self.chan_mgr.add_message(system_channel, s)
                print(f"[SYSTEM] {s}")

    def _input_loop(self):
        self._print_help(short=True)
        while self.running:
            try:
                with patch_stdout():
                    line = prompt("> ")
            except EOFError:
                break
            # clear the echoed input line from the terminal so the console doesn't show it
            try:
                self._clear_last_input_line()
            except Exception:
                pass
            if not line:
                continue
            if line.startswith("/"):
                # record last command (without leading slash) so we can filter server echoes
                try:
                    self._last_sent_command = line[1:].strip()
                    self._last_sent_time = time.time()
                except Exception:
                    self._last_sent_command = None
                    self._last_sent_time = 0
                self._handle_command(line[1:].strip())
            else:
                # send as message to active channel or treat as raw command if starts with '/'
                if getattr(self.client, 'active_channel', None) is None:
                    print("No active channel. Use /join <channel> or /switch <channel> to select one.")
                else:
                    self.client.send_message(line)

    def _clear_last_input_line(self):
        """Attempt to remove the last printed input line from the terminal.

        Uses ANSI escape sequences to move the cursor up one line and clear it.
        Works in modern terminals (including Windows 10+). Failure is silently ignored.
        """
        try:
            # move cursor up one line and clear the entire line
            sys.stdout.write('\x1b[1A')
            sys.stdout.write('\x1b[2K')
            sys.stdout.flush()
        except Exception:
            pass

    def _handle_command(self, cmdline: str):
        parts = cmdline.split()
        if not parts:
            return
        cmd = parts[0].lower()
        args = parts[1:]
        if cmd in ("quit", "exit"):
            self.running = False
            print("Quitting...")
            return
        if cmd == "help":
            self._print_help()
            return
        if cmd in ("join", "switch"):
            if not args:
                print("Usage: /join <channel>")
                return
            chan = args[0]
            if not chan.startswith("#"):
                chan = "#" + chan
            # send join command to server
            self.client.send_message(f"/join {chan}")
            # optimistic local active channel set
            self.client.active_channel = chan
            print(f"Joined {chan}")
            return
        if cmd in ("part", "leave"):
            chan = args[0] if args else getattr(self.client, 'active_channel', None)
            if chan and chan.startswith("#"):
                self.client.send_message(f"/part {chan}")
                if getattr(self.client, 'active_channel', None) == chan:
                    self.client.active_channel = None
                print(f"Left {chan}")
            else:
                print("No channel specified or active channel set")
            return
        if cmd in ("channels", "list"):
            # Request server channel list (IRC LIST) and also show local-known channels.
            try:
                self.client.send_message("/list")
                print("Requested server channel list; responses will appear shortly.")
            except Exception as e:
                print("Failed to request server channel list:", e)

            # Show local channels we have seen messages for
            chans = sorted(self.chan_mgr.channel_messages.keys())
            shown = False
            for c in chans:
                if c == system_channel:
                    continue
                msgs = len(self.chan_mgr.get_recent_messages(c, limit=100))
                print(f"#{c} ({msgs} messages)")
                shown = True
            if not shown:
                print("No local channels known (server list requested)")
            return
        if cmd == "show":
            # show recent messages for channel or system
            target = args[0] if args else system_channel
            target = target.lstrip('#')
            msgs = self.chan_mgr.get_recent_messages(target, limit=50)
            for m in msgs:
                print(m)
            return
        if cmd == "nick":
            if not args:
                print(f"Current nick: {getattr(self.client, 'nickname', '')}")
                return
            new = args[0]
            try:
                self.client.set_nick(new)
                try:
                    self.client.client_socket.send(f"NICK {new}\r\n".encode('utf-8'))
                except Exception:
                    pass
                print(f"Nick set to {new}")
            except Exception as e:
                print("Failed to set nick:", e)
            return
        if cmd == "otp-generate":
            # generate a key file path provided or prompt
            kf = args[0] if args else None
            if not kf:
                kf = input("Key file path to save: ").strip()
                if not kf:
                    print("Cancelled")
                    return
            try:
                key = EncryptedStorage.generate_key()
                with open(os.path.abspath(kf), "wb") as f:
                    f.write(key)
                print(f"Generated key file: {kf}")
            except Exception as e:
                print("Failed to generate key:", e)
            return
        if cmd == "otp-create-store":
            if len(args) < 1:
                print("Usage: /otp-create-store <store-path> [key-file]")
                return
            ks = args[0]
            kf = args[1] if len(args) > 1 else None
            key_bytes = None
            if kf:
                try:
                    with open(os.path.abspath(kf), "rb") as f:
                        key_bytes = f.read()
                except Exception:
                    key_bytes = None
            if not key_bytes:
                key_bytes = EncryptedStorage.generate_key()
                if not kf:
                    kf = input("Save generated key file to (optional): ").strip() or None
                    if kf:
                        try:
                            with open(os.path.abspath(kf), "wb") as f:
                                f.write(key_bytes)
                        except Exception:
                            pass
            try:
                storage = EncryptedStorage(os.path.abspath(ks), key_bytes)
                storage.save({})
                print(f"Created encrypted store {ks}")
            except Exception as e:
                print("Failed to create store:", e)
            return
        if cmd == "otp-load":
            if len(args) < 2:
                print("Usage: /otp-load <store-path> <key-file>")
                return
            ks = args[0]
            kf = args[1]
            key_bytes = None
            try:
                with open(os.path.abspath(kf), "rb") as f:
                    key_bytes = f.read()
            except Exception as e:
                print("Failed to read key file:", e)
                return
            try:
                self.client.load_new_keys(os.path.abspath(ks), key_bytes)
                print(f"Loaded OTP store {ks}")
            except Exception as e:
                print("Failed to load store:", e)
            return
        if cmd == "encrypt":
            if not args:
                print(f"Encryption is {'on' if getattr(self.client, 'encrypt_messages', False) else 'off'}")
                return
            val = args[0].lower()
            on = val in ("1", "on", "true", "yes")
            try:
                self.client.toggle_encrypt(on)
                print(f"Encryption set to {on}")
            except Exception as e:
                print("Failed to toggle encryption:", e)
            return
        if cmd == "msg":
            # /msg <target> <message>
            if len(args) < 2:
                print("Usage: /msg <target> <message>")
                return
            target = args[0]
            msg = " ".join(args[1:])
            self.client.send_message(f"/msg {target} {msg}")
            return

        print(f"Unknown command: {cmd}")

    def _print_help(self, short: bool = False):
        if short:
            print("Type /help for commands. Type text to send to active channel.")
            return
        print("Commands:")
        print("  /help                 Show this help")
        print("  /join <chan>          Join and select a channel")
        print("  /part [chan]          Leave channel")
        print("  /channels             List known channels")
        print("  /show [chan]          Show recent messages for channel")
        print("  /nick <nick>          Change nick")
        print("  /msg <target> <msg>   Send a private message (encrypted if enabled)")
        print("  /encrypt on|off       Toggle OTP encryption for outgoing messages")
        print("  /otp-generate [path]  Generate an OTP key file")
        print("  /otp-create-store <store> [keyfile]  Create a new encrypted store")
        print("  /otp-load <store> <keyfile>  Load an existing OTP store")
        print("  /quit                 Exit")


def main():
    p = argparse.ArgumentParser(description="OTP IRC CLI client")
    p.add_argument("--server", help="IRC server address")
    p.add_argument("--port", help="IRC server port", type=int)
    p.add_argument("--nick", help="Nickname to use")
    p.add_argument("--user", help="Username/realname")
    p.add_argument("--key-store", help="Path to OTP key store file")
    p.add_argument("--key-file", help="Path to OTP key file (Fernet key)")
    p.add_argument("--config", help="Path to config file (default otpchat_config.json)")
    args = p.parse_args()

    cli = CLIClient(args)
    cli.start()


if __name__ == "__main__":
    main()
