#!/usr/bin/perl
#
# Watch a Source for interesting lines and report context.
#	- Cameron Simpson <cs@zip.com.au> 13aug96
#

use strict qw(vars);

use cs::Source;

package cs::Monitor;

$cs::Monitor::WindowSize=5;

sub new
	{ my($class,$s,$checkfn,$window)=@_;

	  $checkfn=sub { 0; } if ! defined $checkfn;
	  $window=$WindowSize if ! defined $window;

	  bless { DS		=> $s,
		  WINDOW	=> $window,
		  BEFORE	=> [],
		  LINENO	=> 0,
		  LASTLINENO	=> 0,
		  INTERESTING	=> 0,
		  CHECK		=> $checkfn,
		}, $class;
	}

# fetch a line, return scalar line if uninteresting
#                   or { SKIPPED => skipped, LINE => line, BEFORE => [] }
#                   or '' on EOF
#                   or undef on error
#
sub Poll
	{ my($this)=shift;
	  local($_);

	  return undef if ! defined ($_=$this->{DS}->GetLine());
	  return ''    if ! length;

	  # cs::Upd::err("Poll: got $_");

	  $this->{LINENO}++;

	  my($before)=$this->{BEFORE};

	  if (&{$this->{CHECK}}($_))
		{ my($skipped)=$this->{LINENO}-$this->{LASTLINENO}-1;
		  $this->{LASTLINENO}=$this->{LINENO};

		  $this->{INTERESTING}=$this->{WINDOW};

		  $this->{BEFORE}=[];

		  return { SKIPPED => $skipped,
			   LINE    => $_,
			   BEFORE  => $before,
			 };
		}

	  if ($this->{INTERESTING} > 0)
		{ $this->{INTERESTING}--;
		}

	  if ($this->{INTERESTING} > 0)
		{ $this->{LASTLINENO}=$this->{LINENO};
		}
	  else
	  { while (@$before >= $this->{WINDOW})
		{ shift(@$before);
		}

	    push(@$before,$_);
	  }

	  $_;
	}

1;
