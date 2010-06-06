#!/usr/bin/perl
#
# Target objects.
#	- Cameron Simpson <cs@zip.com.au> 
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Myke::Target;

@cs::Myke::Target::ISA=qw(cs::Myke);

$cs::Myke::Target::F_RUNNING	=0x01;
$cs::Myke::Target::F_INFERRED	=0x02;

sub find
	{ my($myke,$name,$level)=@_;
	  my($this);

	  if (exists $myke->{TARGETS}->{$name})
		{ $this=$myke->{TARGETS}->{$name};
		}
	  else
	  { $this=new cs::Myke::Target $myke, $name, $level;
	  }

	  $this;
	}

sub new
	{ my($class,$myke,$name,$level)=@_;
	  $level=$myke->{LEVEL} if ! defined $level;
	  # $name=cs::Pathname::fullpath($name) if $cs::Myke::F_CanonicalPaths;

	  my($this);

	  if (exists $myke->{TARGETS}->{$name})
		{ my($otarget)=$myke->{TARGETS}->{$name};
		  if ($otarget->Level() < $level)
			# override
			{}
		  elsif ($otarget->Level() > $level)
			{ warn "$name: already got a superior definition";
			}
		  else
		  { warn "$name: multiple definition";
		  }
		}

	  if (! defined $this)
		{ $this
			=$myke->{TARGETS}->{$name}
			={ NAME		=> $name,	# target name
			   MYKE		=> $myke,	# context
			   FLAGS	=> 0x00,	# status flags
			   LEVEL	=> $level,	# precedence
			   PENDING	=> [],		# unissued prereqs
			   DONE		=> [],		# issued prereqs
			   ACTIONS	=> [],		# unissued actions
			   BLOCKED	=> 0,		# pending count
							# must be zero to
							# proceed to next stage
			   OK		=> 1,		# no failure yet
			   WAITERS	=> [],		# targets waiting for
							# this
			 };

	  	  bless $this, $class;
		}
	}

sub Level	{ shift->{LEVEL} }
sub Pending	{ shift->{PENDING} }
sub Actions	{ shift->{ACTIONS} }
sub Running	{ shift->{FLAGS}&$cs::Myke::Target::F_RUNNING }
sub DidInference{ shift->{FLAGS}&$cs::Myke::Target::F_INFERRED }

sub Run
	{ my($this)=@_;

	  if ($this->Running())
		{ warn "$this->{NAME} already active!";
		}
	  else
	  { $this->{FLAGS}|=$cs::Myke::Target::F_RUNNING;

	    my($name)=$this->{NAME};

	    warn "Running($name)";
	    warn "$name=\n".cs::Hier::h2a($this,1,1);

	    my($myke)=$this->{MYKE};

	    my($pending);

	    if (! $this->DidInference())
		{ warn "Infer($name)";
		  $this->Infer();
		}

	    if ($this->{OK} && ! $this->Blocked()
	     && @{$pending=$this->Pending()})
		{ warn "issuing pending prereqs of $name";
		  for (@$pending)
			{ $_=find($myke,$_) if ! ref $_;

			  warn "$name: issuing $_->{NAME}";
			  if ($_->Run())
				{ warn "$name: must block for $_->{NAME}";
				  $this->WaitingOn($_);
				}
			  elsif (! $_->{OK})
				{ warn "$name: prereq $_->{NAME} fails";
				  $this->{OK}=0;
				}
			}
		}

	    if ($this->{OK} && ! $this->Blocked())
		{ my($actions)=$this->Actions();
		  my($a);

		  warn "issuing actions for $name: [$actions]";

		  ACTION:
		   while (@$actions && $this->{OK} && ! $this->Blocked())
			{ $a=shift(@$actions);
			  if (ref $a)
				{ 
				  warn "$name: no special action implementations yet";
				}
			  else
				{
				  warn "$name: action: $a";
				  $a=new cs::Myke::Action($myke,$a);
				  if ($a->Run())
					{ warn "$name: must block for $a->{ACTION}";
					  $this->WaitingOn($a);
					}
				  elsif ($a->Status() != 0)
					{ warn "$name: action fails: $_->{ACTION}";
					  $this->{OK}=0;
					}
				}
			}
		}

	    $this->{FLAGS}&=~$cs::Myke::Target::F_RUNNING;
	  }

	  warn "leaving Run($this->{NAME})";

	  $this->Blocked();
	}

sub Infer
	{ my($this)=@_;

	  warn "no inference yet";
	  $this->{FLAGS}|=$cs::Myke::Target::F_INFERRED;
	}

sub Require
	{ my($this,$target)=@_;
	  warn "$this->{NAME}: note prereq \"$target\"";
	  push(@{$this->{PENDING}},$target);
	}
sub Action
	{ my($this,$action)=@_;
	  warn "$this->{NAME}: note action \"$action\"";
	  push(@{$this->{ACTIONS}},$action);
	}

1;
