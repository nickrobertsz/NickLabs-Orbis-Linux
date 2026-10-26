#!/bin/sh
# Exercise the Orbis-patched root selection in GayCorp init with stubbed devices.
# Usage: root-selection.sh /path/to/patched/initramfs-ps4 [workdir]
# Used by build_initramfs.py after patching; exits non-zero on a wrong decision.
SRC="$1"
H="${2:-$(mktemp -d)}"
mkdir -p "$H/bin"
cat > "$H/bin/findfs" <<'EOF'
#!/bin/sh
c=$(cat "$STATE" 2>/dev/null || echo 0); echo $((c+1)) > "$STATE"
[ "$1" = "LABEL=ORBIS-ROOT" ] && [ "$c" -ge "${APPEAR_AFTER:-0}" ] && echo /dev/sdb2 && exit 0
[ "$1" = "LABEL=psxitarch" ] && [ -n "$HAS_PSXITARCH" ] && echo /dev/sdc1 && exit 0
exit 1
EOF
printf '#!/bin/sh\n:\n' > "$H/bin/sleep"
chmod +x "$H/bin/findfs" "$H/bin/sleep"
# the two patched blocks of init, verbatim
sed -n '/^# NickLabs Orbis: with root=/,/^echo$/p' "$SRC/init" > "$H/block.sh"
sed -n '/^# NickLabs Orbis: an explicit/,/^	ps4_find_install$/p' "$SRC/init" >> "$H/block.sh"
echo fi >> "$H/block.sh"
sed -n '/^orbis_root_dev()/,/^ps4_find_install() {/p' "$SRC/functions.sh" | sed '$d' > "$H/funcs.sh"

fail=0
check() { # name cmdline appear_after has_psxitarch expected
	printf 'pci=nocrs %s rw\n' "$2" > "$H/cmdline"
	: > "$H/state"
	out=$(STATE="$H/state" APPEAR_AFTER="$3" HAS_PSXITARCH="$4" PATH="$H/bin:$PATH" H="$H" sh -c '
		root=""; for i in $(cat "$H/cmdline"); do case $i in root=*) root=${i#root=};; esac; done
		eerror(){ echo "EERROR $*"; }; ewarn(){ :; }; einfo(){ :; }
		use(){ n="$(eval echo \$$1)"; [ -n "$n" ]; }
		ps4_fsck(){ echo "fsck $1"; }; ps4_wait_disks(){ echo "wait-psxitarch"; }
		ps4_is_southbridge(){ return 1; }
		ps4hdd_open(){ echo "OPEN-INTERNAL-DRIVE"; }; run_hooks(){ echo "hooks-$1"; }
		emount(){ echo "mount root=$root"; }; ps4_find_install(){ echo "discovery"; }
		. "$H/funcs.sh"
		orbis_cmdline(){ grep -qw -- "$1" "$H/cmdline"; }
		. "$H/block.sh"' 2>&1 | grep -v '^$' | tr '\n' ';')
	if [ "$out" = "$5" ]; then r=ok; else r=FAIL; fail=1; fi
	printf '%-4s %-40s => %s\n' "$r" "$1" "$out"
}
check "root= set, appears after 3 polls" "root=LABEL=ORBIS-ROOT" 3 "" \
	"fsck /dev/sdb2;mount root=LABEL=ORBIS-ROOT;"
check "root= set, old psxitarch disk present" "root=LABEL=ORBIS-ROOT" 0 1 \
	"fsck /dev/sdb2;mount root=LABEL=ORBIS-ROOT;"
check "root= set, never appears" "root=LABEL=ORBIS-ROOT" 99 "" \
	"EERROR root=LABEL=ORBIS-ROOT did not appear within 30s.;mount root=LABEL=ORBIS-ROOT;"
check "no root=, psxitarch present (compat)" "" 0 1 \
	"wait-psxitarch;OPEN-INTERNAL-DRIVE;hooks-ps4hdd_open;discovery;"
check "root= + orbis.ps4hdd=1" "root=LABEL=ORBIS-ROOT orbis.ps4hdd=1" 0 "" \
	"fsck /dev/sdb2;OPEN-INTERNAL-DRIVE;hooks-ps4hdd_open;mount root=LABEL=ORBIS-ROOT;"
exit $fail
