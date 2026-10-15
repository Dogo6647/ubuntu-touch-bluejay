#!/bin/sh
# [TEMP] mapper.pixel.so (stable-C IMapper 5) lives in /vendor/lib64/hw.
case "$HYBRIS_LD_LIBRARY_PATH" in
    */vendor/lib64/hw*) return ;;
esac
export HYBRIS_LD_LIBRARY_PATH=/vendor/lib64/hw
