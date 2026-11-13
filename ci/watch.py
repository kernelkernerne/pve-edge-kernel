#!/usr/bin/env python3
"""Look for upstream updates and propose them, run daily by .github/workflows/watch.yml.

Run from a full clone of the branch to update (fetch-depth 0), with GH_TOKEN set.

Updates that can be applied mechanically go into one pull request from the branch
'ci/updates/<branch>', regenerated whenever the set of available updates changes:

  linux     newer kernel.org stable release of the current series; the tarball signature is
            verified against keys/kernel.org.gpg, patches it already contains are dropped
  hardened  newer linux-hardened release for the current series; signature verified against
            keys/linux-hardened.gpg
  arch      changed Arch Linux linux-hardened config (config/linux-hardened-amd64.config)
  zfs       new commits of Proxmox zfsonlinux

Things that need a human are reported in issues instead:

  - a newer stable kernel series
  - new Proxmox pve-kernel commits, with their patches triaged against the current queue

Options: --dry-run only prints what would be done.
"""

import argparse
import email.utils
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import patchq  # noqa: E402

TOP = patchq.TOP
KERNEL_ORG = 'https://cdn.kernel.org/pub/linux/kernel'
HARDENED_REPO = 'anthraxx/linux-hardened'
ARCH_CONFIG_REPO = 'https://gitlab.archlinux.org/archlinux/packaging/packages/linux-hardened.git'
ARCH_CONFIG_FILE = 'config.x86_64'
ZFS_REPO = 'https://git.proxmox.com/git/zfsonlinux.git'
PVE_KERNEL_REPO = 'https://git.proxmox.com/git/pve-kernel.git'
MAINTAINER = 'pve-edge-kernel <pve-edge-kernel@users.noreply.github.com>'
LABEL = 'upstream'

DRY_RUN = False


def run(*cmd, cwd=TOP, check=True, capture=True, input=None):
    p = subprocess.run(cmd, cwd=cwd, check=check, input=input, text=True,
                       capture_output=capture)
    return p.stdout.strip() if capture else None


def git(*args, **kw):
    return run('git', *args, **kw)


def gh(*args, **kw):
    return run('gh', *args, **kw)


def fetch(url, dest=None):
    req = urllib.request.Request(url, headers={'User-Agent': 'pve-edge-kernel-ci'})
    with urllib.request.urlopen(req, timeout=300) as r:
        if dest is None:
            return r.read()
        with open(dest, 'wb') as f:
            shutil.copyfileobj(r, f)
    return dest


def vtuple(v):
    return tuple(int(x) for x in re.findall(r'\d+', v))


def gpgv(keyring, sig, data_path=None, data_cmd=None):
    cmd = ['gpgv', '--keyring', os.path.join(TOP, 'keys', keyring), sig, data_path or '-']
    if data_cmd:
        src = subprocess.Popen(data_cmd, stdout=subprocess.PIPE)
        subprocess.run(cmd, stdin=src.stdout, check=True, capture_output=True)
        if src.wait() != 0:
            raise RuntimeError(f'{data_cmd} failed')
    else:
        subprocess.run(cmd, check=True, capture_output=True)


# ---- current state -------------------------------------------------------------------------

def current():
    v = patchq.makefile_vars()
    hardened = [n for n in os.listdir(os.path.join(TOP, 'patches', 'hardened')) if n.endswith('.patch')]
    return {
        'maj': int(v['KERNEL_MAJ']), 'min': int(v['KERNEL_MIN']),
        'patchlevel': int(v['KERNEL_PATCHLEVEL']), 'krel': int(v['KREL']),
        'hardened': hardened[0].removeprefix('linux-hardened-').removesuffix('.patch') if hardened else None,
        'zfs': git('ls-tree', 'HEAD', 'submodules/zfsonlinux').split()[2],
    }


# ---- update sources --------------------------------------------------------------------------

def kernel_releases():
    return json.loads(fetch('https://www.kernel.org/releases.json'))['releases']


def find_linux(cur, releases):
    series = f"{cur['maj']}.{cur['min']}"
    best = None
    for r in releases:
        v = r['version']
        if v == series or v.startswith(series + '.'):
            pl = int(v.split('.')[2]) if v.count('.') == 2 else 0
            if best is None or pl > best:
                best = pl
    return best if best is not None and best > cur['patchlevel'] else None


def find_hardened(cur):
    series = f"{cur['maj']}.{cur['min']}"
    rels = json.loads(gh('api', f'repos/{HARDENED_REPO}/releases?per_page=100'))
    tags = [r['tag_name'] for r in rels
            if re.fullmatch(rf'v{re.escape(series)}(\.\d+)?-hardened\d+', r['tag_name'])
            and not r['prerelease']]
    if not tags:
        return None
    best = max(tags, key=vtuple)
    return best if cur['hardened'] is None or vtuple(best) > vtuple(cur['hardened']) else None


