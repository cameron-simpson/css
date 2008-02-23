#!/usr/bin/perl
#
# Code to fiddle with Message-IDs.
#	- Cameron Simpson <cs@zip.com.au>, 06sep94
#

require 'cs/index.pl';
require 'cs/pathname.pl';
require 'cs/lists.pl';

package msgid;

sub getattr	# (msgid,attr) -> attrval or undef
	{ local($msgid,$attr)=@_;
	  if (!length $msgid)
		{ print STDERR "msgid'getattr(msgid=[],attr=[$attr]) called from ",join('|',caller(0)),"\n";
		}
	  local($msgidndx,$msgidkey)=&cs_index'msgid2ndxkey($msgid);
	  local($_);

	  $_=&cs_index'getattr($msgidndx,$msgidkey,$attr);
	  return undef if !defined;
	  $_;
	}

sub setattr	# (msgid,attr,value) -> ok
	{ local($msgid,$attr,$value)=@_;
	  local($msgidndx,$msgidkey)=&cs_index'msgid2ndxkey($msgid);
	  local($_);

	  &cs_index'setattr($msgidndx,$msgidkey,$attr,$value);
	}

sub appendattr	# (msgid,attr,appendage[,sep]) -> newvalue
	{ local($aa_id,$aa_a,$aa_ap,$aa_s)=@_;
	  if (!length($aa_id))
		{ print STDERR "zerolength msgid, called from [",join('|',caller),"]\n";
		}
	  local($aa_mn,$aa_mk)=&cs_index'msgid2ndxkey($aa_id);
	  local($_);
	  $_=&cs_index'appendattr($aa_mn,$aa_mk,$aa_a,$aa_ap,$aa_s);
	  return undef if !defined;
	  $_;
	}

sub addlink	# (msgid,link)
	{ local($msgid,$link)=@_;
	  &appendattr($msgid,'links',&'tilde($link));
	  if (!length($msgid))
		{ print STDERR "addlink: zerolength msgid, called from [",join('|',caller),"]\n";
		}
	}

sub links	# (msgid) -> links
	{ &lists'sep(&getattr($_[0],'links'));
	}


# locate a readable link, possibly under a constraint
sub findlink	# msgid -> link or undef
	{ local($fl_id,$regexp)=@_;
	  if (!length($fl_id))
		{ print STDERR "findlink(\"\") called from ",join('|',caller(0)),"\n";
		}
	  local(@links)=&links($fl_id);
	  local($_);

	  for (@links)
		{ $_=&'untilde($_);
		  return $_ if -r $_ && (!defined($regexp) || /$regexp/);
		}

	  return undef;
	}

sub setparent	# (msgid,parent)
	{ local($sp_msgid,$sp_parent)=@_;
	  &appendattr($sp_msgid,'parents',$sp_parent);
	  &appendattr($sp_parent,'children',$sp_msgid);
	}

sub children	# (msgid) -> @children
	{ &lists'sep(&getattr($_[0],'children'));
	}

sub parent	# msgid -> parent or @parents or undef
	{ local(@p)=&lists'sep(&getattr($_[0],'parents'));
	  return undef unless @p;
	  wantarray ? @p : shift @p;
	}

1;
