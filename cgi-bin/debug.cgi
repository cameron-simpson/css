#!/bin/sh

echo "Content-Type: text/plain"
echo

hostname
id
uptime
/bin/pwd
which perl
df -k .
df -lk
df -k
mount

echo
ls -la ..

echo
echo "0=$0"
echo "*=$*"

echo
env|sort

echo
cat | sed l

exit 0