def find_arch_config():
    out = git('ls-remote', ARCH_CONFIG_REPO, 'refs/heads/main', cwd=None)
    commit = out.split()[0]
    with open(os.path.join(TOP, 'config', 'edge-amd64.policy')) as f:
        pinned = re.search(r'\(config\.x86_64 at ([0-9a-f]{40})', f.read())
    return None if pinned and pinned.group(1) == commit else commit


def find_zfs(cur):
    head = git('ls-remote', ZFS_REPO, 'refs/heads/master', cwd=None).split()[0]
    return None if head == cur['zfs'] else head


# ---- applying updates ----------------------------------------------------------------------

def commit(message, paths):
    git('add', '-A', '--', *paths)
    git('commit', '-q', '-m', message + '\n\nGenerated by ci/watch.py.')


def apply_linux(cur, patchlevel, work):
    ver = f"{cur['maj']}.{cur['min']}.{patchlevel}"
    tarball = os.path.join(TOP, 'upstream', f'linux-{ver}.tar.xz')
    base = f"{KERNEL_ORG}/v{cur['maj']}.x/linux-{ver}"
    fetch(base + '.tar.xz', tarball)
    sign = fetch(base + '.tar.sign', os.path.join(work, f'linux-{ver}.tar.sign'))
    gpgv('kernel.org.gpg', sign, data_cmd=['xz', '-cd', tarball])
    sha = run('sha256sum', os.path.basename(tarball), cwd=os.path.dirname(tarball))
    for old in os.listdir(os.path.join(TOP, 'upstream')):
        if old.endswith('.sha256'):
            os.remove(os.path.join(TOP, 'upstream', old))
    with open(tarball + '.sha256', 'w') as f:
        f.write(sha + '\n')
    set_makefile('KERNEL_PATCHLEVEL', str(patchlevel))
    commit(f'update to Linux {ver}', ['upstream', 'Makefile'])

    results = patchq.apply_queue(_tree(tarball, work), drop_applied=True)
    dropped = [p for p, s, d in results if d == 'dropped']
    if dropped:
        commit(f'drop patches included in Linux {ver}\n\n' +
               '\n'.join(f'  {os.path.basename(p)}' for p in dropped), ['patches/kernel'])
    return ver, dropped


def apply_hardened(cur, tag, work):
    d = os.path.join(TOP, 'patches', 'hardened')
    name = f'linux-hardened-{tag}.patch'
    url = f'https://github.com/{HARDENED_REPO}/releases/download/{tag}/{name}'
    new = fetch(url, os.path.join(work, name))
    sig = fetch(url + '.sig', new + '.sig')
    gpgv('linux-hardened.gpg', sig, data_path=new)
    for old in os.listdir(d):
        os.remove(os.path.join(d, old))
    shutil.copy(new, d)
    shutil.copy(sig, d)
    commit(f'update linux-hardened to {tag}', ['patches/hardened'])


def apply_arch_config(commit_id, work):
    clone = os.path.join(work, 'arch')
    git('clone', '-q', '--depth', '1', ARCH_CONFIG_REPO, clone, cwd=None)
    shutil.copy(os.path.join(clone, ARCH_CONFIG_FILE),
                os.path.join(TOP, 'config', 'linux-hardened-amd64.config'))
    pkgver = re.search(r'^pkgver=(\S+)', open(os.path.join(clone, 'PKGBUILD')).read(), re.M)
    pkgrel = re.search(r'^pkgrel=(\S+)', open(os.path.join(clone, 'PKGBUILD')).read(), re.M)
    ident = f'{pkgver.group(1)}-{pkgrel.group(1)}' if pkgver and pkgrel else 'unknown'
    policy = os.path.join(TOP, 'config', 'edge-amd64.policy')
    text = open(policy).read()
    text = re.sub(r'\(config\.x86_64 at [0-9a-f]{40}, [^)]*\)',
                  f'(config.x86_64 at {commit_id}, {ident})', text)
    open(policy, 'w').write(text)
    commit(f'update the Arch Linux linux-hardened config to {ident}', ['config'])
    return ident


def apply_zfs(cur, head, work):
    clone = os.path.join(work, 'zfs.git')
    git('clone', '-q', '--bare', '--filter=blob:none', ZFS_REPO, clone, cwd=None)
    log = git('log', '--format=  %h %s', f"{cur['zfs']}..{head}", cwd=clone)
    git('update-index', '--cacheinfo', f'160000,{head},submodules/zfsonlinux')
    git('commit', '-q', '-m', f'update ZFS submodule to {head[:12]}\n\n{log}\n\nGenerated by ci/watch.py.')
    return log


def _tree(tarball, work):
    tree = tempfile.mkdtemp(prefix='tree-', dir=work)
    patchq.extract(tarball, tree)
    return tree


