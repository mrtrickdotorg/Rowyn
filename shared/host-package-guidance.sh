#!/usr/bin/env bash
# Compose installed-host package-manager guidance without removing apt or dpkg.
#
# The helper is intended to run after image construction has completed. It puts
# wrappers in /usr/sbin, leaving Ubuntu's real binaries in /usr/bin so image
# builds and explicit absolute paths remain usable. Distrobox has its own
# filesystem and package manager; this helper never edits a user's container.
set -euo pipefail

root=/
while (($#)); do
    case "$1" in
        --root)
            root=${2:?--root requires a path}
            shift 2
            ;;
        --help|-h)
            printf 'usage: %s [--root PATH]\n' "$0"
            exit 0
            ;;
        *)
            printf 'usage: %s [--root PATH]\n' "$0" >&2
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

write_if_changed() {
    local destination=$1
    local mode=$2
    local temporary
    temporary=$(mktemp "${destination}.tmp.XXXXXX")
    cat >"$temporary"
    chmod "$mode" "$temporary"
    if [[ ! -e "$destination" ]] || ! cmp -s "$temporary" "$destination"; then
        mv -f "$temporary" "$destination"
    else
        rm -f "$temporary"
        chmod "$mode" "$destination"
    fi
}

write_wrapper() {
    local destination=$1
    local real=$2
    local kind=$3
    local real_quoted
    real_quoted=$(printf '%q' "$real")

    if [[ "$kind" == apt ]]; then
        write_if_changed "$destination" 0755 <<EOF
#!/usr/bin/env bash
set -u
real=$real_quoted

apt_mutation() {
    local arg command= skip=0
    for arg in "\$@"; do
        if ((skip)); then
            skip=0
            continue
        fi
        case "\$arg" in
            --)
                continue
                ;;
            -c|--config-file|-o|--option|-t|--target-release|-a|--host-architecture)
                skip=1
                ;;
            -c?*|-o?*|-t?*|-a?*)
                ;;
            --config-file=*|--option=*|--target-release=*|--host-architecture=*)
                ;;
            --*)
                ;;
            -*)
                ;;
            *)
                command="\$arg"
                break
                ;;
        esac
    done
    case "\$command" in
        update|install|reinstall|remove|purge|autoremove|upgrade|full-upgrade|dist-upgrade|dselect-upgrade|build-dep|satisfy|clean|autoclean|autopurge|mark)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

if apt_mutation "\$@"; then
    cat >&2 <<'GUIDANCE'
Rowyn's installed host is an immutable system image; this package change was not run.

For graphical applications, open Discover and install the Flatpak app from Flathub.
For command-line or development packages, use an Ubuntu Distrobox instead:
  distrobox create --name ubuntu-dev --image docker.io/library/ubuntu:26.10
  distrobox enter ubuntu-dev
  sudo apt-get update
  sudo apt-get install <package>

Use Rowyn system image updates for changes to the base operating system.
Read-only package queries remain available on the host.
GUIDANCE
    exit 1
fi

exec "\$real" "\$@"
EOF
    else
        write_if_changed "$destination" 0755 <<EOF
#!/usr/bin/env bash
set -u
real=$real_quoted

dpkg_mutation() {
    local arg short char operation
    for arg in "\$@"; do
        case "\$arg" in
            --install|--unpack|--configure|--triggers-only|--remove|--purge|--set-selections|--clear-selections|--add-architecture|--remove-architecture|--update-avail|--merge-avail|--clear-avail|--forget-old-unavail)
                return 0
                ;;
            --install=*|--unpack=*|--configure=*|--triggers-only=*|--remove=*|--purge=*|--set-selections=*|--add-architecture=*|--remove-architecture=*|--update-avail=*|--merge-avail=*)
                return 0
                ;;
            --)
                break
                ;;
            --*)
                ;;
            -?*)
                short="\${arg#-}"
                for ((operation=0; operation<\${#short}; operation++)); do
                    char="\${short:operation:1}"
                    case "\$char" in
                        i|u|r|P|A)
                            return 0
                            ;;
                    esac
                done
                ;;
        esac
    done
    return 1
}

if dpkg_mutation "\$@"; then
    cat >&2 <<'GUIDANCE'
Rowyn's installed host is an immutable system image; this package change was not run.

Installing a .deb or changing dpkg's package state on the host is not how .debs are installed on Rowyn: open the .deb from your file manager (or the Hub's Apps page) and Rowyn installs supported app .debs into its dedicated app container instead; system packages are refused there with the reason.
For graphical applications, open Discover and install the Flatpak app from Flathub.
For command-line or development packages, use an Ubuntu Distrobox instead:
  distrobox create --name ubuntu-dev --image docker.io/library/ubuntu:26.10
  distrobox enter ubuntu-dev
  sudo apt-get update
  sudo apt-get install <package>

Use Rowyn system image updates for changes to the base operating system.
Read-only dpkg and dpkg-query operations remain available on the host.
GUIDANCE
    exit 1
fi

exec "\$real" "\$@"
EOF
    fi
}

bin_dir=$(root_path /usr/sbin)
doc_dir=$(root_path /usr/share/doc/rowyn)
install -d "$bin_dir" "$doc_dir"

for command in apt apt-get dpkg; do
    real=$(root_path "/usr/bin/$command")
    [[ -x "$real" ]] || {
        printf 'missing executable package-manager binary: %s\n' "$real" >&2
        exit 1
    }
    kind=apt
    [[ "$command" == dpkg ]] && kind=dpkg
    write_wrapper "$bin_dir/$command" "$real" "$kind"
done

write_if_changed "$(root_path /usr/share/doc/rowyn/host-package-guidance.txt)" 0644 <<'EOF'
Rowyn installed-host package guidance
=====================================

The installed Rowyn host is an immutable system image. The composed /usr/sbin
apt, apt-get, and dpkg wrappers reject package-changing commands with exit
status 1.
The original Ubuntu binaries remain in /usr/bin, and read-only dpkg-query and
read-only dpkg operations continue to delegate to them.
This is convenience guidance for the installed host, not a security boundary.

Covered apt/apt-get mutating commands:
  update, install, reinstall, remove, purge, autoremove, upgrade,
  full-upgrade, dist-upgrade, dselect-upgrade, build-dep, satisfy, clean,
  autoclean, autopurge, mark

Covered dpkg mutating operations:
  -i/--install, -u/--unpack, --configure, --triggers-only,
  -r/--remove, -P/--purge, --set-selections, --clear-selections,
  --add-architecture, --remove-architecture, --update-avail,
  --merge-avail, --clear-avail, --forget-old-unavail

For graphical applications, use Discover and install a Flatpak from Flathub.
A downloaded .deb is installed through the rowyn-deb-handler / Hub Apps page
flow: app .debs go into the dedicated rowyn-deb Distrobox, system packages
are refused with the reason. Host dpkg state is never changed by that flow.
For command-line or development packages, create or enter the Ubuntu Distrobox
chosen by the user, then run apt-get inside that independent container. This
composition helper does not edit the container or the user's Distrobox choice.
Use Rowyn system image updates for changes to the base operating system.
EOF
