#!/usr/bin/perl
#
# Multi-I/O: play games with select() to handle multiple input/output streams
# efficiently.	- Cameron Simpson <cs@zip.com.au>, 29aug94
#
# A stream is essentially a buffer with a method to add data and retrieve
# data. The buffer may be either a flat character buffer, in which case
# packet sizes are discarded, or an array of packets (scalars).
# Each stream is represented by a record as follows:
#	Packets		Boolean
#	Buffer		[] or scalar depending on Packets
# Optional fields include:
#	Info		Auxiliary state information.
#	inFILE,outFILE	Most streams represent a filehandle, active for read or
#			write. These specify the name of the filehandle.
#			It is assumed that any filehandle so named can
#			be used with select().
#	Received	Ref to subroutine(stream,datum) for notication that
#			data have been queued.
#	Writable	Callback: Whether this stream's target will accept data.
#			0 -> No.
#			>0 -> Max likely to be accepted.
#	Readable	Callback: Whether this stream's source has data.
#			Note: _not_ the size of the stream's queue.
#			0 -> No.
#			>0 -> Amount present for read.
#
# Calls to this module include:
#  Send Datum
#	Queue Datum on the stream. It will always be accepted.
#	If present, the Received callback will be called.
#  Read	Try to read data from the source. May block if not used in concert with
#	the Readable() call.
#  Write Try to write data to the target. May block
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Net;
use TCP;
use UDP;
require 'cs/package.pl';

package cs::Multio;

sub _min	{ $_[0] < $_[1] ? $_[0] : $_[1]; }
sub _max	{ $_[0] > $_[1] ? $_[0] : $_[1]; }

$cs::Multio::IOSIZE=8192;

# active streams
@cs::Multio::Streams=();

map($SettableViaNew{$_}=1,Info,Packets,inFILE,outFILE);

sub new
	{ my(%attrs)=@_;
	  my($this)={};

	  # rig defaults
	  %$this=(	Packets => 0,
			IOSize  => $IOSIZE
	         );

	  # set attributes as supplied
	  for (keys %attrs)
		{ if (! $SettableViaNew{$_})
			{ print STDERR "$'cmd: Multio'new: not allowed to set attribute \"$_\", discarded\n";
			}
		  else
		  { $this->{$_}=$attrs{$_};
		  }
		}

	  $this->{'Buffer'}=($this->{'Packets'} ? [] : '');

	  push(@Streams,$this);

	  bless $this;
	}

