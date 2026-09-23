import json
import sys

command = sys.argv[1]
options = dict(zip(sys.argv[2::2], sys.argv[3::2]))
if command == "snapshots":
    print(json.dumps({"type": "snapshots", "snapshots": [
        {"id": "b" * 64, "shortId": "bbbbbbbb", "time": "2026-08-17T03:30:00Z", "paths": ["/home/test"]},
        {"id": "a" * 64, "shortId": "aaaaaaaa", "time": "2026-08-16T03:30:00Z", "paths": ["/home/test"]},
    ]}))
elif options["--path"] == "/home/test":
    print(json.dumps({"type": "entries", "path": "/home/test", "truncated": False, "entries": [
        {"name": "Documents", "type": "dir", "path": "/home/test/Documents", "size": None},
        {"name": "Pictures", "type": "dir", "path": "/home/test/Pictures", "size": None},
        {"name": "<b>ENTRY_LITERAL</b>.txt", "type": "file", "path": "/home/test/<b>ENTRY_LITERAL</b>.txt", "size": 42},
    ]}))
elif options["--path"] == "/home/test/Pictures" and options["--snapshot"] == "a" * 64:
    print(json.dumps({"type": "error", "error": "path /home/test/Pictures: not found"}))
else:
    print(json.dumps({"type": "entries", "path": options["--path"], "truncated": False, "entries": [
        {"name": "notes.md", "type": "file", "path": options["--path"] + "/notes.md", "size": 1024},
    ]}))
