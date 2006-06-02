#!/bin/sh -u
#
# Set up user environment to use the CSS tools.
#	- Cameron Simpson <cs@zip.com.au> 09jun2005
#

: ${OPTCSS:=/opt/css}
: ${HOSTNAME:=`hostname`}
: ${HOST:=`echo "$HOSTNAME" | sed 's/\..*//'`}
: ${HOSTDOMAIN:=`echo "$HOSTNAME" | sed 's/[^.]*\.//'`}
[ -n "$HOSTDOMAIN" ] || HOSTDOMAIN=localdomain
: ${USER:=`whoami`}
: ${SITENAME:=$HOSTDOMAIN}
: ${MAILDOMAIN:=$SITENAME}
: ${EMAIL:="$USER@$MAILDOMAIN"}
: ${MANPATH:=/usr/man:/usr/share/man}
: ${PATH:=/usr/bin:/bin}
: ${PERL5LIB:=/usr/lib/perl5}
: ${CLASSPATH:=/usr/lib/jre/lib}

PATH=$PATH:$OPTCSS/bin:/sbin:/usr/sbin
MANPATH=$MANPATH:$OPTCSS/man
PERL5LIB=$PERL5LIB:$OPTCSS/lib
CLASSPATH=$CLASSPATH:$OPTCSS/lib/au.com.zip.cs.jar
PYTHONPATH=${PYTHONPATH:+"$PYTHONPATH:"}$OPTCSS/lib

export PATH MANPATH PERL5LIB HOST HOSTNAME HOSTDOMAIN MAILDOMAIN USER SITENAME EMAIL CLASSPATH PYTHONPATH
