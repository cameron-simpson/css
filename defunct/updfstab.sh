#!/bin/sh

HOSTNAME=`hostname`
HOST=`expr "x$HOSTNAME" : 'x\([^.]*\).*'`

fslist=fslist	# fslist=/usr/local/etc/fslist

udirptn='\/u\/[a-z][a-z]*\/[0-9][^ 	\/]*'
ufmt='/u/$host/$fs'

tmpdirptn='\/mnt\/tmp\/[a-z][a-z]*'
tmpfmt='/tmp/mnt/$host'

case $ARCH in
    sgi.mips.irix|sun.sparc.sunos)
		fstab=/etc/fstab
		fsline='$host:$srcdir	$localdir	nfs	rw,bg,soft	0 0'
		umatch="^[a-z][a-z]*:$udirptn[ 	]"
		tmpmatch="^[a-z][a-z]*:\/[^ 	]*[ 	][ 	]*$tmpdirptn[ 	]"
		;;
    sun.sparc.solaris)
		fstab=/etc/vfstab
		fsline='$host:$srcdir	-	$localdir	nfs	-	yes	soft,bg'
		umatch="^[a-z][a-z]*:$udirptn[ 	]"
		tmpmatch="^[a-z][a-z]*:$tmpdirptn[ 	]"
		;;
	*)	echo "Sorry, I don't know the fstab conventions for ARCH=\"$ARCH\"" >&2
		exit 1
		;;
esac

ofstab=/tmp/ofstab$$
nfstab=/tmp/nfstab$$
cfslist=/tmp/fslist$$

tidyup='rm -f $ofstab $nfstab $cfslist'

trap "$tidyup; exit 1" 1 3 15

ok=1

# strip lines we look after
sed -e "/$umatch/d
	/$tmpmatch/d" <$fstab >$nfstab

cat $nfstab

# get fslist sans comments
sed 's/^[ 	]*//
     /^#/d
     /^$/d' <$fslist >$cfslist

# parse fslist
mode=
while read a b c
do
    case "$a" in
	u:)	mode=u; continue ;;
	tmp:)	mode=tmp; continue ;;
	*:)	echo "unknown mode \"$a\"" >&2
		ok=
		mode=
		continue
		;;
    esac
    [ -n "$mode" ] || { echo "no mode: line, rejecting:" $a $b $c
			ok=
			continue
		      }

    case "$mode" in
	u)	host=$a fs=$b
		[ "x$host" = "x$HOST" ] && continue
		fs=/u/$host/$fs
		srcdir=$fs localdir=$fs
		;;
	tmp)	host=$a fs=$b
		[ "x$host" = "x$HOST" ] && continue
		srcdir=$fs localdir=/mnt/tmp/$host
		;;
	*)	echo "huh? can't handle mode \"$mode:\"" >&2
		ok=
		continue
		;;
    esac
    eval "echo \"$fsline\""
done <$cfslist | sort

eval "$tidyup"

[ $ok ] && exit 0
exit 1
