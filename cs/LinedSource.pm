#!/usr/bin/perl
#
# A class to fetch lines of data from things.
# You can tie an array to something with
#   tie \@array, cs::LinedSource, new-args...
#	- Cameron Simpson <cs@zip.com.au> 15may96
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Source;

package cs::LinedSource;


@cs::LinedSource::ISA=();
$cs::LinedSource::DfltCacheSize=128;

sub new
	{ my($class)=shift;
	  my($s)=new cs::Source (@_);

	  return undef if ! defined $s;

	  if (! $s->Seekable())
		{ warn "bailing - can't Seek() on ".cs::Hier::h2a($s,0);
		  return undef;
		}

	  bless { POS		=> [],	# line offsets
		  CACHE		=> {},	# lineno => text
		  CACHED	=> [],	# lineno...
		  CACHEMAX	=> $DfltCacheSize,
		  DS		=> $s,
		  LINE		=> 0,	# current line #
		  LINES		=> 0,	# lines recorded
		};
	}

sub TIEARRAY
	{ new(@_);
	}
sub FETCH
	{ GetLine(@_);
	}
sub STORE
	{ die "can't STORE(@_) to a LinedSource";
	}

sub LinePos
	{ my($this,$lineno)=@_;
	  warn "lineno not defined" if ! defined $lineno;
	  warn "only ".scalar(@{$this->{POS}})." lines noted - $lineno out of range"
		if $lineno >= @{$this->{POS}};
	  warn "POS[$lineno] not defined" if ! defined $this->{POS}->[$lineno];
	  $this->{POS}->[$lineno];
	}

sub GetLine
	{ my($this,$lineno)=@_;
	  $lineno=$this->{LINE} if ! defined $lineno;

	  local($_);

	  if ($lineno < $this->{LINES})
		# previously seen line
		{ if (defined $this->{CACHE}->{$lineno})
			# take straight from cache, adjust nothing
			{ $_=$this->{CACHE}->{$lineno};
			}
		  elsif (! $this->{DS}->Seek($this->LinePos($lineno)))
			# failed seeks leave us inconsistent - bail
			{ die "seek fails";	# ; this=".cs::Hier::h2a($this,1);
			}
		  else
		  # ok, fetch and cache line
		  { $this->{LINE}=$lineno;
		    $_=$this->_FetchCurrentLine();
		  }
		}
	  else
	  { if ($this->{LINE} < $this->{LINES})
		# skip to furthest point
		{ if (! $this->{DS}->Seek($this->LinePos($this->{LINES}-1)))
			{ die "seek fails: this=".cs::Hier::h2a($this,0);
			}

		  $this->{LINE}=$this->{LINES};
		}

	    # roll forward until we've fetched the desired line
	    while ($lineno >= $this->{LINES})
		{ return undef if ! defined ($_=$this->_FetchCurrentLine())
			       || ! length;
		}
	  }

	  $_;
	}

sub _FetchCurrentLine
	{ my($this)=@_;
	  local($_);

	  warn "_FetchCurrentLine() of cached line ($this->{LINE})"
		if defined $this->{CACHE}->{$this->{LINE}};

	  # note start of line
	  if ($this->{LINE} == $this->{LINES})
		{ $this->{POS}->[$this->{LINE}]=$this->{DS}->Tell();
		  $this->{LINES}++;
		}

	  return undef if ! defined ($_=$this->{DS}->GetLine())
		       || ! length;

	  # purge cache overflow
	  while (@{$this->{CACHED}} >= $this->{CACHEMAX})
		{ delete $this->{CACHE}->{shift(@{$this->{CACHED}})};
		}

	  # cache line
	  $this->{CACHE}->{$this->{LINE}}=$_;
	  push(@{$this->{CACHED}},$this->{LINE});

	  # advance line counter for read
	  $this->{LINE}++;

	  $_;
	}

1;
