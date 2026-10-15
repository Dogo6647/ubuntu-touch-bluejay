#!/bin/sh

# Doing a part of Android's first stage init. They have to be done in the mount
# hook or mounting things can clober what we're doing.

mknod -m 666 "${LXC_ROOTFS_MOUNT}/dev/null" c 1 3
mknod -m 600 "${LXC_ROOTFS_MOUNT}/dev/kmsg" c 1 11
mknod -m 666 "${LXC_ROOTFS_MOUNT}/dev/random" c 1 8
mknod -m 666 "${LXC_ROOTFS_MOUNT}/dev/urandom" c 1 9

# bluejay: Trusty, the TEE behind KeyMint, Gatekeeper and metricsd, is reached
# through /dev/trusty-ipc-dev0, but the container's ueventd never creates it
# ("tipc_connect: cannot open tipc device /dev/trusty-ipc-dev0"). Create it,
# and the Trusty log, with the owners and modes of the stock ueventd.rc
# (system=1000, drmrpc=1026). trusty-ipc-dev0 has a dynamic major, so read the
# numbers from sysfs; skip a node the kernel doesn't have.
trusty_node() {
    [ -r "/sys/class/$1/$2/dev" ] || return 0
    devnum=$(cat "/sys/class/$1/$2/dev")
    mknod -m 660 "${LXC_ROOTFS_MOUNT}/dev/$2" c "${devnum%%:*}" "${devnum#*:}" &&
        chown "$3" "${LXC_ROOTFS_MOUNT}/dev/$2"
}
trusty_node trusty_ipc trusty-ipc-dev0 1000:1026
trusty_node misc trusty-log0 1000:1000

# Don't expose the raw commandline to unprivileged processes.
chmod 440 "${LXC_ROOTFS_MOUNT}/proc/cmdline"

# bluejay: /proc/sysrq-trigger is not namespaced. Android init's shutdown path
# (reboot_on_failure of apexd/vold, critical services) writes to it, and
# sysrq e/i signal every process on the HOST, not just the container's
# (first boot: journald restarted and lxc-android-ready got SIGTERM at 39.7 s).
# kernel.sysrq=0 does not help: it only gates the keyboard, and the GSI's
# init.rc already writes 0 there. Give the container a dummy file instead.
# Not /dev/null: init.rc chowns/chmods /proc/sysrq-trigger (0220 root:system),
# which would change the host's /dev/null.
: > "${LXC_ROOTFS_MOUNT}/dev/.halium-sysrq-trigger"
mount --bind "${LXC_ROOTFS_MOUNT}/dev/.halium-sysrq-trigger" \
    "${LXC_ROOTFS_MOUNT}/proc/sysrq-trigger" || true

# Here we're doing the opposite of Android 7's init, but apparently
# Android 9 doesn't have /socket anymore, so save a bind mount.
if [ -w "${LXC_ROOTFS_MOUNT}" ]; then
    ln -s /dev/socket "${LXC_ROOTFS_MOUNT}/socket"
fi