def set_makefile(var, value):
    path = os.path.join(TOP, 'Makefile')
    text = open(path).read()
    text, n = re.subn(rf'^{var}=.*$', f'{var}={value}', text, count=1, flags=re.M)
    if n != 1:
        raise RuntimeError(f'{var} not found in the Makefile')
    open(path, 'w').write(text)


def bump_version(cur, items, linux_updated):
    krel = 1 if linux_updated else cur['krel'] + 1
    set_makefile('KREL', str(krel))
    version = f"{patchq.kernel_version()}-{krel}"
    path = os.path.join(TOP, 'debian', 'changelog')
    text = open(path).read()
    source = text.split(' ', 1)[0]
    body = '\n\n'.join('  * ' + '\n    '.join(_wrap(i, 72)) for i in items)
    date = email.utils.format_datetime(email.utils.localtime())
    entry = f'{source} ({version}) trixie; urgency=medium\n\n{body}\n\n -- {MAINTAINER}  {date}\n\n'
    open(path, 'w').write(entry + text)
    commit(f'bump version to {version}', ['Makefile', 'debian/changelog'])
    return version


def _wrap(text, width):
    lines, line = [], ''
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f'{line} {word}'.strip()
    return lines + [line]


# ---- GitHub --------------------------------------------------------------------------------

def ensure_label():
    gh('label', 'create', LABEL, '--color', 'c5def5', '--description', 'upstream changes to look at',
       '--force')


def find_issue(title):
    issues = json.loads(gh('issue', 'list', '--state', 'open', '--label', LABEL,
                           '--json', 'number,title,body', '--limit', '100'))
    return next((i for i in issues if i['title'].startswith(title)), None)


def upsert_issue(prefix, title, body):
    issue = find_issue(prefix)
    if DRY_RUN:
        print(f'[dry-run] issue: {title}\n{body}')
        return
    if issue is None:
        gh('issue', 'create', '--title', title, '--body', body, '--label', LABEL)
    elif issue['title'] != title or issue['body'].strip() != body.strip():
        gh('issue', 'edit', str(issue['number']), '--title', title, '--body', body)


def close_issue(prefix, comment):
    issue = find_issue(prefix)
    if issue and not DRY_RUN:
        gh('issue', 'close', str(issue['number']), '--comment', comment)


def open_pr(head):
    prs = json.loads(gh('pr', 'list', '--state', 'open', '--head', head,
                        '--json', 'number,body', '--limit', '1'))
    return prs[0] if prs else None


def dispatch(branch):
    for wf in ('check.yml', 'build.yml'):
        gh('workflow', 'run', wf, '--ref', branch)


# ---- main ----------------------------------------------------------------------------------

def check_new_series(cur, releases):
    prefix = 'New stable kernel series'
    newer = [r['version'] for r in releases if r['moniker'] == 'stable'
             and vtuple(r['version'])[:2] > (cur['maj'], cur['min'])]
    series = {'.'.join(v.split('.')[:2]) for v in newer}
    cs = f"{cur['maj']}.{cur['min']}"
    current_listed = any(r['version'] == cs or r['version'].startswith(cs + '.') for r in releases)
    if series:
        s = max(series, key=vtuple)
        eol = '' if current_listed else (f"\n\n{cur['maj']}.{cur['min']} is no longer listed on "
                                         'kernel.org and gets no more stable updates.')
        upsert_issue(prefix, f'{prefix}: Linux {s}',
                     f'Linux {s} is available as a stable release; this branch is on '
                     f"{cur['maj']}.{cur['min']}.{eol}\n\nMoving to it needs a rebase of the "
                     'patch queue, the config policy and the ZFS compatibility, so it is not '
                     'done automatically.')
    else:
        close_issue(prefix, 'No newer stable series than the current one.')