sub old
	{ my($this)=@_;

	  if ($this->{'Packets'})
		{ if (@{$this->{'Buffer'}})
			{ print STDERR("$'cmd: warning: discarding stream with queued packets: [",
				join('|',@{$this->{'Buffer'}}),"]\n");
			}
		}
	  elsif (length $this->{'Buffer'})
		{ print STDERR("$'cmd: warning: discarding stream with queued data: [${$this->{'Buffer'}}]\n");
		}

	  Stream:
	    for (0..$#Streams)
		{ if ($this == $Streams[$_])
			{ splice(@Streams,$_,1);
			  last Stream;
			}
		}
	}

# queue data onto stream
sub Send
	{ my($this,$datum)=@_;

	  die "$'cmd: Multio'Send: not a scalar" if ref $datum;

	  if ($this->{'Packets'})	{ push(@{$this->{'Buffer'}},"$datum"); }
	  else				{ ${$this->{'Buffer'}}.=$datum; }

	  if (defined $this->{'Received'})
		{ &{$this->{'Received'}}($this,$datum);
		}
	}

# fetch data from stream source, return amount fetched or undef on error
sub Read
	{ my($this)=@_;

	  if (defined $this->{'Read'})
		{ &{$this->{'Read'}}($this);
		}
	  elsif (defined $this->{inFILE})
		{ my($data,$n);

		  return undef if ! defined($n=sysread($this->{inFILE},
						       $data,
						       $this->{'IOSize'}));
		  $this->Send($data);

		  $n;
		}
	  else
	  { print STDERR "$'cmd: Multio'Read: no read method defined\n";
	    undef;
	  }
	}

# dispatch data from stream to target, return amount dispatched
# or undef on error
sub Write
	{ my($this,$iosize)=@_;

	  if (! defined $iosize)
		{ if (defined $this->{'Writable'})
			{ $iosize=&{$this->{'Writeable'}}($this);
			}
		  else
		  { $iosize=$this->{'IOSize'};
		  }
		}

	  my($w)=&_writable($this);

	  if ($iosize > $w)
		{ $iosize=$w;
		}

	  return 0 if $iosize < 1;

	  my($n);

	  if (defined $this->{'Write'})
		{ $n=&{$this->{'Write'}}($this,$iosize);
		}
	  elsif (defined $this->{'outFILE'})
		{ $n=syswrite($this->{'outFILE'},
			      ($this->{'Packets'}
				? ${$this->{'Buffer'}}[0]
				: substr($this->{'Buffer'},0,$iosize)
			      ), $iosize);
		}
	  else
	  { print STDERR "$'cmd: Multio'Write($iosize): no write method defined\n";
	    return undef;
	  }

	  return undef if ! defined $n;

	  if ($this->{'Packets'})
		{ if ($n == length(${$s->{'Buffer'}}[0]))
			{ shift(@{$s->{'Buffer'}});
			}
		  else
		  { substr(${$this->{'Buffer'}}[0],0,$n)='';
		  }
		}
	  else
	  { substr($this->{'Buffer'},0,$n)='';
	  }

	  # XXX - callback for write?

	  $n;
	}

# return size of available writable data in the queue, for a single write
sub _writable
	{ my($this)=@_;
	  
	  if ($this->{'Packets'})
		{ @{$this->{'Buffer'}}
			? length(${$this->{'Buffer'}}[0])
			: 0;
		}
	  else
	  { length ${$this->{'Buffer'}};
	  }
	}

$cs::Multio::PollTick=1;	# manual poll every second
sub poll	# (timeout[,stream-refs]) -> callbacks-made or undef
	{ my($timeout)=shift;
	  my($calltime)=time;	# checkpoint forr interleaving
	  my($pause);		# 
	  my($vr,$vw);		# vectors for select

	  my(@rfd2stream,@wfd2stream);

	  my($maxr,$maxw);
	  my($nrsel,$nwsel);
	  my($active,$n,$fd);

	  Loop:
	    while (1)
		{
		  # collate interesting streams
		  @rfd2stream=();
		  @wfd2stream=();
	  	  ($maxr,$maxw)=(0,0);
	  	  ($nrsel,$nwsel)=(0,0);
		  ($vr,$vw)=('','');

		  $active=0;

		  for (@Streams)
			{ if (defined $_->{'Readable'})
				# check via callback
				{ $_->Read && $active++;
				}
			  elsif (defined $_->{'inFILE'})
				# check via select
				{ $fd=fileno($_->{'inFILE'});
		  		  vec($vr,$fd,1)=1;
				  $rfd2stream[$fd]=$_;
				  $maxr=&_max($maxr,$fd);
				  $nrsel++;
				}

			  if (&_writable($_) < 1)
				# no data? forget it
				{}
			  elsif (defined $_->{'Writable'})
				# check via callback
				{ $_->Write && $active++;
				}
			  elsif (defined $_->{'outFILE'})
				# check via select
				{ $fd=fileno($_->{'outFILE'});
		  		  vec($vw,$fd,1)=1;
				  $wfd2stream[$fd]=$_;
				  $maxw=&_max($maxr,$fd);
				  $nwsel++;
				}
			}

		  if ($active)
			{ $pause=0; }
		  elsif (@vr || @vw)
			{ $pause=&_max($PollTick,$timeout); }
		  else
			{ $pause=$timeout; }

	  	  $n=select($vr,$vw,undef,$pause);

		  for $fd (0..$maxr)
			{ if (vec($vr,$fd,1))
				{ $rfd2stream[$fd]->Read && $active++;
				}
			}

		  for $fd (0..$maxw)
			{ if (vec($vw,$fd,1))
				{ $wfd2stream[$fd]->Write && $active++;
				}
			}

		  last Loop if $active || $calltime+$timeout <= time;
		}

	  return $active;
	}

sub Copy
	{ my($FROM,@TO)=@_;

	  &'Abs($FROM);
	  for (@TO)
		{ &'Abs($_);
		}

	  my($info)=[];

	  @$info=@TO;

	  XXXXXXX XXXX
	}

1;
