#!/usr/bin/env python3
"""Copy the live-watch logs to the site folder so the page can read them.

The watcher (watch.py / grade.py) writes model/v6/live_watch/watch-log.json,
and hand-called picks live in manual-picks-log.json beside it.
The GitHub Pages site serves only the docs/ folder, so the page reads
docs/watch-log.json and docs/manual-picks-log.json. This script copies
each log over and then checks both copies still parse as JSON and hold
the same number of flags — if anything does not match, it exits
non-zero and leaves the old docs copy alone.

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
MANUAL_SOURCE = os.path.join(HERE, "manual-picks-log.json")
MANUAL_DEST = os.path.join(REPO_ROOT, "docs", "manual-picks-log.json")


def load_log(path):
    """Read a log file and return its parsed JSON."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def sync_file(source, dest, label):
    """Copy one log to docs/ with temp-file + verify.

    Copies to a temporary file first, then moves it into place, so
    the docs copy is never half-written if the copy is interrupted.
    Then checks the docs copy still parses and holds the same number
    of flags. Returns True on success, False when the source log
    does not exist yet or the copy does not verify."""
    if not os.path.exists(source):
        print("No %s log to sync yet: %s does not exist" % (label, source))
        return False
    source_log = load_log(source)
    if not isinstance(source_log.get("flags"), list):
        print("Source %s log has no flags list — refusing to sync" % label)
        return False
    tmp = dest + ".tmp"
    shutil.copyfile(source, tmp)
    os.replace(tmp, dest)
    # Verify: the docs copy must parse and hold the same flags.
    dest_log = load_log(dest)
    if len(dest_log.get("flags", [])) != len(source_log["flags"]):
        print("Sync check failed for %s: source has %d flags, docs copy "
              "has %d" % (label, len(source_log["flags"]),
                          len(dest_log.get("flags", []))))
        return False
    record = dest_log.get("record", {})
    print("Synced docs/%s: %d flag(s), %d graded, record %d-%d-%d"
          % (os.path.basename(dest), len(dest_log["flags"]),
             record.get("graded", 0), record.get("wins", 0),
             record.get("losses", 0), record.get("pushes", 0)))
    return True


def main():
    """Copy the watch log, then the manual-picks log if it exists.

    The watch log is required — without it there is nothing to
    publish. The manual log is optional: it only exists once a
    pick has been called in, so a missing one is skipped with a
    message and the run still exits 0 when the watch log synced."""
    if not sync_file(SOURCE, DEST, "watch"):
        return 1
    sync_file(MANUAL_SOURCE, MANUAL_DEST, "manual picks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
