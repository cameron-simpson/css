#!/bin/sh
cd $HOME/rc/mail || exit 1
id=${1:-$SYSTEMID}
cvsedit "cats.$id" && myke
