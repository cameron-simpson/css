#!/usr/bin/perl
#
# A Sink object.
# This is an object to which data may be written, with backends to
# pass the data to several types of downstream object:
#	FILE	A absolute filehandle.
#	APPEND	A file opened for append. This becomes a FILE.
#	PATH	A file opened for write. This becomes a FILE.
#	PIPE	A shell pipeline to open.
#	ARRAY	A reference to an array. Data are push()ed onto it.
#	SCALAR	A reference to a scalar. Data are appened to it.
#	Sink	Another Sink-like object.
# A Sink does _not_ provide an inherent buffering.
# XXX: this may change with the async extensions.
#
# Methods:
# new type args
#	Make a new cs::Sink of one of the types above.
#	Args are:
#	  FILE		Reference to a FILE.
#	  APPEND	Pathname.
#	  PATH		Pathname.
#	  ARRAY		Reference to an array.
#	  SCALAR	Reference to a scalar.
#	  Sink		Reference to a Sink.
#	Returns undef on failure.
# Flush
#	Call the Flush method of the downstream object, if any.
# Write data
#	Call the Write method of the downstream object.
#	Returns the number of bytes written, or undef on error.
#	NOTE: if less than the full data are written, the caller
#	      must be prepared to deal with the unwritten portion.
# Put @data
#	Iterate over Write until all items in @data are written.
# Suck [n]
#	Called by the downstream object if it wants "free" data.
#	This is primarily to assist asynchronous I/O.
#	At most n bytes of data are returned, if n > 0.
#	It will call the upstream object's Suck routine at need
#	if one is registered.
# SuckFrom ref
#	Register ref as the upstream object whose Suck method can
#	be called from "free" data.
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::IO;

require 'flush.pl';

package cs::Sink;

$cs::Sink::_UseIO=$cs::IO::_UseIO;

if ($cs::Sink::_UseIO)
{ ::need(IO);
  ::need(IO::File);
  ::need(IO::Handle);
}

sub put
{ my($args)=shift;
  my($s)=new cs::Sink @$args;

  return undef if ! defined $s;

  $s->Put(@_);
}

sub open
{ new cs::Sink (PATH,@_);
}

sub new
{ my($class,$type)=(shift,shift);
  my($this)={};
  my(@c)=caller;

  ## main::carp "new Sink (class=$class, type=$type, @=[@_])";

  $this->{CALLER}=[ @c ];
  $this->{FLAGS}=0;
  $this->{BUF}='';	# only used in asynchronous mode

  if ($type eq ASYNC)
  { $this->{FLAGS}|=cs::IO::F_ASYNC;
    $type=shift;
  }

  if ($type eq FILE)
  { my($FILE)=shift;
    ::flush($FILE);
    $this->{IO}=($cs::Sink::_UseIO
		  ? new_from_fd IO::Handle (fileno($FILE),"w")
		  : $FILE);
    $this->{FLAGS}|=$cs::IO::F_NOCLOSE;
  }
  elsif ($type eq APPEND)
  { my($path,$complex)=@_;
    $complex=0 if ! defined $complex;
    my($io,$Fpath)=_new_FILE($path,1,$complex);
    return undef if ! defined $io;
    $this->{IO}=$io;
    $this->{PATH}=$Fpath;	# debugging
    $type=FILE;
  }
  elsif ($type eq PATH)
  { my($path,$complex)=@_;
    $complex=0 if ! defined $complex;
    my($io,$Fpath)=_new_FILE($path,0,$complex);
    return undef if ! defined $io;
    $this->{IO}=$io;
    $this->{PATH}=$Fpath;	# debugging
    $type=FILE;
  }
  elsif ($type eq PIPE)
  { my($pipeline)="| ".shift(@_)." ";
    my($io);

    $io=($cs::Sink::_UseIO ? new IO::File : cs::IO::mkHandle());
    return undef if ! ($cs::Sink::_UseIO
			  ? $io->open($pipeline)
			  : CORE::open($io,$pipeline));
    $this->{IO}=$io;
    $type=FILE;
  }
  elsif ($type eq ARRAY)
  { $this->{ARRAY}=shift;
  }
  elsif ($type eq SCALAR)
  { $this->{SCALAR}=shift;
  }
  elsif ($type eq Sink)
  { $this->{DS}=shift;
  }
  else
  { warn "cs::Sink::new: unknown type \"$type\"";
    return undef;
  }

  $this->{TYPE}=$type;

  bless $this, $class;

  if (exists $this->{IO} && ($this->{FLAGS}&$cs::IO::F_ASYNC))
  { cs::IO::selAddSink($this);
  }

  $this;
}

$cs::Sink::_new_FILE_first=1;
sub _new_FILE
{ my($path,$append,$complex)=@_;
  $complex=0 if ! defined $complex;

  ## warn "Sink::_new_FILE(@_) ...\n";

  my($f,@f);

  # real path, filter list
  ($f,@f)=($complex
		? cs::IO::choose($path,$append ? '' : undef)
		: $path);

  if ($append && @f)
	{ warn "tried to append to \"$f\" [@f]";
	  return undef;
	}

  my($io)=cs::IO::openW($append,$f,@f);

  defined $io
	? wantarray ? ($io,$f) : $io
	: wantarray ? (undef,undef) : undef;
}

