#!/usr/bin/perl
#
# Processes (only tracking, not issue).
#	- Cameron Simpson <cs@zip.com.au> 11may97
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Myke::Process;

@cs::Myke::Process::ISA=qw(cs::Myke);

sub new
	{ my($class,$myke,$pid)=@_;

	  die "process $pid already active"
		if exists $myke->{PROCESSES}->{$pid};

	  $myke->{PROCESSES}->{$pid}
	  =bless { PID		=> $pid,
		   MYKE		=> $myke,
		   WAITERS	=> [],
		 }, $class;
	}

sub wait1
	{ my($myke)=@_;

	  my($pid,$status);

	  WAIT:
	    while (defined ($pid=wait()))
		{ $status=$?;
		  if (exists $myke->{PROCESSES}->{$pid})
			{ my($p)=$myke->{PROCESSES}->{$pid};
			  delete $myke->{PROCESSES}->{$pid};
			  $p->Finished($status);
			  $p->Unblock();
			  last WAIT;
			}
		  else
		  { warn "unknown child pid=$pid";
		  }
		}

	  return undef;
	}

1;
