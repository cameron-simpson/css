#!/bin/sh

case $1 in
    -n)		;;
    //*)	set x -n "$@"; shift ;;
    *)		i=$1; shift; set x -n "//$i" ${1+"$@"}; shift ;;
esac

exec dspst ${1+"$@"}
