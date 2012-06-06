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
: ${PATH:=/usr/bin:/bin}
: ${MANPATH:=/usr/man:/usr/share/man}
: ${CLASSPATH:=/usr/lib/jre/lib}

: ${OS:=''}
if [ -z "$OS" ]
then
  OS=`uname -s | tr '[A-Z]' '[a-z]'`
  case "$OS" in
    sunos)
      case "`uname -r`" in
        5.\*)   OS=solaris ;;
      esac
      ;;
  esac
fi

PATH=$PATH:$OPTCSS/bin:/sbin:/usr/sbin
MANPATH=$MANPATH:$OPTCSS/man
PERL5LIB=${PERL5LIB:+"$PERL5LIB:"}$OPTCSS/lib/perl
CLASSPATH=${CLASSPATH:+"$CLASSPATH:"}$OPTCSS/lib/java/au.com.zip.cs.jar
PYTHONPATH=${PYTHONPATH:+"$PYTHONPATH:"}$OPTCSS/lib/python

export PATH MANPATH PERL5LIB HOST HOSTNAME HOSTDOMAIN USER CLASSPATH PYTHONPATH OS
