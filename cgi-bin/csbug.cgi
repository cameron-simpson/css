#!/bin/sh -u
#
# Web interface to csbug.
#	- Cameron Simpson <cs@zip.com.au> 29jul2005
#

: ${TMPDIR:=/tmp}
: ${PATH_INFO:=''}
: ${EMAIL:='cs@zip.com.au'}
: ${HTTP_COOKIE:=''}

HOME=/u/cameron
CSBUG_ROOT=$HOME/var/bugs
export HOME CSBUG_ROOT

sessionid=
sessiondir=$HOME/var/csbug-sessions
passwordfile=$sessiondir/.passwd

exec 2>>$sessiondir/.log

# login challenge, sets cookie
if [ "x$PATH_INFO" = x/login ]
then
  echo "IN LOGIN PATH, QUERY_STRING=[$QUERY_STRING]" >&2

  PARAM_login=
  PARAM_password=
  eval `query_string2sh`

  if [ -n "$PARAM_login" ]
  then
    lpw=$PARAM_login:$PARAM_password
    echo "LOOKUP [$lpw]" >&2
    lookup=`fgrep "$lpw" <"$passwordfile"`
    if [ "x$lookup" = "x$lpw" ]
    then
      echo LOGIN MATCH >&2
      sessionid=`mkpw` || exit 1
      echo "$PARAM_login" >"$sessiondir/$sessionid"
      echo NEW SESSION $sessionid >&2
      ##echo Content-Type: text/plain
      ##echo
      echo "Set-Cookie: csbug_session_id=$sessionid; path=$SCRIPT_NAME"
      echo "Location: $SCRIPT_NAME"
      echo
      env | sort
      exit 0
    fi
  fi

  echo IN LOGIN, no LOGIN field submission, offer form >&2

  echo Content-Type: text/html
  echo
  echo "<H1>Bug Login</H1>"
  echo "<FORM METHOD=\"GET\" ACTION=\"$SCRIPT_NAME/login\">"
  echo "<TABLE>"
  echo "<TR><TD ALIGN=LEFT>Login:<TD ALIGN=LEFT><INPUT TYPE=TEXTFIELD NAME=\"login\" LENGTH=\"32\">"
  echo "<TR><TD ALIGN=LEFT>Password:<TD ALIGN=LEFT><INPUT TYPE=PASSWORD NAME=\"password\" LENGTH=\"32\">"
  echo "<TR><TD ALIGN=LEFT><INPUT TYPE=SUBMIT VALUE=\"Login\">"
  echo "</TABLE>"
  echo "</FORM>"

  exit 0
fi

echo NOT IN LOGIN >&2

##echo "Content-Type: text/plain"
##echo
##env | grep '^HTTP'
##echo

echo "check \$HTTP_COOKIE: $HTTP_COOKIE" >&2
case "; $HTTP_COOKIE; " in
  *'; csbug_session_id='*)
    sessionid=`echo " $HTTP_COOKIE" | sed -n 's:.*; *csbug_session_id=\([^;/]*\).*:\1:p'`
    if [ -n "$sessionid" ]
    then
      echo "got cookie, sessionid=$sessionid" >&2
      sessfile=$sessiondir/$sessionid
      if [ -s "$sessfile" ]
      then email=`sed 1q "$sessfile"`
	   echo "email is $email" >&2
      else sessionid=
      fi
    fi
    ;;
esac

if [ -z "$sessionid" ]
then
  echo "after HTTP_COOKIE, no sessionid - redirect to login" >&2
  echo "Location: $SCRIPT_NAME/login"
  echo
  exit 0
fi

echo "login/session accepted, sessionid=$sessionid, email=$email" >&2

trap 'echo "<PRE>";id;pwd;env | sort;echo "</PRE>"' 0

# load up $PATH_INFO as argument list
oIFS=$IFS
IFS=/
set -- $PATH_INFO
IFS=$oIFS

# strip empty path segments
first=1
for seg
do
  [ $first ] && { first=; set --; }
  [ -n "$seg" ] || continue
  set -- ${1+"$@"} "$seg"
done

