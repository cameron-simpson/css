#!/bin/sh
#

cd $HOME/tmp
PATH=$HOME/public/mosaic/bin:$PATH con -a xmosaic ${1+"$@"}
