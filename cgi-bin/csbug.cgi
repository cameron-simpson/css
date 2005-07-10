#!/bin/sh -u
#
# Web interface to csbug.
#	- Cameron Simpson <cs@zip.com.au> 29jul2005
#

set -u
umask 002

: ${TMPDIR:=/tmp}
: ${PATH_INFO:=''}
: ${EMAIL:='cs@zip.com.au'}
: ${HTTP_COOKIE:=''}
: ${QUERY_STRING:=''}
: ${CSBUG_ROOT:=$HOME/var/bugs}
: ${CSBUG_SESSIONS:=$CSBUG_ROOT/.sessions}

: ${OPTCSS:=/opt/css}
. "$OPTCSS/env.sh"

export TMPDIR EMAIL CSBUG_ROOT CSBUG_SESSIONS

sessiondir=$CSBUG_SESSIONS
sessionid=
passwordfile=$sessiondir/.passwd
logfile=$sessiondir/.log
exec 2>>"$logfile"

trace=set-x
set -x

# remove vars of given prefix
unenv_pfx()
{ eval `set | sed -n "s/^\\\\($1[a-z_0-9]*\\\\)=.*/unset \\\\1;/"`
}
necho()
{ printf "%s" "$*"
}
# field len
edit_bugfield()
{ ebf_field=$1
  ebf_len=$2
  eval "ebf_value=\$bugfield_$ebf_field"
  ebf_qvalue=`qsencode "$ebf_value"`

  necho "<FORM METHOD=GET ACTION=\"$SCRIPT_NAME/$CSBUG_BUGNUM/\"><INPUT TYPE=TEXTFIELD LENGTH=$ebf_len VALUE=\"$ebf_value\"></FORM>"
}
# link text [title/tip [name]]
ht_href()
{ ht_link=$1
  ht_text=$2
  ht_title=${3:-''}
  ht_name=${4:-''}
  necho "<A HREF=\"$ht_link\""
  [ -n "$ht_title" ] && echo " TITLE=\"$ht_title\""
  [ -n "$ht_name"  ] && echo " NAME=\"$ht_title\""
  necho ">$ht_text</A>"
}
# bugnum [anchor-text]
ht_bugref()
{ ht_bugnum=$1
  ht_head=`csbug -b "$ht_bugnum" GET headline | htencode`
  ht_text=${2:=$ht_head}
  ht_href "$SCRIPT_NAME/$ht_bugnum/" "$ht_text" "bug $ht_bugnum: $ht_head"
}
ht_hacker()
{ necho "<A HREF=\"mailto:$1\">$1</A>"
}
# bugnum [link-text]
ht_addcomment_url()
{ ht_bugnum=${1:-$CSBUG_BUGNUM}
  ht_fmt="csbug%d+COMMENT@$MAILDOMAIN"
  printf "mailto:$ht_fmt" "$ht_bugnum"
}
# bugnum [link-text]
ht_addcomment()
{ ht_bugnum=${1:-$CSBUG_BUGNUM}
  ht_text=${2:-"Add Comment"}
  necho "<A HREF=\""; ht_addcomment_url "$ht_bugnum"; necho "\">$ht_text</A>"
}

unenv_pfx PARAM_
if [ -n "$QUERY_STRING" ]
then
  echo "QUERY_STRING=[$QUERY_STRING]" >&2
  eval `query_string2sh`
fi

# login challenge, sets cookie
if [ "x$PATH_INFO" = x/login ]
then
  echo "IN LOGIN PATH" >&2

  : ${PARAM_login:=''}
  : ${PARAM_password:=''}
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
      ( echo "Set-Cookie: csbug_session_id=$sessionid; path=$SCRIPT_NAME"
	echo "Content-Type: text/html"
	echo
	echo "<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 4.01 Transitional//EN\">"
	echo "<HTML><HEAD><TITLE>Login accepted for $PARAM_login</TITLE>"
	echo "<meta http-equiv=\"Refresh\" CONTENT=\"1;URL=$SCRIPT_NAME\">"
	echo "</HEAD><BODY>Login accepted for $PARAM_login."
	echo "You should be transferred to the main bug page in one second."
	echo "If this does not happen, please <A HREF=\"$SCRIPT_NAME\">proceed manually</A>."
	echo "</BODY></HTML>"
      ) | tee -a "$logfile"
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

