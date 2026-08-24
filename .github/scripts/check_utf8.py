"""Fail if any git-tracked text file is not valid UTF-8."""
import subprocess
import sys

# This repo was corrupted once by a Windows script that read/wrote files as
# ANSI instead of UTF-8. This check catches that class of mistake in CI.
SKIP_EXTENSIONS = {".db", ".db-wal", ".db-shm", ".png", ".jpg", ".jpeg",
                    ".gif", ".ico", ".webp", ".bmp"}

files = subprocess.run(
    ["git", "ls-files"], capture_output=True, text=True, check=True
).stdout.splitlines()

bad = []
for path in files:
    if any(path.lower().endswith(ext) for ext in SKIP_EXTENSIONS):
        continue
    with open(path, "rb") as f:
        data = f.read()
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as e:
        bad.append((path, str(e)))

for path, err in bad:
    print(f"::error file={path}::not valid UTF-8 ({err})")

if bad:
    sys.exit(1)

print(f"checked {len(files)} tracked files, all valid UTF-8")
