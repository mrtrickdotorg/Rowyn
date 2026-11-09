#!/usr/bin/env bash
# Make Brave the default web browser for new Rowyn sessions.
#
# Brave is the OS-vendor preinstalled Flatpak (shared/brave.preinstall);
# these are *defaults* only: /etc/xdg/mimeapps.list is the system-wide
# default-association file and the /etc/skel copy seeds the account the
# installer creates. A user's own ~/.config/mimeapps.list always wins,
# and nothing here touches an existing user's file.
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

mimeapps=$(cat <<'EOF'
[Default Applications]
x-scheme-handler/http=com.brave.Browser.desktop
x-scheme-handler/https=com.brave.Browser.desktop
text/html=com.brave.Browser.desktop
application/xhtml+xml=com.brave.Browser.desktop
EOF
)

install -d "$(root_path /etc/xdg)" "$(root_path /etc/skel/.config)"
printf '%s\n' "$mimeapps" > "$(root_path /etc/xdg/mimeapps.list)"
chmod 0644 "$(root_path /etc/xdg/mimeapps.list)"
printf '%s\n' "$mimeapps" > "$(root_path /etc/skel/.config/mimeapps.list)"
chmod 0644 "$(root_path /etc/skel/.config/mimeapps.list)"
