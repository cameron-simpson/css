#!/usr/bin/perl
#
# Code to manage various index information.
#
# The basic approach is to treat an index as a flat attrfile
# (see cs/attrdbm.pl) and to map different types of keys into
# different attrfiles and keys.
#
# Indices supported:
#	- message-ids
#

use POSIX;
require 'cs/dir.pl';
require 'cs/open.pl';
require 'cs/attrdbm.pl';

package cs_index;

$INDEXDIR="$ENV{HOME}/etc/index";
$INDEX='.index';	# if $INDEXDIR not defined
undef $currentindex;	# filename for cached index
undef %INDEX;		# cached index

sub final	{ &savecurrent; }

sub setattr	# (indexname,key,attr,value) -> ok
	{ local($indexname,$key,$attr,$value)=@_;
	  local($index);

	  return undef unless defined($index=&setcurrent($indexname));

	  &attrdbm'setattrs("cs_index'INDEX",$key,"$attr=$value");
	}

sub getattr	# (indexname,key,attr) -> value or undef
	{ local($indexname,$key,$attr)=@_;

	  return undef unless defined($index=&setcurrent($indexname));
	  return undef if !defined $INDEX{$key};

	  &attrdbm'getattr($INDEX{$key},$attr);
	}

sub appendattr	# (indexname,key,attr,appendage[,sep]) -> newattr or undef
	{ local($n,$k,$a,$ap,$s)=@_;
	  local($_);
	  if (! defined $s)	{ $s="\0"; }
	  $_=&getattr($n,$k,$a).$s.$ap;
	  &setattr($n,$k,$a,$_);
	  $_;
	}

sub setcurrent	# index name -> index filename or undef
	{ local($filename)=&ndx2fname($_[0]);

	  # print STDERR "setcurrent $filename\n";
	  if (defined($currentindex)
	   && $filename ne $currentindex)
		# save current index
		{ &savecurrent;
		}

	  if (!defined($currentindex)
	   || $filename ne $currentindex)
		{ # print STDERR "load index \"$filename\"\n";

		  undef %INDEX;
		  $currentindex=$filename;

		  local($F)=&openr($filename);
		  if (defined($F))
			{ %INDEX=&attrdbm'fload($F);
			  close($F);
			}
		}

	  $currentindex;
	}

sub savecurrent
	{ local($F);

	  return if !defined($currentindex);

	  if (!defined($F=&openw($currentindex)))
		{ print STDERR "$'cmd WARNING: couldn't save cache for $currentindex\n";
		  return undef;
		}

	  # print STDERR "save index \"$currentindex\"\n";
	  &attrdbm'fsave($F,%INDEX);
	  close($F);
	  1;
	}

sub ndx2fname	# index name -> full name
	{ local($_)=shift;

	  if (defined($INDEXDIR))
		{ $_=$INDEXDIR.'/'.$_; }
	  else	{ $_=$INDEX.'/'.$_; }
	}

sub openr { &open('<',@_); }
sub openw { &open('>',@_); }
sub open	# (mode,index filename) -> FILE or undef
	{ local($mode,$_)=@_;
	  &'mkdir(&'dirname($_));
	  local($FILE);
	  if (!defined($FILE=&'subopen($mode,$_)))
		{ if ($mode ne '<' || $! != POSIX->ENOENT)
			{ print STDERR "$'cmd: cs_index'open($mode,$_): $!\n";
			}

		  return undef;
		}

	  # print STDERR "opened $mode \"$_\": $FILE\n";
	  $FILE;
	}

sub msgid2ndxkey	# msgid -> (indexname,key)
	{ local($_)=@_;
	  local($left,$right);

	  s/\s+//g;
	  if ((($left,$right) = /^<([^@<>]+)@([^@<>]+)>$/))
		{ $right =~ tr:/:@:;
		  $right =~ tr/A-Z/a-z/;
		  $right =~ s:.*\.(.*\..*):$1:;	# max 2 domains
		  ("msgid/$right",$_);
		}
	  else
	  { print STDERR "\"$_\" doesn't look like a message-id\n";
	    print STDERR "called from [",join('|',caller),"]\n";
	    ("msgid/BADIDS",$_);
	  }
	}

sub url2ndxkey	# url -> (indexname,key)
	{ local($_)=@_;
	  local($left,$right);

	  if ((($left,$right) = m;^(\w+://[^/:]+)(.*);))
		{ $left =~ s://::;
		  $left =~ tr/A-Z/a-z/;
		  ("url/$left",$_);
		}
	  else
	  { print STDERR "\"$_\" doesn't look like URL\n";
	    print STDERR "called from [",join('|',caller),"]\n";
	    ("url/BADURLS",$_);
	  }
	}

1;
