#!/usr/bin/perl
#
# USENET news via NNTP connection.
#	- Cameron Simpson <cs@zip.com.au> 09jun98
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::News::NTTP;

@cs::News::NNTP::ISA=(cs::News);

sub new
	{ my($class,$server,$rw)=@_;
	  $rw=0 if ! defined $rw;

	  my($nntp)=new cs::NNTP ($server, $rw);

	  return undef if ! defined $nntp;

	  return undef if $rw && ! $nntp->CanPost();

	  my($this)=bless { NNTP => 

	  $this;
	}

1;
