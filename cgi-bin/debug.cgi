#!/bin/sh

## . `dirname "$0"`/.cgienv.sh

echo "Content-Type: text/plain"
echo

uptime
pwd
hostname
id
which perl

echo
echo "0=$0"
echo "*=$*"

echo
env|sort

echo
cat | sed l

exit 0
