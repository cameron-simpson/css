#!/bin/sh -u
#
# Set up user environment to use the CSS tools.
#	- Cameron Simpson <cs@zip.com.au> 09jun2005
#

: ${OPTCSS:=/opt/css}
: ${HOSTNAME:=`hostname`}
: ${HOST:=`echo "$HOSTNAME" | sed 's/\..*//'`}
: ${HOSTDOMAIN=`echo "$HOSTNAME" | sed 's/[^.]*\.//'`}
: ${USER:=`whoami`}
: ${SITENAME:=$HOSTDOMAIN}
: ${EMAIL:="$USER@$SITENAME"}

PATH=$PATH:$OPTCSS/bin
MANPATH=$MANPATH:$OPTCSS/man
PERL5LIB=$PERL5LIB:$OPTCSS/lib
CLASSPATH=$CLASSPATH:$OPTCSS/lib/au.com.zip.cs.jar

## When actually used much.
## PYTHONPATH=$PYTHONPATH:$OPTCSS/lib

export PATH MANPATH PERL5LIB HOST HOSTNAME HOSTDOMAIN USER SITENAME EMAIL
