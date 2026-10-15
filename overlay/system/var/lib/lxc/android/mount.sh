#!/bin/sh

# Doing a part of Android's first stage init. They have to be done in the mount
# hook or mounting things can clober what we're doing.

mknod -m 666 "${LXC_ROOTFS_MOUNT}/dev/null" c 1 3
mknod -m 600 "${LXC_ROOTFS_MOUNT}/dev/kmsg" c 1 11
mknod -m 666 "${LXC_ROOTFS_MOUNT}/dev/random" c 1 8
mknod -m 666 "${LXC_ROOTFS_MOUNT}/dev/urandom" c 1 9

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
