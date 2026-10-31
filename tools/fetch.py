#!/usr/bin/env python3
"""Fetch third-party build inputs listed in assets.lock into the third_party directory.

Each entry lists mirrors, tried in order (env FETCH_MIRROR_FIRST=<substring>
moves a matching mirror to the front). A download is checked against the
mirror's sha256, extracted according to the entry kind (raw | zip | exe),
checked against the entry's accepted final sha256 list, and written
atomically. Entries whose final file already matches are skipped. Downloaded
executables are only unpacked with 7z/cabextract, never run.

Usage: fetch.py [--dest DIR] [--lock FILE] [NAME ...]
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_UA = "iridium-kicad-lib-ci/1.0 (+https://github.com/ndoo/iridium-kicad-lib)"
RETRIES = 4


class FetchError(Exception):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, dest):
    ua = os.environ.get("FETCH_UA") or DEFAULT_UA
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f)
            return
        except Exception as e:  # noqa: BLE001
            last = e
            if getattr(e, "code", None) in (400, 401, 403, 404, 410):
                break
            if attempt < RETRIES:
                time.sleep(2 ** attempt)
    raise FetchError(f"download failed: {last}")


def run_extract(cmds):
    errors = []
    for cmd in cmds:
        if shutil.which(cmd[0]) is None:
            continue
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if r.returncode == 0:
            return
        errors.append(f"{' '.join(cmd[:2])}: {r.stdout.strip()[-300:]}")
    if not errors:
        raise FetchError("none of 7z, 7zz, cabextract found in PATH")
    raise FetchError("extraction failed: " + "; ".join(errors))


def extract(spec, archive, tmp):
    """Return path of the extracted final file inside tmp."""
    kind = spec["kind"]
    if kind == "raw":
        return archive
    member = spec["member"]
    out = os.path.join(tmp, "x")
    os.makedirs(out)
    if kind == "zip":
        with zipfile.ZipFile(archive) as z:
            if member not in z.namelist():
                raise FetchError(f"member {member!r} not in zip")
            with z.open(member) as src, open(os.path.join(out, "member"), "wb") as dst:
                shutil.copyfileobj(src, dst)
        return os.path.join(out, "member")
    if kind == "exe":
        run_extract([["7z", "x", "-y", f"-o{out}", archive, member],
                     ["7zz", "x", "-y", f"-o{out}", archive, member],
                     ["cabextract", "-L", "-q", "-d", out, archive]])
        for fn in os.listdir(out):
            if fn.lower() == member.lower():
                return os.path.join(out, fn)
        raise FetchError(f"member {member!r} not found after extraction")
    raise FetchError(f"unknown kind {kind!r}")


def try_mirror(entry, mirror, tmp):
    """Download from one mirror; return (path of final file, sha256)."""
    archive = os.path.join(tmp, "download")
    download(mirror["url"], archive)
    got = sha256_file(archive)
    if got != mirror["sha256"]:
        raise FetchError(f"download sha256 mismatch: expected {mirror['sha256']}, got {got}")
    spec = dict(entry)
    spec.update({k: mirror[k] for k in ("kind", "member") if k in mirror})
    src = extract(spec, archive, tmp)
    got = sha256_file(src)
    if got not in {f["sha256"] for f in entry["finals"]}:
        raise FetchError(f"extracted file sha256 {got} is not in the accepted list")
    return src, got


def fetch_entry(entry, dest):
    name = entry["name"]
    finals = {f["sha256"]: f for f in entry["finals"]}
    final = os.path.join(dest, entry["dest"])
    if os.path.isfile(final) and sha256_file(final) in finals:
        print(f"{name}: up to date")
        return
    mirrors = list(entry["mirrors"])
    first = os.environ.get("FETCH_MIRROR_FIRST")
    if first:
        mirrors.sort(key=lambda m: first not in m["url"])
    failures = []
    for m in mirrors:
        print(f"{name}: trying {m['url']}")
        with tempfile.TemporaryDirectory(prefix="fetch-") as tmp:
            try:
                src, got = try_mirror(entry, m, tmp)
            except (FetchError, OSError, zipfile.BadZipFile) as ex:
                note = f"\n    note: {m['note']}" if m.get("note") else ""
                failures.append(f"  {m['url']}: {ex}{note}")
                print(f"{name}: mirror failed: {ex}", file=sys.stderr)
                continue
            os.makedirs(os.path.dirname(final), exist_ok=True)
            # Per process, so parallel make jobs fetching the same entry do not collide
            part = f"{final}.{os.getpid()}.part"
            shutil.copyfile(src, part)
            os.replace(part, final)
        f = finals[got]
        if not f.get("preferred"):
            print(f"WARNING: {name}: used non-preferred version: {f.get('label', got)}", file=sys.stderr)
        print(f"{name}: ok from {m['url']} -> {final}")
        return
    raise FetchError(f"{name}: all mirrors failed\n" + "\n".join(failures))


def verify_entry(entry, dest):
    """Offline check of the fetched file against the lock; True if it is an accepted version."""
    final = os.path.join(dest, entry["dest"])
    if not os.path.isfile(final):
        print(f"{entry['name']}: MISSING {final}")
        return False
    got = sha256_file(final)
    for f in entry["finals"]:
        if f["sha256"] == got:
            print(f"{entry['name']}: ok ({f.get('label', got)})")
            return True
    print(f"{entry['name']}: MISMATCH {final} sha256 {got}")
    return False


def check_mirrors(entry):
    """Download every mirror into a temp dir and check both checksums; True if all pass."""
    ok = True
    for m in entry["mirrors"]:
        with tempfile.TemporaryDirectory(prefix="fetch-") as tmp:
            try:
                _, got = try_mirror(entry, m, tmp)
            except (FetchError, OSError, zipfile.BadZipFile) as ex:
                print(f"{entry['name']}: FAIL {m['url']}: {ex}")
                ok = False
                continue
        label = next(f.get("label", got) for f in entry["finals"] if f["sha256"] == got)
        print(f"{entry['name']}: ok {m['url']} -> {label}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true", help="check fetched files against the lock; no network")
    mode.add_argument("--check-mirrors", action="store_true", help="download and check every mirror; writes nothing to --dest")
    ap.add_argument("--dest", default=os.environ.get("THIRD_PARTY") or os.path.join(ROOT, "third_party"))
    ap.add_argument("--lock", default=os.path.join(ROOT, "assets.lock"))
    ap.add_argument("names", nargs="*")
    args = ap.parse_args()
    with open(args.lock) as f:
        entries = json.load(f)["entries"]
    known = {e["name"] for e in entries}
    unknown = [n for n in args.names if n not in known]
    if unknown:
        sys.exit(f"fetch: unknown entries: {', '.join(unknown)} (known: {', '.join(sorted(known))})")
    failed = 0
    for e in entries:
        if args.names and e["name"] not in args.names:
            continue
        if args.verify or args.check_mirrors:
            failed += not (verify_entry(e, args.dest) if args.verify else check_mirrors(e))
            continue
        try:
            fetch_entry(e, args.dest)
        except FetchError as ex:
            print(f"FETCH ERROR: {ex}", file=sys.stderr)
            failed += 1
    if failed:
        sys.exit(f"fetch: {failed} entr{'y' if failed == 1 else 'ies'} failed")


if __name__ == "__main__":
    main()