# field len
edit_bugfield()
{ ebf_field=$1
  ebf_len=$2
  eval "ebf_value=\$bugfield_$ebf_field"
  ebf_qvalue=`qsencode "$ebf_value"`

  necho "<FORM METHOD=GET ACTION=\"$SCRIPT_NAME/$CSBUG_BUGNUM/\"><INPUT TYPE=TEXTFIELD LENGTH=$ebf_len VALUE=\"$ebf_value\"></FORM>"
}

echo Content-Type: text/html
echo
echo "Hello <A HREF=\"mailto:$email\">$email</A>."

if [ $# = 0 ]
then
  echo "<H1>All Bugs</H1>"
  echo "<TABLE>"
  csbug LIST \
  | while read bugnum
    do
      eval `set | sed -n 's/^\(bugfield_[^=]*\)=.*/unset \1;/p'`
      eval `csbug -b "$bugnum" GET FIELDS -sh status headline hacker | sed 's/^/bugfield_/'`
      bugref=$SCRIPT_NAME/$bugnum/
      [ -n "$bugfield_headline" ] || bugfield_headline='--no-headline--'
      echo "<TR>"
      echo "  <TD ALIGN=RIGHT VALIGN=TOP>$bugnum:"
      echo "  <TD ALIGN=LEFT VALIGN=TOP>"
      echo "    <A NAME=\"$bugnum\" HREF=\"$bugref\">$bugfield_headline</A><BR>"
      echo "  <TD ALIGN=RIGHT VALIGN=TOP>$bugfield_status:"
      echo "  <TD ALIGN=LEFT VALIGN=TOP>"
      case "$bugfield_status,$bugfield_hacker" in
	NEW,*)		echo "<A HREF=\"$bugref?status=TAKEN&hacker=$EMAIL\">Take</A>" ;;
	TAKEN,$email)	echo "<A HREF=\"$bugref?status=DONE\">Mark as DONE</A>" ;;
	TAKEN,*)	echo "by $hacker" ;;
      esac
    done
  echo "</TABLE>"
  exit 0
fi

CSBUG_BUGNUM=$1; shift
export CSBUG_BUGNUM

# backref to top of tree
echo "[ <A HREF=\"$SCRIPT_NAME#$CSBUG_BUGNUM\">All Bugs</A>"

if [ $# = 0 ]
then
  echo "]"

  eval `csbug GET FIELDS -sh | sed 's/^/bugfield_/'`

  necho "<H1>Bug $CSBUG_BUGNUM: [$bugfield_status] $bugfield_headline</H1>"

  if [ -n "$QUERY_STRING" ]
  then
    eval `set | sed -n 's/^\(PARAM_[^=]*\)=.*/unset \1;/p'`
    eval `query_string2sh`
    for field in `set | sed -n 's/^PARAM_\([a-z][^=]*\)=.*/\1/p'`
    do  eval "value=\$PARAM_$field"
	csbug SET "$field" "$value" && echo "Set $field=$value<BR>"
    done
  fi
  exit 0
fi

# backref to bug overview
echo "| <A HREF=\"$SCRIPT_NAME/$CSBUG_BUGNUM/\">Bug $CSBUG_BUGNUM Overview</A>"

case "$1" in
  COMMENT:[1-9]*)
    n=`expr "$1" : 'COMMENT:\(.*\)'`
    [ $n -gt 1 ] && { pren=`expr $n - 1`; echo "| <A HREF=\"$SCRIPT_NAME/$CSBUG_BUGNUM/COMMENTS:$pren\">&lt;- Comment $pren"
    echo "]"
    csbug GET "$1" \
    | ( tmpf=$TMPDIR/csbug-cgi.$$
	trap 'rm -f "$tmpf"' 0
	cat >"$tmpf"
	eval `set | sed -n 's/^\(comment_[^=]*\)=.*/unset \1;/p'`
	eval `mhdrs -sh <"$tmpf" | sed 's/^/comment_/'`
	echo "<H2>$comment_SUBJECT</H2>"
	echo "Date: $comment_DATE<BR>"
      )
    ;;
  ATTACHMENT:[1-9]*)
    ;;
  *)echo "Unsupported bug subcomponent: \"$1\"."
    ;;
esac
