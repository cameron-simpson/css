#!/bin/sh
cd $HOME/rc/mail || exit 1
case $# in
  0)	cvsedit "cats.$SYSTEMID" ;;
  1)	$EDITOR "cats/$1" ;;
  *)	echo "Usage: $0 [subfile]" >&2; exit 1;;
esac
touch cats.*
exec myke _all