echo "check \$HTTP_COOKIE: $HTTP_COOKIE" >&2
case "; $HTTP_COOKIE; " in
  *'; csbug_session_id='*)
    sessionid=`echo "; $HTTP_COOKIE" | sed -n 's:.*; *csbug_session_id=\([^;/]*\).*:\1:p'`
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
  add_hdr
  echo "Location: $SCRIPT_NAME/login"
  echo
  exit 0
fi

echo "login/session accepted, sessionid=$sessionid, email=$email" >&2

wkdir=`mkdirn "$TMPDIR/csbug-cgi-"` || exit 1
trap 'rm -rf "$wkdir"' 0 1 15

wk_hdr=$wkdir/hdr.txt
wk_toplinks=$wkdir/topline.txt
wk_body=$wkdir/body.html

# link text
add_toplink()
{ echo "$*" >>"$wk_toplinks"
}

add_hdr(){ echo "$*" >>"$wk_hdr"; }
# link text title
add_toplink()
{ echo "ADD_TOPLINK: 1=[$1] 2=[$2] 3=[$3]" >&2
  echo "$1 $2" >>"$wk_toplinks"
  echo "$3" >>"$wk_toplinks"
}

exec 3>"$wk_body"

( exec >&3

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

  add_hdr Content-Type: text/html

  if [ $# = 0 ]
  then
    echo "<H1>All Bugs</H1>"
    echo "<TABLE>"
    csbug SQL "select bugnum from bugfields where field = 'status' and value = 'NEW' or field = 'hacker' and value = '$email'" \
    | sort -rnu \
    | while read bugnum
      do
	eval `set | sed -n 's/^\(bugfield_[^=]*\)=.*/unset \1;/p'`
	eval `csbug -b "$bugnum" GET FIELDS -sh status headline hacker | sed 's/^/bugfield_/'`
	bugref=$SCRIPT_NAME/$bugnum/
	[ -n "$bugfield_headline" ] || bugfield_headline='--no-headline--'
	echo " <TR>"
	echo "  <TD ALIGN=RIGHT VALIGN=TOP>$bugnum:"
	necho "  <TD ALIGN=LEFT VALIGN=TOP>"
	ht_bugref "$bugnum" "$bugfield_headline"; echo "<BR>"
	echo "  <TD ALIGN=RIGHT VALIGN=TOP>$bugfield_status:"
	echo "  <TD ALIGN=LEFT VALIGN=TOP>"
	: ${bugfield_hacker:=''}
	case "$bugfield_status,$bugfield_hacker" in
	  NEW,*)
	    echo "<A HREF=\"$bugref?status=TAKEN&hacker=$EMAIL\">Take</A>"
	    ;;
	  TAKEN,$email)
	    echo "<A HREF=\"$bugref?status=DONE\">Mark as DONE</A>"
	    ;;
	  TAKEN,*)
	    echo "by $hacker"
	    ;;
	esac
	##necho ",&nbsp;"; ht_addcomment "$bugnum" "Comment"
	children=`$trace csbug -b "$bugnum" GET CHILDREN`
	if [ -n "$children" ]
	then
	  echo "BUG $bugnum: CHILDREN=[$children]" >&2
	  necho "<TD ALIGN=LEFT>Children:"
	  sep=" "
	  for ch in $children
	  do  necho "$sep"
	      ht_bugref "$ch" "$ch"
	      sep=", "
	  done
	  echo
	fi
	echo "  <TD ALIGN=RIGHT COLSPAN=3><FORM METHOD=GET ACTION=\"$SCRIPT_NAME/new\"><INPUT TYPE=TEXTFIELD LENGTH=48 NAME=\"headline\"><INPUT TYPE=HIDDEN NAME=\"META_parents\" VALUE=\"$bugnum\"><INPUT TYPE=SUBMIT VALUE=\"Open New Child Bug\"></FORM>"
      done
    echo "</TABLE>"
    exit 0
  fi

  #########################################################################################
  # Some subfunction.
  #
  # backref to top of tree
  add_toplink "#" "All Bugs" "Bug Listing Overview"

  case "$1" in
    new)
	  : ${PARAM_headline:=''}
	  CSBUG_BUGNUM=`$trace csbug NEW "$PARAM_headline"` || exit 1
	  export CSBUG_BUGNUM
	  csbug SET submitter "$email"
	  echo "Created new bug." >&3
	  : ${PARAM_META_parents:=''}
	  if [ -n "$PARAM_META_parents" ]
	  then
	    set -- `echo "$PARAM_META_parents" | tr , ' '`
	    for parent
	    do  csbug PARENTS "+$parent"
	    done
	  fi
	  echo "Proceed to <A HREF=\"$SCRIPT_NAME/$CSBUG_BUGNUM/\">bug $CSBUG_BUGNUM</A>" >&3
	  echo "or to the <A HREF=\"$SCRIPT_NAME\">bug overview listing</A>." >&3
	  exit 0
	  ;;
    [1-9]*)
	  ;;
    *)	echo "NO MATCH [$1]"
	  exit 1
	  ;;
  esac

  ###########################################################################################
  # Per bug.
  #
  CSBUG_BUGNUM=$1; shift
  export CSBUG_BUGNUM

  unenv_pfx bugfield_
  eval `csbug GET FIELDS -sh | sed 's/^/bugfield_/'`

  add_toplink "`ht_addcomment_url`" "Add Comment" "Add New Comment to bug $CSBUG_BUGNUM via email."

  if [ $# = 0 ]
  then
    necho "<H1>Bug $CSBUG_BUGNUM: [$bugfield_status] $bugfield_headline</H1>"

    : ${bugfield_hacker:=''}
    [ -n "$bugfield_hacker" ] && { necho "hacker: "; ht_hacker "$bugfield_hacker"; echo "<P>"; }

    csbug HTML COMMENTS

    if [ -n "$QUERY_STRING" ]
    then
      for field in `set | sed -n 's/^PARAM_\([a-z][^=]*\)=.*/\1/p'`
      do  eval "value=\$PARAM_$field"
	  csbug SET "$field" "$value" && echo "Set $field=$value<BR>"
      done
    fi
    exit 0
  fi

  #################################################################################
  # Bug subcomponent.
  #

  # backref to bug overview
  add_toplink "$CSBUG_BUGNUM" "Bug $CSBUG_BUGNUM" "Bug $CSBUG_BUGNUM Overview"

  necho "<H1>Bug $CSBUG_BUGNUM: [$bugfield_status] $bugfield_headline</H1>"
  ht_addcomment; echo "<BR>"

  subsection=$1; shift
  case "$subsection" in
    COMMENTS)
      if [ $# = 0 ]
      then
	echo "<TABLE BORDER=1>"
	ncomments=`csbug GET COMMENT:`
	for n in `seq $ncomments -1 1`
	do
	  echo "<TR><TD>"
	  csbug HTML "COMMENT:$n"
	done
	echo "</TABLE>"
	continue
      fi

      n=$1; shift
      csbug HTML "COMMENT:$n"
      ;;
    COMMENT:[1-9]*)
      csbug HTML "$1"
      ;;
    ATTACHMENT:[1-9]*)
      ;;
    *)echo "Unsupported bug subcomponent: \"$subsection\"."
      ;;
  esac
)

