#!/bin/sh

SIGNATURE=${SIGNATURE:-$HOME/.signature}

cat "$SIGNATURE"
echo
exec picksig ${1+"$@"}
