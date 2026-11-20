#!/usr/bin/env bash
# Regenerate the offline containers/image fixtures used by
# build/tests/test_update_policy.py.
#
# `image` is an unsigned copy of a tiny public image in the `dir:` transport
# format. `signed-image` is the same image plus the sigstore attachment
# (`signature-1`) that `test_signed_fixture_requires_the_matching_signing_key`
# verifies. The keys are throwaway fixture keys: they are regenerated here and
# never used by Rowyn itself, so only the public halves are committed.
#
# The signature is an ECDSA signature over the image manifest digest, so
# re-running this script changes `signature-1` bytes but not the test outcome.
# Run it only when the fixture needs to move, then run
# `python3 -B -m unittest test_update_policy` from build/tests.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

# hello-world, linux/amd64 manifest, pinned by digest so the fixture is
# reproducible instead of tracking a moving tag.
manifest="sha256:d1a8d0a4eeb63aff09f5f34d4d80505e0ba81905f36158cc3970d8e07179e59e"
identity="docker.io/library/hello-world:latest"
passphrase="${work}/passphrase"
printf 'rowyn-fixture-key\n' > "${passphrase}"

rm -rf "${here}/image" "${here}/signed-image"

skopeo copy --override-arch amd64 --override-os linux \
    "docker://docker.io/library/hello-world@${manifest}" "dir:${here}/image"

skopeo generate-sigstore-key --output-prefix "${work}/signing" \
    --passphrase-file "${passphrase}"
skopeo generate-sigstore-key --output-prefix "${work}/other" \
    --passphrase-file "${passphrase}"

skopeo copy --sign-by-sigstore-private-key "${work}/signing.private" \
    --sign-passphrase-file "${passphrase}" --sign-identity "${identity}" \
    "dir:${here}/image" "dir:${here}/signed-image"

install -m 0644 "${work}/signing.pub" "${here}/signing-key.pub"
install -m 0644 "${work}/other.pub" "${here}/other-key.pub"
