#!/bin/sh

cmd=`basename "$0"`
case $cmd in
    dat) TAPE=dat:albeniz ;;
    leo) TAPE=leo:black ;;
    *)	 echo "$cmd: who am I? I only know 'dat' and 'leo'." >&2
	 exit 1
	 ;;
esac
export TAPE
exec rtape ${1+"$@"}
