#!/bin/sh
# Static TLS for the ELF-TLS-capable libhybris (Android 16 blobs use TLSDESC).
case "$GLIBC_TUNABLES" in
    *glibc.rtld.optional_static_tls=4096*) return ;;
esac
export GLIBC_TUNABLES=glibc.rtld.optional_static_tls=4096
