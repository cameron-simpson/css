#!/bin/sh
#
# Sourced by shell CGIs to set useful parameters.
# Also captures stderr and reports.
#	- Cameron Simpson <cs@zip.com.au> 04mar99
#

# figure out where we are
case $0 in
    /*)	cmdpath=$0 ;;
    *)	cmdpath=`pwd`/$0 ;;
esac

case "$cmdpath" in
    /u/cameron/*)
	ARCH=sun.sparc.solaris
	SYSTEMID=home
	HOME=/u/cameron
	WEBPROXY=proxy:8080
	;;
    /a/zapff/home/cameron/* | /a/ivie/home/cameron/* | /home/ivie/cameron/* | /a/spindler/home/*)
	ARCH=redhat.x86.linux
	SYSTEMID=cisra
	HOME=/u/cameron
	WEBPROXY=proxy:8080
	PERL5LIB=/opt/perl/lib
	;;
    /home/docs/www/web/*|/usr/local/misc/htdocs/*)
	ARCH=redhat.x86.linux
	SYSTEMID=cisra
	HOME=/home/ivie/cameron
	WEBPROXY=proxy:8080
	PERL5LIB=/opt/perl/lib
	;;
    /home[13]/cs/*)
	ARCH=redhat.x86.linux
	SYSTEMID=zip
	HOME=/home1/cs
	WEBPROXY=proxy:8080
	;;
    *)	echo content-type: text/plain
	echo
	echo "$0: can't deduce system environment"
	pwd
	hostname
	id
	which perl
	echo "\$0=$0"
	echo "\$cmdpath=$cmdpath"
	echo " *=$*"
	env|sort
	exit 0
	;;
esac

cmd=`basename "$0"`
CS_WRAPPER=$ARCH@$SYSTEMID
SCRIPT_URL=${SCRIPT_URL:-$SCRIPT_NAME}
SCRIPT_URI=${SCRIPT_URI:-"http://$HTTP_HOST$SCRIPT_URL"}
http_proxy=$WEBPROXY
ftp_proxy=$WEBPROXY
PATH=$HOME/stubs:$HOME/scripts:$HOME/bin/$ARCH:$PATH:/opt/script:/opt/bin:/usr/local/bin
PERL5LIB=$HOME/rc/perl:${PERL5LIB:-'/opt/perl/lib:/usr/lib/perl5'}
export ARCH SYSTEMID HOME WEBPROXY http_proxy ftp_proxy PATH PERL5LIB
export SCRIPT_URL SCRIPT_URI CS_WRAPPER
