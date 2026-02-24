from .crypto import OTPManager
from .storage import EncryptedStorage
import socket
import threading
import secrets

class OTPChatClient:

    EXCLUDE_MSGS = ['001','002','003','004','005','396','251','255','265','266','422','MODE']

    def __init__(self, client_message_queue, key_store_path=None, key_store_password=None):
        self.client_message_queue = client_message_queue
        self.key_store_path = key_store_path
        self.key_store_password = key_store_password
        # Initialize other necessary attributes
        if key_store_path and key_store_password:
            self.storage = EncryptedStorage(key_store_path, key_store_password)
            self.otp_manager = OTPManager(self.storage)
        else:
            self.storage = None
            self.otp_manager = None
        self.encrypt_messages = True

        self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.connected = False

        self.known_channels = set()
        self.known_users = set()

        self.system_channel = "SYSTEM"
        
        self.active_channel = None

    def load_new_keys(self, key_store_path, key_store_password):
        # Implement logic to load new keys from the key store
        self.key_store_path = key_store_path
        self.key_store_password = key_store_password
        if key_store_path and key_store_password:
            self.storage = EncryptedStorage(key_store_path, key_store_password)
            self.otp_manager = OTPManager(self.storage)
        else:
            self.storage = None
            self.otp_manager = None

    def toggle_encrypt(self, encrypt: bool):
        # Implement logic to toggle encryption on/off
        self.encrypt_messages = encrypt

    def set_user_data(self, username, realname):
        # Implement logic to set user data for IRC connection
        self.username = username
        self.realname = realname
    
    def set_nick(self, nickname):
        # Implement logic to set the nickname for IRC connection
        self.nickname = nickname

    def connect(self, server, port):
        # Implement connection logic to the IRC server
        # check if user data is set, then connect and start listening for messages
        try:
            if not hasattr(self, 'username') or not hasattr(self, 'realname') or not hasattr(self, 'nickname'):
                raise ValueError("User data (username, realname, nickname) must be set before connecting.")
            self.client_socket.connect((server, port))
            self.connected = True
            self.client_socket.send(f"NICK {self.nickname}\r\n".encode('utf-8'))
            self.client_socket.send(f"USER {self.username} 0 * :{self.realname}\r\n".encode('utf-8'))
            self.known_channels = set()
            self.known_users = set()
            self.active_channel = None
            threading.Thread(target=self.receive_message, daemon=True).start()
        except Exception as e:
            print("Failed to connect to server:", e)
            self.connected = False

    def _parse_client_message(self, message):
        # Check if active channel is set and message is not a server message, then encrypt and send to active channel
        success = False
        if self.active_channel == None and message[0] != "/":
            print("Message does not start with '/', ignoring.")
            self.client_message_queue.put("Message does not start with '/', ignoring.")
            message = None
        else:
            if message[0] == "/":
                # Handle commands (e.g., /join, /part, /msg)
                command_parts = message[1:].split(' ', 1)
                command = command_parts[0].lower()
                if command == "join" and len(command_parts) > 1:
                    channel = command_parts[1]
                    message = f"JOIN {channel}\r\n".encode('utf-8')
                    self.active_channel = channel
                elif command == "part":
                    if len(command_parts) <= 1:
                        channel = self.active_channel
                    elif len(command_parts) > 1:
                        channel = command_parts[1]
                    message = f"PART {channel}\r\n".encode('utf-8')
                    if self.active_channel == channel:
                        self.active_channel = None
                elif command == "msg" and len(command_parts) > 1:
                    target_message = command_parts[1].split(' ', 1)
                    if len(target_message) > 1:
                        target = target_message[0]
                        msg = target_message[1]
                        encrypted_msg, success = self.otp_manager.encode(msg)
                        if success:
                            message = f"PRIVMSG {target} :{encrypted_msg}\r\n".encode('utf-8')
                        else:
                            print("Failed to encrypt message:", encrypted_msg)
                            message = None
                            self.client_message_queue.put(f"Failed to encrypt message: {msg}")
                elif command == "list":
                    message = f"LIST\r\n".encode('utf-8')
                elif command == "names":
                    message = f"NAMES\r\n".encode('utf-8')
                elif command == "whois" and len(command_parts) > 1:
                    target = command_parts[1]
                    message = f"WHOIS {target}\r\n".encode('utf-8')
                elif command == "motd":
                    message = f"MOTD\r\n".encode('utf-8')
                else:
                    print("Unknown command or missing argument.")
                    self.client_message_queue.put(f"{self.active_channel if self.active_channel else self.system_channel} Unknown command or missing argument: {message}")
                    message = None
            else:
                if self.encrypt_messages and self.active_channel and self.otp_manager.keys:
                    encrypted_msg, success = self.otp_manager.encode(message)
                    if success:
                        message = f"PRIVMSG {self.active_channel} :{encrypted_msg}\r\n".encode('utf-8')
                    else:
                        print("Failed to encrypt message:", encrypted_msg)
                        self.client_message_queue.put(f"{self.active_channel} Failed to encrypt message: {message}\r\n^ ERROR ^")
                        return(None, False)
                elif self.encrypt_messages and self.otp_manager.key_count() == 0:
                    print("No OTP keys available, message not sent.")
                    self.client_message_queue.put(f"{self.active_channel} No OTP keys available, message not sent.\r\n^ ERROR ^")
                    message = None
                else:
                    message = f"PRIVMSG {self.active_channel} :{message}\r\n".encode('utf-8')
        return(message, success)

    def send_message(self, message):
        # Implement logic to send a message to the IRC server
        original = message
        message, encrypted = self._parse_client_message(message)
        if message:
            print(f"Sending message: {message.decode('utf-8').strip()}")
            try:
                self.client_socket.send(message)
            except Exception as e:
                print("Error sending message:", e)
                return
            if self.client_message_queue:
                try:
                    encrypted_note = "!enc" if encrypted else ""
                    self.client_message_queue.put(f"{self.active_channel} <{self.nickname}{encrypted_note}> {original}")
                except Exception:
                    pass

    def _parse_server_message(self, message):
        #IRC message parser from http://twistedmatrix.com/trac/browser/trunk/twisted/words/protocols/irc.py#54
        prefix = ''
        trailing = []
        if not message:
            raise Exception("Empty line.")
        if message[0] == ':':
            prefix, message = message[1:].split(' ', 1)
        if message.find(' :') != -1:
            message, trailing = message.split(' :', 1)
            args = message.split()
            args.append(trailing)
        else:
            args = message.split()
        command = args.pop(0)
        return(prefix, command, args)

    def receive_message(self):
        # Implement logic to receive messages from the IRC server and handle them (e.g., update known channels/users, print messages)
        print("Started message receiving thread.")
        buffer = ""
        while self.connected:
            try:
                data = self.client_socket.recv(1024).decode('utf-8', errors='replace')
                if not data:
                    print("Connection closed by server.")
                    self.connected = False
                    break
                buffer += data
                while '\r\n' in buffer:
                    line, buffer = buffer.split('\r\n', 1)
                    if line:
                        #print(f"Raw message received: {line.strip()}")
                        try:
                            prefix, command, args = self._parse_server_message(line)
                        except Exception as parse_exc:
                            print(f"Failed to parse server message: {line} ({parse_exc})")
                            continue
                        # print/queue non-verbose server messages (skip common numeric replies)
                        if command not in self.EXCLUDE_MSGS and command != "PRIVMSG":
                            print(f"Received message: {line.strip()}")
                            if self.client_message_queue:
                                try:
                                    pass
                                    #self.client_message_queue.put(line.strip())
                                except Exception:
                                    pass
                        if command == "PING":
                            self.client_socket.send(f"PONG {args[0]}\r\n".encode('utf-8'))
                        # Handle specific commands to update known channels/users
                        if command == "JOIN":
                            channel = args[0]
                            user = prefix.split('!')[0]
                            self.known_channels.add(channel)
                            if self.client_message_queue:
                                try:
                                    self.client_message_queue.put(f"{channel} {user} joined")
                                except Exception:
                                    pass
                        elif command == "PART":
                            channel = args[0]
                            user = prefix.split('!')[0]
                            self.known_channels.discard(channel)
                            if self.client_message_queue:
                                try:
                                    self.client_message_queue.put(f"{channel} {user} left")
                                except Exception:
                                    pass
                        elif command == "PRIVMSG":
                            sender = prefix.split('!')[0]
                            self.known_users.add(sender)
                            # Attempt to decode message if it appears to be encrypted
                            display_text = None
                            if len(args) > 1 and self.otp_manager and len(self.otp_manager.keys) > 0 and len(args[1]) == self.otp_manager.msg_len + self.otp_manager._get_key_len_hex():
                                decoded_msg, success = self.otp_manager.decode(args[1])
                                if success:
                                    display_text = f"{channel} <{sender}!enc> {decoded_msg}"
                                    print(f"Decrypted message from {sender}: {args[1]} -> {decoded_msg}")
                                else:
                                    display_text = f"{channel} <{sender}!enc> (failed to decrypt) {args[1]}"
                                    print(f"Failed to decrypt message from {sender}: {args[1]}")
                            else:
                                # args[-1] holds the trailing message text
                                msg_text = args[-1] if args else ''
                                display_text = f"{channel} <{sender}> {msg_text}"
                                print(display_text)
                            if self.client_message_queue and display_text:
                                try:
                                    self.client_message_queue.put(display_text)
                                except Exception:
                                    pass
                        elif command == "NOTICE":
                            sender = prefix.split('!')[0]
                            self.known_users.add(sender)
                            print(f"Notice from {sender}: {' '.join(args)}")
                            if self.client_message_queue:
                                try:
                                    self.client_message_queue.put(f"NOTICE from {sender}: {' '.join(args)}")
                                except Exception:
                                    pass
                        elif command == "372":  # MOTD
                            print(f"MOTD: {' '.join(args)}")
                            if self.client_message_queue:
                                try:
                                    if len(args[1].strip()) > 0:
                                        self.client_message_queue.put(f"MOTD: {args[1].strip()}")
                                except Exception:
                                    pass
                        elif command == "322":  # Channel list
                            channel = args[1]
                            self.known_channels.add(channel)
                            if self.client_message_queue:
                                try:
                                    self.client_message_queue.put(f"CHANNEL: {channel}")
                                except Exception:
                                    pass
                        elif command == "401":  # No such nick/channel
                            print(f"Error: {' '.join(args)}")
                            if self.client_message_queue:
                                try:
                                    self.client_message_queue.put(f"ERROR: {' '.join(args)}")
                                except Exception:
                                    pass
                        elif command == "433": # Nickname in use
                            # Server reports nickname is already in use. Try an alternative nick
                            attempted = args[1] if len(args) > 1 else getattr(self, "nickname", "")
                            alt = f"{getattr(self, 'nickname', 'user')}_{secrets.token_hex(2)}"
                            # ensure nickname not excessively long
                            alt = alt[:30]
                            self.nickname = alt
                            try:
                                self.client_socket.send(f"NICK {alt}\r\n".encode('utf-8'))
                                if self.client_message_queue:
                                    try:
                                        self.client_message_queue.put(f"Nickname '{attempted}' in use, trying '{alt}'")
                                    except Exception:
                                        pass
                            except Exception as e:
                                print("Failed to change nickname:", e)
                                if self.client_message_queue:
                                    try:
                                        self.client_message_queue.put(f"Failed to change nick: {e}")
                                    except Exception:
                                        pass
            except Exception as e:
                print("Error receiving message:", e)
                self.connected = False