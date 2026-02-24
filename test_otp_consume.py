from otpchat.storage import EncryptedStorage
from otpchat.crypto import OTPManager
import os

store = 'tmp_test.store'
key = 'tmp_test.key'
if os.path.exists(store):
    os.remove(store)
if os.path.exists(key):
    os.remove(key)

fernet = EncryptedStorage.generate_key()
with open(key, 'wb') as f:
    f.write(fernet)

storage = EncryptedStorage(store, fernet)
mgr = OTPManager(storage)
mgr.generate(mgr.alphabet, mgr.msg_len if hasattr(mgr, 'msg_len') else 64, 256)
print('initial count', mgr.key_count())
success = 0
fail = 0
for i in range(300):
    c, ok = mgr.encode('hello')
    if ok:
        success += 1
    else:
        fail += 1
print('success', success, 'fail', fail)
print('final count', mgr.key_count())
