#!/bin/sh
#
# Sourced by shell CGIs to set useful parameters.
# Also captures stderr and reports.
#	- Cameron Simpson <cs@zip.com.au> 04mar99
#

# figure out where we are
wd=`pwd`
wdscripts=`dirname "$wd"`	## probably in ~/bins/cgi-bin

case "$HOSTNAME" in
  sweet.research.canon.com.au)
    ARCH=redhat.x86.linux
    SYSTEMID=cisra
    HOME=/u/cameron
    WEBPROXY=proxy:8080
    ;;
  *)
    case "$wd" in
      /u/cameron/*)
	ARCH=sun.sparc.solaris
	SYSTEMID=home
	HOME=/u/cameron
	WEBPROXY=proxy:8080
	;;
      /home/cameron/*)
	;;
      /home/virtual/site76/fst/var/www/cgi-bin)
	ARCH=redhat.x86.linux
	SYSTEMID=ezos
	HOME=$DOCUMENT_ROOT/cs
	wdscripts=$HOME/bins
	;;

      /home/kaper/cameron/* | /a/kaper/home/cameron/* )
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
      /home[13]/cs/* | /home/cs/*)
	ARCH=redhat.x86.linux
	SYSTEMID=zip
	HOME=/home/cs
	WEBPROXY=proxy:8080
	;;
      *)echo content-type: text/plain
	echo
	echo "$0: can't deduce system environment"
	pwd
	hostname
	id
	which perl
	echo "\$0=$0"
	echo "\$cmdpath=$cmdpath"
	echo " *=$*"
	echo
	env|sort
	echo
	ls -la
	exit 0
	;;
    esac
    ;;
esac

CS_WRAPPER=$ARCH@$SYSTEMID
SCRIPT_URL=${SCRIPT_URL:-$SCRIPT_NAME}
SCRIPT_URI=${SCRIPT_URI:-"http://$HTTP_HOST$SCRIPT_URL"}
http_proxy=$WEBPROXY
ftp_proxy=$WEBPROXY
PATH=$wdscripts/stubs:$wdscripts:$HOME/bins/stubs:$HOME/bins:$HOME/bin/$ARCH:$PATH:/opt/bin:/opt/bin:/usr/local/bin
PERL5LIB=$HOME/rc/perl:${PERL5LIB:-'/opt/perl/lib:/usr/lib/perl5'}
export HOME ARCH SYSTEMID HOME WEBPROXY http_proxy ftp_proxy PATH PERL5LIB
export SCRIPT_URL SCRIPT_URI CS_WRAPPER
