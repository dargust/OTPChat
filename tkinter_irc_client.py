from otpchat.client import OTPChatClient
from otpchat.storage import EncryptedStorage
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog
import threading
import queue
import os
import json
import secrets

system_channel = "SYSTEM"

class ChannelMessageManager:
    def __init__(self):
        # store recent messages per channel for potential future use (e.g. resend, history)
        self.channel_messages = {}
    
    def add_message(self, channel: str, message: str):
        # if the message is an error for a channel, remove the channel prefix for
        # cleaner display in system_channel and add a note about the channel in the message
        if channel is system_channel:
            if message.startswith("#") and "⌄ ## ERROR ## ⌄" in message:
                # remove the #channel prefix from the message for cleaner display
                channel_str, message = message.split(" ", 1)
                message_bulk = message.split("^ ## ERROR ## ^", 1)[0].strip()
                message = f"{message_bulk}\r\nin channel {channel_str}\r\n^ ## ERROR ## ^"
                print(f"Processed error message: {message}")
        updated_channels = 0
        if channel not in self.channel_messages:
            self.channel_messages[channel] = []
            updated_channels = 1
        self.channel_messages[channel].append(message)
        # keep only the last 100 messages per channel to limit memory usage
        if len(self.channel_messages[channel]) > 100:
            self.channel_messages[channel] = self.channel_messages[channel][-100:]
        return updated_channels

    def remove_channel(self, channel: str):
        """Remove a channel and its messages. Returns 1 if removed, 0 otherwise."""
        if channel in self.channel_messages:
            try:
                del self.channel_messages[channel]
            except Exception:
                return 0
            return 1
        return 0
    
    def get_recent_messages(self, channel: str, limit: int = 10):
        return self.channel_messages.get(channel, [])[-limit:]

