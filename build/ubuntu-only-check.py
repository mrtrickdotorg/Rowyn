#!/usr/bin/env python3
"""Reject non-Ubuntu OS/build paths before a costly or privileged ISO build."""
from pathlib import Path
import re
import sys
import yaml


def validate(root):
    workflow_text = (root / '.github/workflows/installer-iso.yml').read_text()
    workflow = yaml.safe_load(workflow_text)
    # The digest pin lives only in the Containerfile, and every consumer
    # derives it from there. A copy inside a workflow file would also make
    # the scheduled pin bump unpushable: its GITHUB_TOKEN cannot push
    # workflow-file changes, whatever permissions the workflow declares.
    workflows_dir = root / '.github/workflows'
    for path in sorted(workflows_dir.glob('*.yml')) + sorted(workflows_dir.glob('*.yaml')):
        if re.search(r'docker\.io/library/ubuntu@sha256:', path.read_text()):
            raise ValueError('Ubuntu digest hardcoded in workflow file: ' + path.name)
    if 'UBUNTU_LIVE_IMAGE=$(python3 build/ubuntu-pin-bump.py --pinned)' not in workflow_text:
        raise ValueError('ISO workflow must resolve UBUNTU_LIVE_IMAGE from the Containerfile pin')
    base = None
    for file in ('Containerfile', 'build/ubuntu-live.Containerfile'):
        text = (root / file).read_text()
        for match in re.finditer(r'^FROM\s+(\S+)', text, re.M):
            allowed = {'scratch', 'base', '${UBUNTU_IMAGE}', '${PAYLOAD_IMAGE}'}
            if re.fullmatch(r'docker.io/library/ubuntu@sha256:[0-9a-f]{64}', match[1]):
                if base is None:
                    base = match[1]
                elif match[1] != base:
                    raise ValueError('Ubuntu digest in ' + file + ' differs from the Containerfile pin: ' + match[1])
            elif match[1] not in allowed:
                raise ValueError('non-Ubuntu build stage in ' + file + ': ' + match[1])
    if base is None:
        raise ValueError('no digest-pinned Ubuntu base in the Containerfile')
    for job in workflow['jobs'].values():
        if not job['runs-on'].startswith('ubuntu-'):
            raise ValueError('ISO workflow must run on Ubuntu')
    paths = [root / 'build/custom-installer-iso.sh', root / 'build/configure-live.sh',
             root / 'build/assemble-iso.sh', root / 'build/ubuntu-live.Containerfile',
             root / 'build/ubuntu-live-packages.py']
    text = '\n'.join(path.read_text() for path in paths)
    if re.search(r'\b(dnf5?|rpm(?:-ostree)?|yum)\b|titanoboa|build_files/build-live-iso.sh|bash\s+/?\S*installer/build.sh', text, re.I):
        raise ValueError('non-Ubuntu executable ISO assembly/toolchain path')
    if 'test "$ID" = ubuntu' not in (root / 'build/ubuntu-live.Containerfile').read_text():
        raise ValueError('missing runtime Ubuntu OS identity assertion')
    return base


if __name__ == '__main__':
    try:
        print('Ubuntu-only executable build guards passed: ' + validate(Path(__file__).resolve().parents[1]))
    except (KeyError, ValueError) as error:
        sys.exit(str(error))