if [ -s "$wk_hdr" ]
then cat "$wk_hdr"
else echo "Content-Type: text/html"
fi
echo

# top links
echo "<TABLE WIDTH=\"100%\">"

echo " <TR>"
necho "  <TD ALIGN=LEFT>"; [ -n "$email" ] && necho "Hello $email."; echo

necho "  <TD ALIGN=CENTER>"
if [ -s "$wk_toplinks" ]
then
  sep="[ "
  while read link text && read title
  do
    case "$link" in
      /* | http:* | mailto:* )
	;;
      *)
	link="$SCRIPT_NAME/$link"
	;;
    esac
    httext=`printf "%s" "$text" | htencode`
    necho "$sep<A HREF=\"$link\" TITLE=\"$title\">$httext</A>"
    sep=" | "
  done <"$wk_toplinks"
  echo " ]"
fi

echo "  <TD ALIGN=RIGHT><A HREF=\"$SCRIPT_NAME/logout\">Log out</A>"
echo "<TR><TD ALIGN=RIGHT COLSPAN=3><FORM METHOD=GET ACTION=\"$SCRIPT_NAME/new\"><INPUT TYPE=TEXTFIELD LENGTH=48 NAME=\"headline\"><INPUT TYPE=SUBMIT VALUE=\"Open New Bug\"></FORM>"
echo "</TABLE>"

cat "$wk_body"