class IRCClientGUI:
    def __init__(self, master):
        self.master = master
        master.title("OTP Chat Client")

        # Channel selector will control which channel's messages are shown
        self.channel_var = tk.StringVar(value=system_channel)
        ctrl_frame = tk.Frame(master)
        ctrl_frame.pack(fill=tk.X, padx=10, pady=(6, 0))
        tk.Label(ctrl_frame, text="Channel:").pack(side=tk.LEFT)
        self.channel_combo = ttk.Combobox(ctrl_frame, textvariable=self.channel_var, state="readonly", width=30)
        self.channel_combo.pack(side=tk.LEFT, padx=(6, 8))
        self.channel_combo.bind("<<ComboboxSelected>>", lambda e: self._on_channel_select())

        self.chat_display = tk.Text(master, state='disabled', width=100, height=25)
        self.chat_display.pack(padx=10, pady=10, expand=True, fill=tk.BOTH)

        self.encrypt_messages = tk.BooleanVar(value=False)
        # OTP options button (left of encrypt toggle)
        self.otp_options_button = tk.Button(master, text="OTP Options", command=self._open_otp_options)
        self.otp_options_button.pack(side=tk.LEFT, padx=(10, 0), pady=(0, 10))

        self.encrypt_messages_checkbox = tk.Checkbutton(master, text="Encrypt messages", variable=self.encrypt_messages, command=self.toggle_encrypt)
        self.encrypt_messages_checkbox.pack(side=tk.LEFT, padx=(6, 0), pady=(0, 10))
        self.message_entry = tk.Entry(master, width=40)
        self.message_entry.pack(side=tk.LEFT, padx=(10, 0), pady=(0, 10))
        self.message_entry.bind("<Return>", self.send_message)
        self.message_entry.focus_set()

        self.send_button = tk.Button(master, text="Send", command=self.send_message)
        self.send_button.pack(side=tk.LEFT, padx=(5, 10), pady=(0, 10))

        # Queue for incoming messages
        self.message_queue = queue.Queue()

        # load small config for last-used values
        self.config_path = os.path.abspath("otpchat_config.json")
        self.config = self._load_config()

        # otp-related saved values (used by OTP options panel)
        self.otp_key_store_var = tk.StringVar(value=self.config.get("key_store", ""))
        self.otp_key_file_var = tk.StringVar(value=self.config.get("key_file", ""))

        self.channel_manager = ChannelMessageManager()

        # populate combo with 'system_channel' initially
        self._update_channel_list()

        # show login panel before starting client
        self._build_login_panel()

        # Periodically check for new messages
        self.master.after(100, self.check_messages)

    def toggle_encrypt(self):
        self.client.toggle_encrypt(self.encrypt_messages.get())

    def set_user_data(self, username: str, display_name: str):
        self.username = username
        self.display_name = display_name

    def set_nick(self, nick: str):
        self.nick = nick

    def start_client(self):
        # Initialize the OTP chat client
        # use values set by login panel
        ks_path = getattr(self, "login_key_store", None)
        kf_path = getattr(self, "login_key_file", None)
        key_bytes = None
        if kf_path:
            try:
                with open(os.path.abspath(kf_path), "rb") as kf:
                    key_bytes = kf.read()
            except Exception:
                key_bytes = None

        self.client = OTPChatClient(self.message_queue, ks_path, key_bytes)
        self.client.system_channel = system_channel
        self.client.set_user_data(self.username, self.display_name)
        self.client.set_nick(self.nick)
        server = getattr(self, "login_server", "127.0.0.1")
        port = getattr(self, "login_port", 6667)
        try:
            self.client.connect(server, port)
        except Exception as e:
            print("Failed to connect to server:", e)

    def send_message(self, event=None):
        message = self.message_entry.get()
        if message:
            self.client.send_message(message)
            self.message_entry.delete(0, tk.END)

    def _update_channel_list(self):
        vals = [system_channel] + sorted(self.channel_manager.channel_messages.keys())
        # avoid duplicates
        vals = list(dict.fromkeys(vals))
        try:
            self.channel_combo['values'] = vals
        except Exception:
            pass
        # keep selection if valid
        if self.channel_var.get() not in vals:
            self.channel_var.set(vals[0] if vals else system_channel)

    def select_channel(self, channel: str):
        # normalize channel name (strip leading '#') before matching
        if channel is None:
            return
        norm = str(channel).lstrip('#')
        print(f"Selecting channel: raw={channel} norm={norm}")
        print(f"Available channels: {self.channel_combo['values']}")
        if norm in self.channel_combo['values']:
            self.channel_var.set(norm)
            self._on_channel_select()
            print(f"Selected channel: {norm}")

    def _on_channel_select(self):
        sel = self.channel_var.get() or system_channel
        # refresh display with recent messages for selected channel
        msgs = self.channel_manager.get_recent_messages(sel, limit=100)
        # clear display and show messages
        self.chat_display.config(state='normal')
        self.chat_display.delete('1.0', tk.END)
        for m in msgs:
            self.chat_display.insert(tk.END, m + "\n")
        self.chat_display.config(state='disabled')
        self.chat_display.see(tk.END)
        if sel != system_channel:
            self.client.active_channel = "#"+sel
        else:
            self.client.active_channel = None

    # --- config helpers + login panel ---
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

    def _build_login_panel(self):
        # create an in-window overlay that covers the main window
        # and a centered dialog frame inside it. Use a Canvas rectangle
        # with a stipple pattern to simulate semi-transparency.
        overlay = tk.Frame(self.master)
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        # dimmer canvas fills overlay
        dimmer = tk.Canvas(overlay, highlightthickness=0)
        dimmer.pack(fill="both", expand=True)

        def _resize_dimmer(event):
            dimmer.delete("dimmer")
            dimmer.create_rectangle(0, 0, event.width, event.height,
                                     fill="black", stipple="gray25", tags="dimmer")

        dimmer.bind("<Configure>", _resize_dimmer)

        # dialog frame centered in overlay (place on overlay so it sits above the canvas)
        dialog = tk.Frame(overlay, bd=2, relief=tk.RIDGE)
        dialog.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        dialog.lift()
        lf = dialog
        self.login_frame = overlay

        # variables with defaults
        server_default = self.config.get("server", "127.0.0.1")
        port_default = str(self.config.get("port", 6667))
        nick_default = self.config.get("nick", f"user{secrets.token_hex(3)}")
        user_default = self.config.get("user", "")
        key_store_default = self.config.get("key_store", "")
        key_file_default = self.config.get("key_file", "")

        self.server_var = tk.StringVar(value=server_default)
        self.port_var = tk.StringVar(value=port_default)
        self.nick_var = tk.StringVar(value=nick_default)
        self.user_var = tk.StringVar(value=user_default)
        self.key_store_var = tk.StringVar(value=key_store_default)
        self.key_file_var = tk.StringVar(value=key_file_default)

        # layout fields
        row = 0
        def add_row(label_text, var):
            nonlocal row
            lbl = tk.Label(lf, text=label_text)
            lbl.grid(row=row, column=0, sticky=tk.W, padx=4, pady=2)
            ent = tk.Entry(lf, textvariable=var, width=40)
            ent.grid(row=row, column=1, sticky=tk.W, padx=4, pady=2)
            row += 1

        add_row("Server:", self.server_var)
        add_row("Port:", self.port_var)
        add_row("Nickname:", self.nick_var)
        add_row("User:", self.user_var)
        add_row("OTP key store file:", self.key_store_var)
        # add file-picker for key store
        ks_btn = tk.Button(lf, text="Browse...", command=lambda: self._pick_file(self.key_store_var))
        ks_btn.grid(row=row-1, column=2, padx=4)

        add_row("OTP store key file:", self.key_file_var)
        # add file-picker for key file
        kf_btn = tk.Button(lf, text="Browse...", command=lambda: self._pick_file(self.key_file_var))
        kf_btn.grid(row=row-1, column=2, padx=4)

        # login button
        self.login_button = tk.Button(lf, text="Login", state=tk.DISABLED, command=self._on_login)
        self.login_button.grid(row=row, column=0, columnspan=2, pady=(6, 2))

        # validate inputs: enable button when all fields non-empty
        def validate(*args):
            vals = [self.server_var.get().strip(), self.port_var.get().strip(), self.nick_var.get().strip(),
                    self.user_var.get().strip(), self.key_store_var.get().strip(), self.key_file_var.get().strip()]
            ok = all(vals)
            self.login_button.config(state=(tk.NORMAL if ok else tk.DISABLED))

        for v in (self.server_var, self.port_var, self.nick_var, self.user_var, self.key_store_var, self.key_file_var):
            v.trace_add("write", validate)

        # initial validate
        validate()
        # keep the dialog focused
        try:
            lf.focus_set()
        except Exception:
            pass

    def _pick_file(self, var: tk.StringVar):
        path = filedialog.askopenfilename(title="Select file", initialdir=os.path.abspath("."))
        if path:
            var.set(path)

    def _otp_pick_file(self, var: tk.StringVar, save: bool = False):
        if save:
            path = filedialog.asksaveasfilename(title="Select file to save", initialdir=os.path.abspath("."))
        else:
            path = filedialog.askopenfilename(title="Select file", initialdir=os.path.abspath("."))
        if path:
            var.set(path)

    def _open_otp_options(self):
        overlay = tk.Frame(self.master)
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        dimmer = tk.Canvas(overlay, highlightthickness=0)
        dimmer.pack(fill="both", expand=True)

        def _resize_dimmer(event):
            dimmer.delete("dimmer")
            dimmer.create_rectangle(0, 0, event.width, event.height,
                                     fill="black", stipple="gray25", tags="dimmer")

        dimmer.bind("<Configure>", _resize_dimmer)

        dialog = tk.Frame(overlay, bd=2, relief=tk.RIDGE)
        dialog.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        dialog.lift()
        lf = dialog
        self.otp_frame = overlay

        # local vars (use existing config defaults)
        ks_default = self.config.get("key_store", "")
        kf_default = self.config.get("key_file", "")

        ks_var = tk.StringVar(value=ks_default)
        kf_var = tk.StringVar(value=kf_default)

        row = 0
        def add_row(label_text, var):
            nonlocal row
            lbl = tk.Label(lf, text=label_text)
            lbl.grid(row=row, column=0, sticky=tk.W, padx=4, pady=2)
            ent = tk.Entry(lf, textvariable=var, width=40)
            ent.grid(row=row, column=1, sticky=tk.W, padx=4, pady=2)
            row += 1

        add_row("OTP key store file:", ks_var)
        ks_b = tk.Button(lf, text="Browse...", command=lambda: self._otp_pick_file(ks_var))
        ks_b.grid(row=row-1, column=2, padx=4)

        add_row("OTP store key file:", kf_var)
        kf_b = tk.Button(lf, text="Browse...", command=lambda: self._otp_pick_file(kf_var))
        kf_b.grid(row=row-1, column=2, padx=4)

        def _on_apply():
            ks = ks_var.get().strip() or None
            kf = kf_var.get().strip() or None
            # persist
            if ks:
                self.config["key_store"] = ks
            if kf:
                self.config["key_file"] = kf
            self._save_config()
            # if client available, load
            key_bytes = None
            if kf:
                try:
                    with open(os.path.abspath(kf), "rb") as f:
                        key_bytes = f.read()
                except Exception:
                    key_bytes = None
            if hasattr(self, 'client') and self.client:
                try:
                    self.client.load_new_keys(ks, key_bytes)
                except Exception:
                    pass
            # set login attributes for future connections
            self.login_key_store = ks
            self.login_key_file = kf
            try:
                self.otp_frame.destroy()
            except Exception:
                pass
        
        ks_g = tk.Button(lf, text="Generate new store", command=lambda: self._generate_store(ks_var, kf_var))
        ks_g.grid(row=row, column=0, columnspan=1, pady=(6,2))

        apply_btn = tk.Button(lf, text="Apply", command=_on_apply)
        apply_btn.grid(row=row, column=0, columnspan=3, pady=(6,2))

        cancel_btn = tk.Button(lf, text="Cancel", command=lambda: (self.otp_frame.destroy()))
        cancel_btn.grid(row=row, column=2, columnspan=2, pady=(6,2), sticky=tk.E)

    def _generate_key_file(self, kf_var: tk.StringVar):
        path = kf_var.get().strip()
        if not path:
            path = filedialog.asksaveasfilename(title="Save OTP key file", initialdir=os.path.abspath("."))
            if not path:
                return
            kf_var.set(path)
        try:
            key = EncryptedStorage.generate_key()
            with open(os.path.abspath(path), "wb") as f:
                f.write(key)
            # notify
            self.channel_manager.add_message(system_channel, f"OTP: generated key file {os.path.basename(path)}")
            if self.channel_var.get() == system_channel:
                self.display_message(f"OTP: generated key file {os.path.basename(path)}")
        except Exception as e:
            self.channel_manager.add_message(system_channel, f"OTP: failed to generate key: {e}")

    def _generate_store(self, ks_var: tk.StringVar, kf_var: tk.StringVar):
        ks_path = ks_var.get().strip()
        kf_path = kf_var.get().strip()
        if not ks_path:
            ks_path = filedialog.asksaveasfilename(title="Save OTP store file", initialdir=os.path.abspath("."))
            if not ks_path:
                return
            ks_var.set(ks_path)
        # ensure we have a key
        key_bytes = None
        if kf_path:
            try:
                with open(os.path.abspath(kf_path), "rb") as f:
                    key_bytes = f.read()
            except Exception:
                key_bytes = None
        if not key_bytes:
            # generate a new key and save to key file if desired
            key_bytes = EncryptedStorage.generate_key()
            if not kf_path:
                # ask user where to save key
                kf_path = filedialog.asksaveasfilename(title="Save generated key file", initialdir=os.path.abspath("."))
                if kf_path:
                    kf_var.set(kf_path)
            if kf_path:
                try:
                    with open(os.path.abspath(kf_path), "wb") as f:
                        f.write(key_bytes)
                except Exception:
                    pass
        # create encrypted storage with empty dataset
        try:
            storage = EncryptedStorage(os.path.abspath(ks_path), key_bytes)
            storage.save({})
            self.channel_manager.add_message(system_channel, f"OTP: created store {os.path.basename(ks_path)}")
            if self.channel_var.get() == system_channel:
                self.display_message(f"OTP: created store {os.path.basename(ks_path)}")
        except Exception as e:
            self.channel_manager.add_message(system_channel, f"OTP: failed to create store: {e}")

    def _on_login(self):
        # store selections and persist last-used user/key store
        server = self.server_var.get().strip()
        port = int(self.port_var.get().strip())
        nick = self.nick_var.get().strip()
        user = self.user_var.get().strip()
        key_store = self.key_store_var.get().strip() or None
        key_file = self.key_file_var.get().strip() or None

        # persist last-used values
        if user:
            self.config["user"] = user
        if key_store:
            self.config["key_store"] = key_store
        if key_file:
            self.config["key_file"] = key_file
        self.config["server"] = server
        self.config["port"] = port
        self.config["nick"] = nick
        self._save_config()

        # set attributes for start_client
        self.login_server = server
        self.login_port = port
        self.login_nick = nick
        self.login_user = user
        self.login_key_store = key_store
        self.login_key_file = key_file

        # hide login panel
        try:
            self.login_frame.destroy()
        except Exception:
            pass

        # apply user data and start client thread
        self.set_user_data(user or nick, user or nick)
        self.set_nick(nick)
        threading.Thread(target=self.start_client, daemon=True).start()

    def check_messages(self):
        updated = 0
        while not self.message_queue.empty():
            message = self.message_queue.get()
            if message.startswith("#"):
                # extract channel from message prefix
                try:
                    raw_chan = message.split()[0]
                    channel = raw_chan[1:]
                    message = message[len(channel)+2:]
                    # detect join/part system messages (format: "<user> joined" / "<user> left")
                    parts = message.split()
                    if len(parts) >= 2 and parts[-1] in ("joined", "left"):
                        user = parts[0]
                        action = parts[-1]
                        # if the local user left the channel, remove it from our list
                        if action == "left" and hasattr(self, 'client') and getattr(self.client, 'nickname', None) == user:
                            removed = self.channel_manager.remove_channel(channel)
                            if removed:
                                updated += 1
                                # also add an entry to SYSTEM to inform user
                                self.channel_manager.add_message(system_channel, f"#{channel} {message}")
                                # if it was the selected channel, switch to SYSTEM
                                if self.channel_var.get() == channel:
                                    self.channel_var.set(system_channel)
                                # clear client's active channel so post-update selection prefers SYSTEM
                                try:
                                    self.client.active_channel = None
                                except Exception:
                                    pass
                                # skip adding to the removed channel
                                continue
                    # normal message: add to channel history and SYSTEM
                    updated += self.channel_manager.add_message(channel, message)
                    self.channel_manager.add_message(system_channel, f"#{channel} {message}")
                    # if current selection matches channel, display immediately
                    if self.channel_var.get() == channel:
                        self.display_message(message)
                except Exception:
                    pass
            else:
                # for non-channel messages, use a generic key
                self.channel_manager.add_message(system_channel, message)
                if self.channel_var.get() == system_channel:
                    self.display_message(message)

        # if channels changed, refresh the combo values and select system when no active channel
        if updated:
            self._update_channel_list()
            preferred = self.client.active_channel if getattr(self, 'client', None) and getattr(self.client, 'active_channel', None) else system_channel
            self.select_channel(preferred)
        self.master.after(100, self.check_messages)

    def display_message(self, message):
        self.chat_display.config(state='normal')
        self.chat_display.insert(tk.END, message + "\n")
        self.chat_display.config(state='disabled')
        self.chat_display.see(tk.END)

if __name__ == "__main__":
    root = tk.Tk()
    app = IRCClientGUI(root)
    root.mainloop()