def check_pve_kernel(cur, work, tarball):
    prefix = 'Proxmox pve-kernel'
    git('fetch', '-q', PVE_KERNEL_REPO, 'master')
    new = git('rev-list', '--reverse', 'HEAD..FETCH_HEAD').split()
    if not new:
        close_issue(prefix, 'All Proxmox pve-kernel commits are merged.')
        return
    tip = git('rev-parse', 'FETCH_HEAD')
    base = git('merge-base', 'HEAD', 'FETCH_HEAD')
    log = git('log', '--reverse', '--format=- `%h` %s', 'HEAD..FETCH_HEAD')
    changed = [p for p in git('diff', '--name-only', '--diff-filter=AM', base, tip,
                              '--', 'patches/kernel').split() if p]
    triage = 'No new or changed patches.'
    if changed:
        pdir = os.path.join(work, 'pve-patches')
        os.makedirs(pdir, exist_ok=True)
        files = []
        for p in changed:
            dest = os.path.join(pdir, os.path.basename(p))
            with open(dest, 'w') as f:
                f.write(git('show', f'{tip}:{p}') + '\n')
            files.append(dest)
        report = os.path.join(work, 'triage.md')
        run(sys.executable, os.path.join(TOP, 'ci', 'patchq.py'), 'triage', '--tarball', tarball,
            '--report', report, *files)
        triage = open(report).read() + (
            '\n`applied`: already in the kernel or the queue, nothing to do. `ok`/`fuzz`: '
            'candidates to take, check whether they are specific to the Ubuntu base. '
            '`conflict`: needs a manual port or is not applicable.')
    body = (f'{len(new)} commit(s) of [pve-kernel]({PVE_KERNEL_REPO}) up to `{tip[:12]}` are '
            f'not merged into this branch:\n\n{log}\n\n{triage}\n\nMerge them with:\n\n'
            f'```\ngit fetch {PVE_KERNEL_REPO} master\ngit merge FETCH_HEAD\n```\n'
            'keeping the edge Makefile, rules and patch queue, as in 06f310a.')
    upsert_issue(prefix, f'{prefix}: {len(new)} unmerged commits', body)


def main():
    global DRY_RUN
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--dry-run', action='store_true')
    DRY_RUN = ap.parse_args().dry_run

    branch = git('rev-parse', '--abbrev-ref', 'HEAD')
    head_branch = f'ci/updates/{branch}'
    git('config', 'user.name', MAINTAINER.split(' <')[0])
    git('config', 'user.email', MAINTAINER.split('<')[1].rstrip('>'))
    if not DRY_RUN:
        ensure_label()

    cur = current()
    releases = kernel_releases()
    found = {
        'linux': find_linux(cur, releases),
        'hardened': find_hardened(cur),
        'arch': find_arch_config(),
        'zfs': find_zfs(cur),
    }
    print('current:', cur)
    print('updates:', found)
    work = tempfile.mkdtemp(prefix='watch-')
    try:
        check_new_series(cur, releases)

        marker = '<!-- updates: ' + ' '.join(f'{k}={v}' for k, v in found.items() if v) + ' -->'
        pr = open_pr(head_branch)
        if not any(found.values()):
            print('no updates')
        elif pr and marker in pr['body']:
            print(f"PR #{pr['number']} is up to date")
        elif DRY_RUN:
            print(f'[dry-run] would open a PR for {marker}')
        else:
            git('checkout', '-q', '-B', head_branch, branch)
            items, notes = [], []
            if found['linux']:
                ver, dropped = apply_linux(cur, found['linux'], work)
                items.append(f'update to Linux {ver}.')
                if dropped:
                    items.append('drop patches included in Linux ' + ver + ': ' +
                                 ', '.join(os.path.basename(p) for p in dropped) + '.')
                    notes.append('Dropped, as they apply in reverse to the new release:\n' +
                                 '\n'.join(f'- `{os.path.basename(p)}`' for p in dropped))
            if found['hardened']:
                apply_hardened(cur, found['hardened'], work)
                items.append(f"update linux-hardened to {found['hardened']}.")
            if found['arch']:
                ident = apply_arch_config(found['arch'], work)
                items.append(f'update the Arch Linux linux-hardened config to {ident}.')
            if found['zfs']:
                log = apply_zfs(cur, found['zfs'], work)
                items.append(f"update ZFS to Proxmox zfsonlinux {found['zfs'][:12]}.")
                notes.append(f'ZFS changes:\n```\n{log}\n```')
            version = bump_version(cur, items, bool(found['linux']))
            git('push', '-q', '--force', 'origin', head_branch)

            body = (f'Upstream updates for `{branch}`, generated by `ci/watch.py`.\n\n' +
                    '\n'.join(f'- {i}' for i in items) + '\n\n' + '\n\n'.join(notes) +
                    '\n\nThe check and build workflows run on this branch. Fixes can be pushed to it, '
                    'but the branch is regenerated, dropping them, when further upstream '
                    'updates appear before it is merged.\n\n' + marker)
            title = f'Update to {version}'
            if pr:
                gh('pr', 'edit', str(pr['number']), '--title', title, '--body', body)
            else:
                gh('pr', 'create', '--base', branch, '--head', head_branch, '--title', title,
                   '--body', body, '--label', LABEL)
            dispatch(head_branch)
            git('checkout', '-q', branch)

        tarball = patchq.default_tarball()
        if not os.path.exists(tarball):
            v = patchq.kernel_version()
            fetch(f"{KERNEL_ORG}/v{cur['maj']}.x/linux-{v}.tar.xz", tarball)
            run('sha256sum', '-c', os.path.basename(tarball) + '.sha256', cwd=os.path.dirname(tarball))
        check_pve_kernel(cur, work, tarball)
    finally:
        shutil.rmtree(work)


if __name__ == '__main__':
    main()
