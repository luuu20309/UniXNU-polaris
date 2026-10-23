#!/usr/bin/env python3
"""Restore omitted DriverKit headers from Apple's earlier Darwin 23 source.

Device overlay: git-only replacement for the pinned base revision of this
script. The base revision lists the headers through the GitHub contents API,
whose anonymous per-address quota hosted CI runners exhaust (HTTP 403, seen
in runs 40026525583 and 40027042122). This variant obtains the identical
files through the git smart-HTTP protocol, which has no REST rate limit, and
writes the same destination layout and restored-headers manifest as the
upstream script. Interface (environment variables, paths, output) matches
the upstream revision at the call site in build-xnu-4k-baseline.sh.
"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

TAG = os.environ.get('UNIXNU_DRIVERKIT_TAG', 'xnu-10063.121.3')
TRUST_TAG = os.environ.get('UNIXNU_TRUSTCACHE_TAG', 'xnu-8796.101.5')
REPOSITORY = os.environ.get('XNU_GIT', 'https://github.com/apple-oss-distributions/xnu.git')
DESTINATION = Path('.cache/xnu-work/iokit/DriverKit')
TRUST_DESTINATION = Path('.cache/xnu-work/EXTERNAL_HEADERS/TrustCache')
CLONE = Path('.cache/sources/xnu-git')
MANIFEST = Path('.cache/sources/xnu-restored-headers.json')


def git(*args, capture=False):
    result = subprocess.run(['git', *args], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f'git {" ".join(args[:3])} failed: '
                         f'{(result.stderr or result.stdout).strip()[:400]}')
    return result.stdout if capture else ''


def ensure_clone():
    CLONE.parent.mkdir(parents=True, exist_ok=True)
    if not (CLONE / '.git').exists():
        git('clone', '-q', '--depth', '1', '--branch', TAG, REPOSITORY, str(CLONE))
    if not git('-C', str(CLONE), 'tag', '-l', TRUST_TAG, capture=True).strip():
        git('-C', str(CLONE), 'fetch', '-q', '--depth', '1', 'origin',
            'tag', TRUST_TAG)


def listing(ref, path):
    """Every blob under path at ref, as (git blob sha, path relative to `path`)."""
    out = git('-C', str(CLONE), 'ls-tree', '-r', ref, '--', path, capture=True)
    blobs = []
    for line in out.splitlines():
        meta, name = line.split('\t', 1)
        mode, kind, sha = meta.split()
        if kind == 'blob' and name.endswith('.h'):
            blobs.append((sha, name[len(path) + 1:]))
    return blobs


def restore_one(item, destination, tag):
    sha, relative = item
    target = destination / relative
    if target.exists():
        return None
    data = subprocess.run(['git', '-C', str(CLONE), 'cat-file', 'blob', sha],
                          check=True, capture_output=True).stdout
    if hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() != sha:
        raise SystemExit(f'git blob hash mismatch: {name}')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {'name': relative, 'source_tag': tag, 'git_blob': sha,
            'sha256': hashlib.sha256(data).hexdigest()}


def main():
    if not DESTINATION.is_dir():
        raise SystemExit('Create the local XNU work source before preparing headers')
    ensure_clone()
    jobs = [(sha, name, DESTINATION, TAG)
            for sha, name in listing('HEAD', 'iokit/DriverKit')]
    jobs += [(sha, name, TRUST_DESTINATION, TRUST_TAG)
             for sha, name in listing(TRUST_TAG, 'EXTERNAL_HEADERS/TrustCache')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        restored = [entry for entry in pool.map(
            lambda job: restore_one((job[0], job[1]), job[2], job[3]), jobs) if entry]
    existing = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
    by_name = {entry['name']: entry for entry in existing + restored}
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(list(by_name.values()), indent=2) + '\n')
    print(f'Restored {len(restored)} omitted public headers from Apple; blob hashes verified')


if __name__ == '__main__':
    main()
