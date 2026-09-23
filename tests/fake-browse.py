import json
import sys

command = sys.argv[1]
options = dict(argument.split("=", 1) for argument in sys.argv[2:])
if command == "snapshots":
    print(json.dumps({"type": "snapshots", "snapshots": [
        {"id": "b" * 64, "shortId": "bbbbbbbb", "time": "2026-08-17T03:30:00Z", "paths": ["/home/test"]},
        {"id": "a" * 64, "shortId": "aaaaaaaa", "time": "2026-08-16T03:30:00Z", "paths": ["/home/test"]},
    ]}))
elif options["--path"] == "/home/test":
    print(json.dumps({"type": "entries", "path": "/home/test", "truncated": False, "exists": True, "entries": [
        {"name": "Documents", "type": "dir", "path": "/home/test/Documents", "size": None},
        {"name": "Pictures", "type": "dir", "path": "/home/test/Pictures", "size": None},
        {"name": "config.toml", "type": "file", "path": "/home/test/config.toml", "size": 12},
        {"name": "<b>ENTRY_LITERAL</b>.txt", "type": "file", "path": "/home/test/<b>ENTRY_LITERAL</b>.txt", "size": 42},
    ]}))
elif options["--path"] == "/home/test/Pictures" and options["--snapshot"] == "a" * 64:
    print(json.dumps({"type": "error", "error": "path /home/test/Pictures: not found"}))
elif options["--path"] in ("/home/test/Empty", "/home/test/Gone", "/home/test/config.toml"):
    kind = {"/home/test/Empty": "dir", "/home/test/Gone": "missing", "/home/test/config.toml": "file"}[options["--path"]]
    print(json.dumps({"type": "entries", "path": options["--path"], "truncated": False, "entries": [],
                      "exists": kind == "dir", "kind": kind}))
elif options["--path"] == "/home/test/Long":
    print(json.dumps({"type": "entries", "path": "/home/test/Long", "truncated": False, "exists": True, "kind": "dir",
                      "entries": [{"name": "quarterly-report-2026-final-version-reviewed-by-legal.pdf", "type": "file",
                                   "path": "/home/test/Long/quarterly-report-2026-final-version-reviewed-by-legal.pdf",
                                   "size": 10}]}))
else:
    print(json.dumps({"type": "entries", "path": options["--path"], "truncated": False, "exists": True, "entries": [
        {"name": "notes.md", "type": "file", "path": options["--path"] + "/notes.md", "size": 1024},
    ]}))
