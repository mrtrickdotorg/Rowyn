#!/usr/bin/env bash
# Install the image-owned containers/image policy used by bootc update fetches.
#
# This is a system policy in /etc, so it is part of the immutable image and is
# available to root and rootless users that do not provide a user policy. The
# default is fail-closed. Docker Hub and GHCR are explicitly allowed for normal
# Ubuntu and unrelated GHCR images; the more-specific Rowyn repository scopes
# override the broader GHCR allowance and require the Rowyn signature key.
set -euo pipefail

root=/
while (($#)); do
    case "$1" in
        --root)
            root=${2:?--root requires a path}
            shift 2
            ;;
        *)
            echo "usage: $0 [--root PATH]" >&2
            exit 2
            ;;
    esac
done

if [[ "$root" != / ]]; then
    root="${root%/}"
fi
root_path() {
    if [[ "$1" == /* ]]; then
        printf '%s%s' "$root" "$1"
    else
        printf '%s/%s' "$root" "$1"
    fi
}

policy=$(root_path /etc/containers/policy.json)
registries=$(root_path /etc/containers/registries.d/rowyn-update.yaml)

install -d "$(dirname "$policy")" "$(dirname "$registries")"

# bootc's enforced ContainerPolicy rejects a default insecureAcceptAnything
# policy. Keep the default strict while allowing ordinary Docker Hub and GHCR
# use. containers/image chooses the longest matching repository scope, so the
# Rowyn entry below wins over the broader ghcr.io allowance.
cat > "$policy" <<'EOF'
{
  "default": [
    { "type": "reject" }
  ],
  "transports": {
    "docker": {
      "docker.io": [
        { "type": "insecureAcceptAnything" }
      ],
      "ghcr.io": [
        { "type": "insecureAcceptAnything" }
      ],
      "ghcr.io/mrtrickdotorg/rowyn": [
        {
          "type": "sigstoreSigned",
          "keyPath": "/usr/share/rowyn/update.pub",
          "signedIdentity": {
            "type": "exactRepository",
            "dockerRepository": "ghcr.io/mrtrickdotorg/rowyn"
          }
        }
      ]
    }
  }
}
EOF
chmod 0644 "$policy"

cat > "$registries" <<'EOF'
docker:
  ghcr.io/mrtrickdotorg/rowyn:
    use-sigstore-attachments: true
EOF
chmod 0644 "$registries"
