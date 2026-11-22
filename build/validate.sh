#!/usr/bin/env bash
# Validate Rowyn's source and CI contracts before an image build starts.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "${repo_root}"

fail() {
    echo "[validate] ERROR: $*" >&2
    exit 1
}

scripts=(build/*.sh shared/*.sh)
(( ${#scripts[@]} > 0 )) || fail "no shell scripts found"

for script in "${scripts[@]}"; do
    test -x "${script}" || fail "script is not executable: ${script}"
    bash -n "${script}"
done

echo "[validate] shell syntax passed"

command -v shellcheck >/dev/null 2>&1 || fail "shellcheck is required"

# The update-policy signature tests read checked-in dir: fixtures, so they need
# no network, but they do need skopeo. Report that here instead of letting the
# suite skip three tests and the skip baseline explain it afterwards.
command -v skopeo >/dev/null 2>&1 \
    || fail "skopeo is required for the offline update-policy signature tests"

# Actionlint resolves job needs and expression contexts, which neither the YAML
# parse below nor the string contracts can: a job that reads
# needs.validate.outputs without depending on validate evaluates the expression
# to an empty string, so a release gate silently stops gating.
command -v actionlint >/dev/null 2>&1 \
    || fail "actionlint is required for workflow expression and needs-context checks"

echo "[validate] ShellCheck warnings and errors"
shellcheck --severity=warning "${scripts[@]}"

echo "[validate] ShellCheck passed"

python3 - <<'PY'
from pathlib import Path

import yaml

for workflow in sorted(Path('.github/workflows').glob('*.yml')):
    with workflow.open(encoding='utf-8') as handle:
        yaml.safe_load(handle)
PY
echo "[validate] workflow YAML parsed"

echo "[validate] actionlint"
actionlint
echo "[validate] actionlint passed"

deprecated_tool_cache_count="$( { grep -Fh 'tool-cache:' .github/workflows/{build,installer-iso}.yml || true; } | wc -l)"
(( deprecated_tool_cache_count == 0 )) \
    || fail "workflow still uses deprecated free-disk-space tool-cache input"
preinstalled_runtimes_count="$(grep -Fc 'preinstalled-runtimes: true' .github/workflows/{build,installer-iso}.yml | awk -F: '{sum += $NF} END {print sum}')"
(( preinstalled_runtimes_count == 2 )) \
    || fail "both free-disk-space steps must use preinstalled-runtimes"
echo "[validate] free-disk-space inputs passed"

test -s Containerfile || fail "Containerfile is missing or empty"
test -s os-release || fail "os-release is missing or empty"
test -x build/custom-installer-iso.sh || fail "custom installer ISO script is missing or not executable"
test -s .github/actions/setup-compressed-storage/action.yml \
    || fail "compressed Podman storage action is missing or empty"

grep -Fq 'LABEL containers.bootc 1' Containerfile \
    || fail "Containerfile is missing containers.bootc label"
grep -Fq 'RUN bootc container lint' Containerfile \
    || fail "Containerfile is missing bootc container lint"
python3 - <<'PY'
from pathlib import Path

containerfile = Path('Containerfile').read_text(encoding='utf-8')
kde_packages = containerfile.find('plasma-desktop')
rootfs_normalization = containerfile.find('/ctx/shared/bootc-rootfs.sh')
if kde_packages < 0 or rootfs_normalization < 0 or kde_packages > rootfs_normalization:
    raise SystemExit(
        'KDE packages must be installed before bootc-rootfs.sh removes /var'
    )
PY
echo "[validate] KDE package ordering passed"
grep -Fq 'CARGO_NET_OFFLINE=true' shared/build.sh \
    || fail "bootc source build is not explicitly offline"
grep -Fq 'BOOTC_COMMIT="6d7893f2dbdd289a4a04a5727e4df29433d43024"' build/fetch-assets.sh \
    || fail "bootc asset fetch is missing the reviewed source commit pin"
grep -Fq 'fetch --no-tags --depth 1 origin' build/fetch-assets.sh \
    || fail "bootc asset fetch does not fetch the pinned commit"
grep -Fq 'rev-parse --verify HEAD' build/fetch-assets.sh \
    || fail "bootc asset fetch does not verify the checked-out HEAD"

grep -Fq 'NAME="Rowyn"' os-release || fail "os-release identity is not Rowyn"
grep -Fxq 'ID=rowyn' os-release || fail "os-release ID is not rowyn"
grep -Fxq 'ID_LIKE="ubuntu debian"' os-release \
    || fail "os-release does not identify the Ubuntu/Debian family"

if git ls-files | grep -q '^build-assets/'; then
    fail "host-fetched build-assets/ must never be committed"
fi
git check-ignore -q build-assets/.rowyn-ci-sentinel \
    || fail "build-assets/ is not ignored"

grep -Fq 'needs: validate' .github/workflows/build.yml \
    || fail "image build is not gated on validation"
grep -Fq 'grep -Fqx "NAME=\"Rowyn\"" /usr/lib/os-release' .github/workflows/build.yml \
    || fail "image smoke check has an invalid Rowyn os-release pattern"
grep -Fq 'grep -Fqx "ID_LIKE=\"ubuntu debian\"" /usr/lib/os-release' .github/workflows/build.yml \
    || fail "image smoke check has an invalid Ubuntu family pattern"
grep -Fq -- '--digestfile' .github/workflows/build.yml \
    || fail "image publication does not record an immutable digest"
grep -Fq 'steps.source.outputs.image_digest' .github/workflows/installer-iso.yml \
    || fail "ISO job does not consume the published image digest"
if grep -Fq -- '/var/lib/containers/storage:/var/lib/containers/storage' .github/workflows/installer-iso.yml; then
    fail "custom installer ISO path must not use the obsolete bootc-image-builder storage mount"
fi

if grep -Eq 'bootc-image-builder|anaconda-iso' .github/workflows/installer-iso.yml; then
    fail "Rowyn ISO path must use the custom installer environment, not anaconda-iso"
fi
grep -Fq 'workflows: [Rowyn CI]' .github/workflows/installer-iso.yml \
    || fail "custom installer ISO is not downstream of image workflow"
grep -Fq 'id-token: write' .github/workflows/build.yml \
    || fail "image signing job is missing OIDC permission"
grep -Fq 'cosign sign -y' .github/workflows/build.yml \
    || fail "image signing job does not sign the immutable image"
python3 -B build/ubuntu-only-check.py
for script in build/ubuntu-*.py; do
    python3 -c 'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text())' "$script"
done
grep -Fq 'sha256sum -- "$(basename "${iso}")"' .github/workflows/installer-iso.yml \
    || fail "ISO checksum does not use a portable basename"
grep -Fq 'runroot_source="${IMAGE_PATH}.runroot"' .github/actions/setup-compressed-storage/action.yml \
    || fail "compressed storage runroot source is not on the image's disk filesystem"
grep -Fq 'findmnt -n -o MAJ:MIN' .github/actions/setup-compressed-storage/action.yml \
    || fail "compressed storage action does not verify backing devices"
grep -Fq 'findmnt -n -o FSTYPE' .github/actions/setup-compressed-storage/action.yml \
    || fail "compressed storage action does not verify backing filesystems"
grep -Fq '[[ "${graphroot}" == "${MOUNT_POINT}" ]]' .github/actions/setup-compressed-storage/action.yml \
    || fail "compressed storage action does not verify the configured graphroot"
grep -Fq '[[ "${runroot}" == "${RUNROOT}" ]]' .github/actions/setup-compressed-storage/action.yml \
    || fail "compressed storage action does not verify the configured runroot"
grep -Fq 'cosign sign-blob --yes --bundle' .github/workflows/installer-iso.yml \
    || fail "ISO checksum is not signed"
grep -Fq 'cosign verify-blob' .github/workflows/installer-iso.yml \
    || fail "ISO checksum signature is not verified in CI"
grep -Fq 'rowyn-custom-installer-*.iso.sigstore.json' .github/workflows/installer-iso.yml \
    || fail "ISO signature bundle is not uploaded"
grep -Fq 'skopeo inspect --no-creds' .github/workflows/installer-iso.yml \
    || fail "custom installer does not assert anonymous source-image access"
skip_report="$(mktemp)"
trap 'rm -f "${skip_report}"' EXIT
python3 -B build/tests/rowyn_run_suite.py "${skip_report}"

# A test that stops running must fail validation rather than quietly shrink
# coverage: build/tests/skip-baseline.txt lists every expected skip with the
# reason it must report and the guard that makes it legitimate (an unset
# integration variable, an absent generated tree, a missing tool, non-root).
# A skip with a different reason, a skip whose guard no longer applies, and a
# listed test that does not skip while its guard still holds are all failures.
python3 -B build/tests/skip_baseline.py "${skip_report}" \
    || fail "test skips do not match build/tests/skip-baseline.txt"

echo "[validate] Rowyn source and CI contracts passed"