sub DESTROY
{ _DESTROY(@_);
}
sub _DESTROY
{ my($this)=shift;
  return if ! exists $this->{TYPE};	# already destroyed
  my($type)=$this->{TYPE};

  ## warn "Sink::DESTROY($this)\n";
  $this->Flush();

  if (! $cs::Sink::_UseIO
   && $type eq FILE
   && ! ($this->{FLAGS} & $cs::IO::F_NOCLOSE))
	{ ## warn "CLOSE($this->{IO})";
	  close($this->{IO})
		|| warn "$::cmd: close($this->{IO}): $!";
	}
  else
  { ## warn "$::cmd: not try to close ".cs::Hier::h2a($this).", made from [@{$this->{CALLER}}]";
  }

  delete $this->{TYPE};
}

sub Path
{ my($this)=shift;
  return undef if ! exists $this->{PATH};
  $this->{PATH};
}

sub Handle	# return filehandle name
{ my($this)=@_;
  exists $this->{IO} ? $this->{IO} : undef;
}

sub Put
{ my($this)=shift;
  my($alln)=0;
  my($n);

  my($data)=join('',@_);

  WRITE:
    while (length $data)
    { $n=$this->Write($data);
      if (! defined $n)
	    { warn "$::cmd: write error (possibly $!), aborting with [$data] unwritten";
	      return undef;
	    }

      $alln+=$n;
      substr($data,$[,$n)='';
    }

  $alln;
}

sub PollOut
{ my($this)=@_;

  return 0 if ! length $this->{BUF};

  my($n);
  local($_)='';

  $n=$this->{IO}->syswrite($this->BUF,length($this->{BUF}));
  return undef if ! defined $n;

  substr($this->{BUF},$[,$n)='';

  length;
}

sub Write
{ my($this,$datum)=@_;
  my($type)=$this->{TYPE};
  my($io)=$this->{IO};
  my($n)=length $datum;

  if ($type eq FILE)
	{
	  die "UseIO is back on!" if $cs::Sink::_UseIO;
	  if (! print $io $datum)
		{ undef $n;
		}
##		  # XXX - IO module doesn't like zero sized writes
##		  if ($n > 0 || ! $cs::Sink::_UseIO)
##		  	{ $n=($cs::Sink::_UseIO
##				? $io->syswrite($datum,$n)
##				: $this->{FLAGS}&$cs::IO::F_RAWWRITES
##					? syswrite($io,$datum,$n)
##					: (print $io $datum)
##						? $n : undef);
##			}
	}
  elsif ($type eq Sink)
	{ $n=$this->{DS}->Write($datum);
	}
  elsif ($type eq ARRAY)
	{ push(@{$this->{ARRAY}},$datum);
	}
  elsif ($type eq SCALAR)
	{ ${$this->{SCALAR}}.=$datum;
	}

  return undef if ! defined $n;

  $n;
}

sub Flush
{ my($this)=shift;
  my($type)=$this->{TYPE};

  if ($type eq FILE)
  {
    ::flush($this->{IO});
  }
  elsif ($type eq ARRAY || $type eq SCALAR)
  {}
  elsif ($type eq Sink)
  { $this->{DS}->Flush();
  }
  else
  { warn "$::cmd: operation Flush not supported on Sink/$type objects";
  }
}

# take note of where to suck from
sub SuckFrom
{ my($this,$src)=@_;

  $this->{SUCKFROM}=$src;
}

# retrieve any "free" data
# this is the SUCKFROM callback from the downstream module
sub Suck
{ my($this,$n)=@_;
  my($type)=$this->{TYPE};
  my($datum)='';

  if ($type eq FILE || $type eq Sink)
  {}
  elsif ($type eq ARRAY)
  { $datum=shift(@{$this->{ARRAY}})
	  if @{$this->{ARRAY}};
  }
  elsif ($type eq SCALAR)
  { my($len)=length ${$this->{SCALAR}};

    if ($n == 0 || $n > $len)
    { $n=$len;
    }

    $datum=substr(${$this->{SCALAR}},$[,$n);
    substr(${$this->{SCALAR}},$[,$n)='';
  }
  else
  { cs::Upd::err("operation Suck not supported on Sink/$type objects\n");
    $datum=undef;
  }

  # no free data, check for some upstream
  if (! length $datum && defined $this->{SUCKFROM})
  { $datum=$this->{SUCKFROM}->Suck($n);
  }

  # if the free datum is too big, cut it back
  if ($n > 0 && length $datum > $n)
  { my($buf)=substr($datum,$[+$n);
    substr($datum,$[+$n)='';
    $this->_Blow($buf);
  }

  $datum;
}

# push data back onto the stream for later
sub _Blow
{ my($this,$datum)=@_;
  my($type)=$this->{TYPE};

  if ($type eq ARRAY){ unshift(@{$this->{ARRAY}},$datum); }
  else
  { die "no unsuck op for Sink/$type";
  }
}

sub tmpSink
{
  ::need(cs::Pathname);
  new cs::Sink (PATH, cs::Pathname::tmpnam(@_));
}

1;
