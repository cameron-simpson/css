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

=head1 NAME

cs::Sink - a data sink

=head1 SYNOPSIS

use cs::Sink;

=head1 DESCRIPTION

The cs::Sink module provides generic data sink facilities.
B<cs::Sink>s may be created which wire to a variety of objects.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

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

=head1 GENERAL FUNCTIONS

=over 4

=item put(I<sink-args>, I<data...>)

Creates a new B<cs::Sink> using the arguments
in the array references by I<sink-args>
and writes the I<data> to it.
Returns B<undef> on error.

=cut

sub put
{ my($args)=shift;
  my($s)=new cs::Sink @$args;

  return undef if ! defined $s;

  $s->Put(@_);
}

=back

=head1 OBJECT CREATION

=over 4

=item open(I<path>)

Create a new sink attached to the file named in I<path>.

=cut

sub open
{ new cs::Sink (PATH,@_);
}

=item new cs::Sink (I<type>,I<args...>)

Create a new sink of the specified I<type>.
I<args...> varies according to the type:

=over 6

=cut

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

=item B<FILE>, I<handle>

Attach to the filehandle I<handle>.
Flushes any pending output in I<handle> as a side-effect.

=cut

  if ($type eq FILE)
  { my($FILE)=shift;
    ::flush($FILE);
    $this->{IO}=($cs::Sink::_UseIO
		  ? new_from_fd IO::Handle (fileno($FILE),"w")
		  : $FILE);
    $this->{FLAGS}|=$cs::IO::F_NOCLOSE;
    $this->{FILE}=$FILE;
  }

=item B<APPEND>, I<path>

Attach to the file named by I<path> in append mode.

=cut

  elsif ($type eq APPEND)
  { my($path,$complex)=@_;
    $complex=0 if ! defined $complex;
    my($io,$Fpath)=_new_FILE($path,1,$complex);
    return undef if ! defined $io;
    $this->{IO}=$io;
    $this->{PATH}=$Fpath;	# debugging
    $type=FILE;
  }

=item B<PATH>, I<path>

Attach to the file named in I<path> in rewrite mode.

=cut

  elsif ($type eq PATH)
  { my($path,$complex)=@_;
    $complex=0 if ! defined $complex;
    my($io,$Fpath)=_new_FILE($path,0,$complex);
    return undef if ! defined $io;
    $this->{IO}=$io;
    $this->{PATH}=$Fpath;	# debugging
    $type=FILE;
  }

=item B<PIPE>, I<shcmd>

Attach to a pipe to the shell command I<shcmd>.

=cut

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

=item B<ARRAY>, I<arrayref>

Attach to the array referenced by I<arrayref>.
Each B<Write()> to the sink pushes a single string
onto the array.

=cut

  elsif ($type eq ARRAY)
  { $this->{ARRAY}=shift;
  }

=item B<SCALAR>, I<scalarref>

Attach to the scalar referenced by I<scalarref>.
Each B<Write()> appends to the scalar.

=cut

  elsif ($type eq SCALAR)
  { $this->{SCALAR}=shift;
  }

=item B<Sink>, I<sink>

Attach to the B<cs::Sink> object I<sink>.
Typically used by subclasses
to apply a filter to data before depositing in I<sink>.

=cut

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

=back

=item tmpSink(I<tmpnam-args>)

Create a new sink object attached to a new temporary file
allocated by B<cs::Pathname::tmpnam(I<tmpnam-args>)>.

=cut

sub tmpSink
{
  ::need(cs::Pathname);
  new cs::Sink (PATH, cs::Pathname::tmpnam(@_));
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

=back

=head1 OBJECT METHODS

=over 4

=item Path()

For sinks attached to files,
return the pathname of the file.

=cut

sub Path($)
{ my($this)=shift;
  return undef if ! exists $this->{PATH};
  $this->{PATH};
}

=item Handle()

For sinks attached to files or filehandles,
return the filehandle.

=cut

sub Handle($)
{ my($this)=@_;
  exists $this->{IO} ? $this->{IO} : undef;
}

=item Put(I<data...>)

Write all the I<data> to the sink.

=cut

sub Put
{ my($this)=shift;
  my($alln)=0;
  my($n);

  my($data)=join('',@_);

  WRITE:
  while (length $data)
  { $n=$this->Write($data);
    if (! defined $n)
    { my@c=caller;
      warn "$::cmd: write error (possibly $!), aborting with\n\t\t[$data]\n\tunwritten\n\tfrom [@c]";
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
      warn "print $io \$datum: errno=$!";
    }
##	  # XXX - IO module doesn't like zero sized writes
##	  if ($n > 0 || ! $cs::Sink::_UseIO)
##	  	{ $n=($cs::Sink::_UseIO
##			? $io->syswrite($datum,$n)
##			: $this->{FLAGS}&$cs::IO::F_RAWWRITES
##				? syswrite($io,$datum,$n)
##				: (print $io $datum)
##					? $n : undef);
##		}
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

  if (! defined $n)
  { my@c=caller;
    warn "\$n undef! type=[$type] FILE=[$this->{FILE}]\n\tfrom [@c]";
    return undef;
  }

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
  { warn "$::cmd: operation Flush not supported on cs::Sink/$type objects";
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

=back

=head1 SEE ALSO

cs::Source(3), cs::Pathname(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
