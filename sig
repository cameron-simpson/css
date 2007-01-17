#!/bin/sh -u

: ${SIGNATURE:=$HOME/rc/mail/signature.$SYSTEMID}

echo "-- "
cat "$SIGNATURE"
echo
exec picksig ${1+"$@"}
