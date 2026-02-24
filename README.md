# OTPChat
One time pad chat client

This repository contains an older OTP IRC client and a modernized core package in `otpchat/`.

Quick start (development):

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Generate a new encrypted key store (demo):

```bash
python -m otpchat.tools generate --store key.store --keyout store.key --numkeys 256
```

Note: The current change focuses on providing a safer key storage and OTP manager. See the project plan for additional steps (transport security, GUI decoupling, tests).
