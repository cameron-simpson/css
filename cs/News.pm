#!/usr/bin/perl
#
# USENET news connection.
#	- Cameron Simpson <cs@zip.com.au> 09jun98
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::NNTP;
use cs::Range;

package cs::News;

sub new
{ my($class)=shift;

  my($this)=bless { TYPE	=> TOPLEVEL,
		  }, $class;

  my($nntp)=$this->_MetaNNTP();
  return undef if ! defined $nntp;

  $this->{NNTP}=$nntp;

  $this;
}

sub Top
{ my($this)=@_;

  return $this if $this->{TYPE} eq TOPLEVEL;

  $this->{TOP};
}

sub CanPost
{ my($this)=@_;

  $this->{TYPE} eq TOPLEVEL
	? $this->{NNTP}->{NNTP}->CanPost()
	: $this->Top()->CanPost();
}

# fork and return an object to the caller
# child listens to object and speaks to the NNTP server
sub _MetaNNTP
{ my($this)=shift;

  my($nntp)=new cs::NNTP (@_);
  return undef if ! defined $nntp;

  my($meta)=bless { TYPE	=> METANNTP,
		    TOP		=> $this,
		    NNTP	=> $nntp,	# real NNTP
		  }, cs::News;

  my($toChild)=cs::IO::mkHandle();
  my($pid);

  if (! defined ($pid=open($toChild,"|-")))
	{
	  warn "$::cmd: open/fork: $!\n";
	  return undef;
	}

  if ($pid != 0)
	# parent - return object
	{ $meta->{CHILD}=$toChild;
	  return $meta;
	}

  local($_);
  my($rq);

  while (defined ($_=<STDIN>))
	{ chomp;
	  if (! /^(\w+)\s?/)
		{ die "$::cmd: bad request \"$_\"";
		}
	  else
	  { $rq=uc($&);
	    $_=$';

	    if ($rq eq LITERAL)
		{ $nntp->Out("$_\n");
		}
	    elsif ($rq eq MHEAD)
		{
		  for my $n ((new cs::Range $_)->Enum())
			{ $nntp->Out("HEAD $n\n");
			}
		}
	    elsif ($rq eq MBODY)
		{
		  for my $n ((new cs::Range $_)->Enum())
			{ $nntp->Out("BODY $n\n");
			}
		}
	    else
	    # pass through
	    { $nntp->Out("$rq $_\n");
	    }
	  }
	}

  exit 0;
}

sub Put
	{ my($this)=shift;

	  die "$::cmd: Put(@_) on type \"$this->{TYPE}\""
		if $this->{TYPE} ne MEATNNTP;

	  print {$this->{CHILD}} @_;
	}

sub _Reply
	{ my($this)=@_;
	  die "$::cmd: Reply(@_) on type \"$this->{TYPE}\""
		if $this->{TYPE} ne TOPLEVEL;

	  ::flush($this->{NNTP}->{CHILD});
	  $this->{NNTP}->{NNTP}->Reply();
	}

sub _Text
	{ my($this)=@_;
	  die "$::cmd: Reply(@_) on type \"$this->{TYPE}\""
		if $this->{TYPE} ne TOPLEVEL;

	  ::flush($this->{NNTP}->{CHILD});
	  $this->{NNTP}->{NNTP}->Text();
	}

sub Group(\%$)
	{ my($this,$group)=@_;

	  $this->{TOP}->Group($group) if $this->{TYPE} eq GROUP;

	  die "Group(@_) on type \"$this->{TYPE}\""
		if $this->{TYPE} ne TOPLEVEL;

	  my($nntp)=$this->{NNTP};

	  $nntp->Put("GROUP $group\n");

	  my($code,$text)=$this->_Reply();
	  return undef if $code !~ /^2/
		       || $text !~ /^\s*\d+\s+(\d+)\s+(\d+)/;

	  my($low,$high)=($1,$2);

	  my($G)=bless { TYPE	=> GROUP,
			 TOP	=> $this,
			 LOW	=> $low,
			 HIGH	=> $high,
		       }, cs::News;

	  $G;
	}

# return hash of n -> cs::RFC822
sub MultiHead
	{ my($this)=shift;

	  $this->{TOP}->MultiHead($this,@_) if $this->{TYPE} eq GROUP;

	  die "$::cmd: MultiHead($this,@_) on type \"$this->{TYPE}\""
		if $this->{TYPE} ne TOPLEVEL;

	  my($nntp)=$this->{NNTP};

	  my($r)=$this->GetRange(@_);

	  $nntp->Put("MHEAD ".$r->Text()."\n");

	  my($hits)={};

	  my($code,$text,$htext);

	  MHEAD:
	    for my $n ($r->Enum())
		{
		  ($code,$text)=$this->_Reply();
		  if (! defined $code)
			{ warn "$::cmd: eof from NNTP server\n";
			  last MHEAD;
			}

		  next MHEAD if $code !~ /^2/;

		  $htext=$this->_Text();
		  $hits->{$n}=new cs::RFC822 (new cs::Source (SCALAR,$htext));
		}

	  return $hits;
	}

# return hash of n -> bodytext
# if $fn supplied, call &$fn(n,bodytext,args)
# for each body fecthed, and return hash of n -> length(bodytext)
sub MultiBody
	{ my($this,$fn)=(shift,shift);

	  $this->{TOP}->MultiHead($this,@_) if $this->{TYPE} eq GROUP;

	  die "$::cmd: MultiHead($this,@_) on type \"$this->{TYPE}\""
		if $this->{TYPE} ne TOPLEVEL;

	  my($nntp)=$this->{NNTP};

	  my($r)=$this->GetRange(@_);

	  $nntp->Put("MBODY ".$r->Text()."\n");

	  my($hits)={};

	  my($code,$text,$btext);

	  MHEAD:
	    for my $n ($r->Enum())
		{
		  ($code,$text)=$this->_Reply();
		  if (! defined $code)
			{ warn "$::cmd: eof from NNTP server\n";
			  last MHEAD;
			}

		  next MHEAD if $code !~ /^2/;

		  $btext=$this->_Text();

		  if (defined $fn)
			{ &$fn($n,$btext,@_);
			  $hits->{$n}=length $btext;
			}
		  else	{ $hits->{$n}=$btext;
			}
		}

	  return $hits;
	}

sub GetRange
	{ my($this)=shift;

	  my($r)=new cs::Range;

	  for my $arg (@_)
		{ if (ref $arg)
			# presume cs::Range or [low,high]
			{ if (::reftype($arg) eq ARRAY)
				# presume [low,high]
				{ $r->Add($arg->[0],$arg->[1]);
				}
			  else
			  # presume a cs::Range ref
			  { for my $sub (@{$arg->SubRanges()})
				{ $r->Add($sub->[0],$sub->[1]);
				}
			  }
			}
		  else
		  # presume numeric range text
		  { for my $sub (@{(new cs::Range $arg)->SubRanges()})
			{ $r->Add($sub->[0],$sub->[1]);
			}
		  }
		}

	  $r;
	}

1;
