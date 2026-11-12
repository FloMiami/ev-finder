#!/usr/bin/env python3
"""Copy the live-watch log to the site folder so the page can read it.

The watcher (watch.py / grade.py) writes model/v6/live_watch/watch-log.json.
The GitHub Pages site serves only the docs/ folder, so the page reads
docs/watch-log.json. This script copies the log over and then checks
both copies still parse as JSON and hold the same number of flags —
if anything does not match, it exits non-zero and leaves the old
docs copy alone.

Run it after watch.py / grade.py, from anywhere:
    python3 model/v6/live_watch/sync_docs.py
(or from the live_watch/ folder: python3 sync_docs.py)

It never calls any API and spends no credits.
"""

import json
import os
import shutil
import sys

# The script lives in <repo>/model/v6/live_watch/; the repo root is
# three folders up, and the site folder is <repo>/docs/.
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SOURCE = os.path.join(HERE, "watch-log.json")
DEST = os.path.join(REPO_ROOT, "docs", "watch-log.json")


def load_log(path):
    """Read a log file and return its parsed JSON."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    """Copy the log, then verify the copy matches the source."""
    if not os.path.exists(SOURCE):
        print("No log to sync yet: %s does not exist" % SOURCE)
        return 1
    source = load_log(SOURCE)
    if not isinstance(source.get("flags"), list):
        print("Source log has no flags list — refusing to sync")
        return 1
    # Copy to a temporary file first, then move it into place, so the
    # docs copy is never half-written if the copy is interrupted.
    tmp = DEST + ".tmp"
    shutil.copyfile(SOURCE, tmp)
    os.replace(tmp, DEST)
    # Verify: the docs copy must parse and hold the same flags.
    dest = load_log(DEST)
    if len(dest.get("flags", [])) != len(source["flags"]):
        print("Sync check failed: source has %d flags, docs copy has %d"
              % (len(source["flags"]), len(dest.get("flags", []))))
        return 1
    record = dest.get("record", {})
    print("Synced docs/watch-log.json: %d flag(s), %d graded, "
          "record %d-%d-%d"
          % (len(dest["flags"]), record.get("graded", 0),
             record.get("wins", 0), record.get("losses", 0),
             record.get("pushes", 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
