#!/usr/bin/env bash
# Create (once) the local NickLabs Orbis package-signing key. It never leaves your build
# machine: the private key stays in out/keys/gnupg (git-ignored), and only the public key is
# copied into images so the console's pacman can verify [orbis] packages (SigLevel = Required).
#
#   packaging/scripts/orbis-repo-key.sh            # create if missing, print fingerprint
#   packaging/scripts/orbis-repo-key.sh --show     # print fingerprint only
#
# Back up out/keys/gnupg: without it, future packages cannot be signed with the same key and
# consoles need the new public key (orbis-update --import-key FILE).
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
keys="${ORBIS_KEYS_DIR:-$repo_root/out/keys}"
export GNUPGHOME="$keys/gnupg"
uid="NickLabs Orbis local package signing <orbis-repo@nicklabs.invalid>"
mkdir -p "$GNUPGHOME"; chmod 700 "$GNUPGHOME"
fpr() { gpg --batch --with-colons --list-secret-keys "$uid" 2>/dev/null | awk -F: '/^fpr:/ {print $10; exit}'; }
if [ "${1:-}" != "--show" ] && [ -z "$(fpr)" ]; then
  command -v gpg >/dev/null || { echo "gpg not installed (sudo dnf install gnupg2)" >&2; exit 2; }
  gpg --batch --pinentry-mode loopback --passphrase '' --quick-generate-key "$uid" ed25519 sign never
fi
f="$(fpr)"
[ -n "$f" ] || { echo "no Orbis signing key in $GNUPGHOME" >&2; exit 1; }
gpg --batch --armor --export "$f" > "$keys/orbis-repo-key.asc"
echo "$f" > "$keys/orbis-repo-key.fingerprint"
echo "Orbis signing key: $f"
echo "public key: $keys/orbis-repo-key.asc (copied into images; the private key stays in $GNUPGHOME)"
