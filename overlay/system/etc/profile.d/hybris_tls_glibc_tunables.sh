#!/bin/sh
# Static TLS for libhybris and the Android 16 libraries, which use TLSDESC.
# Login shells only; system and user services get it from
# /etc/systemd/{system,user}.conf.d/90-hybris-tls.conf.
case "$GLIBC_TUNABLES" in
    *glibc.rtld.optional_static_tls=*) ;;
    "") export GLIBC_TUNABLES=glibc.rtld.optional_static_tls=4096 ;;
    *) export GLIBC_TUNABLES="$GLIBC_TUNABLES:glibc.rtld.optional_static_tls=4096" ;;
esac
