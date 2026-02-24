from otpchat.client import OTPChatClient
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
        updated_channels = 0
        if channel not in self.channel_messages:
            self.channel_messages[channel] = []
            updated_channels = 1
        self.channel_messages[channel].append(message)
        # keep only the last 100 messages per channel to limit memory usage
        if len(self.channel_messages[channel]) > 100:
            self.channel_messages[channel] = self.channel_messages[channel][-100:]
        return updated_channels
    
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

        self.encrypt_messages = tk.BooleanVar(value=True)

        self.encrypt_messages_checkbox = tk.Checkbutton(master, text="Encrypt messages", variable=self.encrypt_messages, command=self.toggle_encrypt)
        self.encrypt_messages_checkbox.pack(side=tk.LEFT, padx=(10, 0), pady=(0, 10))
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
                    channel = message.split()[0][1:]
                    message = message[len(channel)+2:]  # remove channel prefix and space
                    updated += self.channel_manager.add_message(channel, message)
                    self.channel_manager.add_message(system_channel, f"#{channel} {message}")  # also add to SYSTEM with channel prefix
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

        # if new channels were added, refresh the combo values
        if updated:
            self._update_channel_list()
            self.select_channel(self.client.active_channel)
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