#!/usr/bin/env python3
"""Check the patch queue against a kernel.org tarball, or triage further patches on top of it.

  patchq.py check  [--tarball FILE] [--report FILE] [--drop-applied]
  patchq.py triage [--tarball FILE] [--report FILE] PATCH...

The queue is patches/kernel/*.patch followed by patches/hardened/*.patch, applied in the same
way as by the Makefile. Every patch is classified as

  ok       applies cleanly, possibly at an offset
  fuzz     applies only with fuzz, so the context changed and the result needs a review
  applied  does not apply, but applies in reverse: the change is already in the base
  conflict applies neither way

'check' applies the queue in order and fails on 'applied' and 'conflict'. With
--drop-applied, patches from patches/kernel that are already applied are removed instead.
'triage' applies the queue, then tries each given patch on its own on top of it.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RANK = {'ok': 0, 'fuzz': 1, 'applied': 2, 'conflict': 3}


def makefile_vars():
    values = {}
    with open(os.path.join(TOP, 'Makefile')) as f:
        for line in f:
            m = re.match(r'^(\w+)\s*\??=\s*(.*?)\s*$', line)
            if m and m.group(1) not in values:
                values[m.group(1)] = m.group(2)
    return values


def kernel_version():
    v = makefile_vars()
    return f"{v['KERNEL_MAJ']}.{v['KERNEL_MIN']}.{v['KERNEL_PATCHLEVEL']}"


def default_tarball():
    return os.path.join(TOP, 'upstream', f'linux-{kernel_version()}.tar.xz')


def queue():
    """(path, is_hardened) for each patch, in the order the Makefile applies them."""
    entries = []
    for sub, hardened in (('kernel', False), ('hardened', True)):
        d = os.path.join(TOP, 'patches', sub)
        if os.path.isdir(d):
            entries += [(os.path.join(d, n), hardened) for n in sorted(os.listdir(d))
                        if n.endswith('.patch')]
    return entries


def patch_text(path, hardened):
    if hardened:
        # the release patch sets EXTRAVERSION in a hunk that only applies to its exact base
        return subprocess.run(['filterdiff', '-p1', '-x', 'Makefile', path],
                              check=True, capture_output=True).stdout
    with open(path, 'rb') as f:
        return f.read()


def run_patch(tree, text, *args):
    p = subprocess.run(['patch', '-p1', '--batch', '--no-backup-if-mismatch', *args],
                       cwd=tree, input=text, capture_output=True)
    return p.returncode, p.stdout.decode(errors='replace')


def classify(tree, text):
    rc, out = run_patch(tree, text, '--forward', '--dry-run')
    if rc == 0:
        return ('fuzz' if 'with fuzz' in out else 'ok'), ''
    failed = sum(int(n) for n in re.findall(r'(\d+) out of \d+ hunks? FAILED', out))
    rc, _ = run_patch(tree, text, '--reverse', '--dry-run')
    if rc == 0:
        return 'applied', ''
    return 'conflict', f'{failed} hunk(s) failed' if failed else out.strip().splitlines()[-1]


def extract(tarball, dest):
    subprocess.run(['tar', '-C', dest, '--strip-components=1', '-xf', tarball], check=True)


def apply_queue(tree, drop_applied=False):
    results = []
    for path, hardened in queue():
        text = patch_text(path, hardened)
        status, detail = classify(tree, text)
        if status in ('ok', 'fuzz'):
            rc, out = run_patch(tree, text, '--forward')
            if rc != 0:
                status, detail = 'conflict', out.strip().splitlines()[-1]
        elif status == 'applied' and drop_applied and not hardened:
            os.remove(path)
            detail = 'dropped'
        results.append((os.path.relpath(path, TOP), status, detail))
    return results


def report(title, results, path=None):
    counts = {}
    for _, status, _ in results:
        counts[status] = counts.get(status, 0) + 1
    lines = [f'### {title}', '',
             ', '.join(f'{n} {s}' for s, n in sorted(counts.items(), key=lambda x: RANK[x[0]])), '']
    shown = [r for r in results if r[1] != 'ok']
    if shown:
        lines += ['| patch | status | detail |', '|---|---|---|']
        lines += [f'| `{p}` | {s} | {d} |' for p, s, d in shown]
    else:
        lines.append('All patches apply cleanly.')
    text = '\n'.join(lines) + '\n'
    print(text)
    if path:
        with open(path, 'a') as f:
            f.write(text + '\n')
    return text


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('mode', choices=('check', 'triage'))
    ap.add_argument('patches', nargs='*')
    ap.add_argument('--tarball', default=None)
    ap.add_argument('--report', default=os.environ.get('PATCHQ_REPORT'))
    ap.add_argument('--drop-applied', action='store_true')
    args = ap.parse_args()

    tarball = args.tarball or default_tarball()
    base = os.path.basename(tarball).removesuffix('.tar.xz')
    tree = tempfile.mkdtemp(prefix='patchq-')
    try:
        extract(tarball, tree)
        results = apply_queue(tree, args.drop_applied)
        if args.mode == 'check':
            report(f'Patch queue against {base}', results, args.report)
            bad = [r for r in results
                   if r[1] == 'conflict' or (r[1] == 'applied' and r[2] != 'dropped')]
            sys.exit(1 if bad else 0)

        if any(s == 'conflict' for _, s, _ in results):
            print('warning: the queue itself does not apply, triage results are unreliable',
                  file=sys.stderr)
        triaged = []
        for p in args.patches:
            with open(p, 'rb') as f:
                status, detail = classify(tree, f.read())
            triaged.append((os.path.basename(p), status, detail))
        report(f'Triage against {base} with the patch queue applied', triaged, args.report)
    finally:
        shutil.rmtree(tree)


if __name__ == '__main__':
    main()
