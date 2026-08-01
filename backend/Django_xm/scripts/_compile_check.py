import py_compile
import sys

files = [
    r"Django_xm/apps/chat/services/stream_helpers.py",
    r"Django_xm/apps/chat/services/stream/loop.py",
    r"Django_xm/apps/chat/services/stream/finalizer.py",
    r"Django_xm/apps/chat/services/stream/interrupt.py",
    r"Django_xm/apps/chat/views_chat.py",
]
ok = True
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"OK: {f}")
    except py_compile.PyCompileError as e:
        print(f"FAIL: {f}\n{e}")
        ok = False
sys.exit(0 if ok else 1)
