#!/usr/bin/perl
#
# Action objects (subprocesses).
#	- Cameron Simpson <cs@zip.com.au> 11may97
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Myke::Action;

@cs::Myke::Action::ISA=qw(cs::Myke);

$cs::Myke::Action::F_ISSUED	=0x01;
$cs::Myke::Action::F_FINISHED	=0x02;

sub new
	{ my($class,$myke,$action)=@_;

	  bless { ACTION	=> $action,
		  MYKE		=> $myke,
		  FLAGS		=> 0,
		  PID		=> undef,
		  STATUS	=> undef,
		  BLCOKED	=> 0,
		  WAITERS	=> [],
		}, $class;
	}

sub Issued	{ shift->{FLAGS}&$cs::Myke::Action::F_ISSUED }
sub Finished	{ shift->{FLAGS}&$cs::Myke::Action::F_FINISHED }
sub Status	{ shift->{STATUS} }

sub Finish	{ my($this,$status)=@_;
		  $this->{STATUS}=$status;
		  $this->{FLAGS}|=$cs::Myke::Action::F_FINISHED;
		}

sub Run
	{ my($this)=@_;
	  my($myke)=$this->{MYKE};
	  my($action)=$this->{ACTION};

	  warn "run action: [$action]";

	  if (! $this->Issued())
		{ my($pid);

		  $this->{FLAGS}|=$cs::Myke::Action::F_ISSUED;
		  if (! defined ($pid=fork()))
			# fork fails
			{ warn "can't fork($this->{ACTION}): $!";
			  $this->Finish(-1);
			}
		  elsif ($pid > 0)
			# parent
			{ $this->{PID}=$pid;
			  my($proc)=new cs::Myke::Process $pid;
			  $this->WaitingOn($proc);
			}
		  else
		  { exec($myke->{SHELL},'-c',$action);
		    die "exec($myke->{SHELL}) fails: $!";
		  }
		}

	  return 0 if ($this->Finished());	# don't block

	  1;
	}

1;
