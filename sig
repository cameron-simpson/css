#!/bin/sh -u

: ${SIGNATURE:=$HOME/rc/mail/signature@$SYSTEMID}

cat "$SIGNATURE"
echo
exec picksig ${1+"$@"}
