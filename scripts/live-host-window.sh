#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Temporary host preparation only; never invoked by the HTTP API.
set -Eeuo pipefail
umask 077
[[ $EUID == 0 && -n ${SUDO_USER:-} && $SUDO_USER != root ]] || exit 2
[[ $# == 2 ]] || { echo "usage: sudo $0 PRIVATE_STATE_DIR SHARED_DEVICE_DIR" >&2; exit 2; }
base=$(realpath -e "$1")
device_dir=$(realpath -e "$2")
[[ $(stat -c %u "$base") == "$SUDO_UID" && $(stat -c %a "$base") == 700 ]] || exit 2
[[ -f $device_dir/.device.lock ]] || exit 2
exec 8>"$device_dir/.device.lock"
flock -n 8 || exit 2
[[ -d $base && ! -e $base/host-ready && ! -e $base/host-stop ]] || exit 2
iface=''
boards=0
shopt -s nullglob
for d in /sys/bus/usb/devices/*; do
 [[ -f $d/idVendor && -f $d/idProduct ]] || continue
 [[ $(<"$d/idVendor") == 0456 && $(<"$d/idProduct") == b673 ]] || continue
 ((boards+=1))
 for n in "$d":*/net/*; do [[ -z $iface ]] || exit 2; iface=${n##*/}; done
done
[[ $boards == 1 && -n $iface ]] || exit 2
[[ -z $(ip -4 -o addr show dev "$iface") && $(<"/sys/class/net/$iface/flags") == 0x1002 ]] || exit 2
[[ ! -e $device_dir/recovery-required.json ]] || exit 2
pcsc_socket=$(systemctl is-active pcscd.socket || true)
pcsc_service=$(systemctl is-active pcscd.service || true)
[[ $pcsc_service == inactive && $pcsc_socket == active ]] || exit 2
configured=0
pcsc_changed=0
pcsc_pid=''
cleanup() {
 status=$?
 trap - EXIT TERM INT
 set +e
 # Hold the shared lock while removing the route. Let an owned finite RX
 # finish first; if it cannot be reaped, preserve connectivity for recovery.
 if ! flock -w 650 8; then
   echo "cleanup blocked: RX still owns the shared lock; inspect before removing route" >&2
   printf 'status=1 cleanup=blocked_by_rx\n' > "$base/host-cleanup"
   chown "$SUDO_UID:$SUDO_GID" "$base/host-cleanup"
   exit 1
 fi
 if [[ -n $pcsc_pid ]]; then kill "$pcsc_pid" 2>/dev/null || true; wait "$pcsc_pid" 2>/dev/null || true; fi
 pcsc_rc=0; address_rc=0; link_rc=0
 if [[ $pcsc_changed == 1 ]]; then systemctl start pcscd.socket; pcsc_rc=$?; fi
 if [[ $configured == 1 ]]; then
   ip -4 addr del 192.168.2.10/24 dev "$iface"; address_rc=$?
   ip link set "$iface" down; link_rc=$?
 fi
 if ((pcsc_rc || address_rc || link_rc)); then status=1; fi
 printf 'status=%s pcsc_restore_rc=%s address_restore_rc=%s link_restore_rc=%s pcsc_socket=%s\n' \
   "$status" "$pcsc_rc" "$address_rc" "$link_rc" "$(systemctl is-active pcscd.socket)" > "$base/host-cleanup"
 chown "$SUDO_UID:$SUDO_GID" "$base/host-cleanup"
 exit "$status"
}
trap cleanup EXIT
trap 'exit 143' TERM INT
ip -4 addr add 192.168.2.10/24 dev "$iface"
configured=1
ip link set "$iface" up
pcsc_changed=1
systemctl stop pcscd.socket pcscd.service
# Private directory controls access; no permanent policy or permission change.
install -d -m 700 -o "$SUDO_USER" -g "$(id -gn "$SUDO_USER")" "$base/pcsc"
(umask 000; exec timeout --signal=TERM --kill-after=3s 3600s systemd-socket-activate -l "$base/pcsc/pcscd.comm" /usr/sbin/pcscd --foreground --disable-polkit --reader-name-no-serial --reader-name-no-interface) > /dev/null 2>&1 &
pcsc_pid=$!
for i in {1..50}; do [[ ! -S $base/pcsc/pcscd.comm ]] || break; sleep .1; done
[[ -S $base/pcsc/pcscd.comm ]] || exit 1
chown "$SUDO_USER:$(id -gn "$SUDO_USER")" "$base/pcsc/pcscd.comm"
chmod 600 "$base/pcsc/pcscd.comm"
flock -u 8
printf 'ready\n' > "$base/host-ready"
chown "$SUDO_UID:$SUDO_GID" "$base/host-ready"
for i in {1..3400}; do [[ ! -e $base/host-stop ]] || exit 0; kill -0 "$pcsc_pid"; sleep 1; done
