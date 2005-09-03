#!/usr/bin/perl
#
# A class to fetch data from things.
# Supports _only_ sequential reading or skipping.
# The base method to be overridden by subclasses is Read.
# The fields TYPE, BUF and POS are used.
#	- Cameron Simpson <cs@zip.com.au> 15may1996
#
# Added Seek() and Seekable().
# They may need overriding if you implement a seekable subclass.
#	- cameron, 29mar1997
#
# Added asynchronous interface.
#	- cameron, 19apr1997
#
# Added fetch call.
#	- cameron, 04jun1997
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::IO;

package cs::Source;

$cs::Source::_UseIO=$cs::IO::_UseIO;
$cs::Source::_BUFSIZ=8192;

if ($cs::Source::_UseIO)
{ ::need(IO);
  ::need(IO::File);
  ::need(IO::Handle);
  ::need(IO::Seekable);
}

sub fetch
{ my($s)=new cs::Source @_;
  return undef if ! defined $s;
  $s->Fetch();
}

sub open
{ new cs::Source (PATH,@_);
}

sub new
{ my($class,$type)=(shift,shift);
  my($this)={};
  my(@c)=caller;

  $this->{CALLER}=[ @c ];
  $this->{FLAGS}=0;
  $this->{POS}=0;
  $this->{BUF}='';

  if ($type eq ASYNC)
  { $this->{FLAGS}|=$cs::IO::F_ASYNC;
    $type=shift;
  }

  if ($type eq FILE)
  { my($FILE)=shift;
    # align real fd with FILE
    eval "stat $FILE" && -f _ && sysseek($FILE,tell($FILE),0);

    if ($cs::Source::_UseIO)
    {
      my($fd)=fileno($FILE);
      if (! defined $fd)
      { warn "$::cmd: fileno($FILE): $!";
	return undef;
      }

      ## warn "fd=$fd";
      $this->{IO}=new_from_fd IO::Handle ($fd,"r");
    }
    else
    { $this->{IO}=$FILE;
    }

    $this->{FLAGS}|=$cs::IO::F_NOCLOSE|$cs::IO::F_STICKYEOF;
  }
  elsif ($type eq TAIL)
  { my($path,$rewind)=@_;
    $rewind=0 if ! defined $rewind;

    my($io,$file)=_new_FILE($path,$rewind);
    return undef if ! defined $io;

    $this->{IO}=$io;
    $this->{POS}=($cs::Source::_UseIO
		  ? $io->tell()
		  : tell($io));
    $this->{PATH}=$path;
    $this->{REALPATH}=$file;	# debugging
    $type=FILE;
  }
  elsif ($type eq PATH)
  { my($path)=shift;
    if (! defined $path)
    { my(@c)=caller;
      die "\$path not set from [@c]";
    }

    my($io,$file)=_new_FILE($path,1,@_);
    return undef if ! defined $io;
    $this->{IO}=$io;
    $path->{PATH}=$path;
    $path->{REALPATH}=$file;	# debugging
    $type=FILE;
    $this->{FLAGS}|=$cs::IO::F_STICKYEOF;
  }
  elsif ($type eq PIPE)
  { my($pipefrom)=shift;

    if (ref $pipefrom)
    # assume subroutine ref
    {
      my($ds)=new cs::PipeDecode ($pipefrom,[ @_ ]);
      return undef if ! defined $ds;

      $this->{DS}=$ds;
      $type=Source;
    }
    else
    { my($pipeline)=" $pipefrom |";
      my($io);

      if ($cs::Source::_UseIO)
      # shell command
      { $io=new IO::File;
	return undef if ! $io->open(" $pipefrom |");
      }
      else
      { $io=cs::IO::mkHandle();
	return undef if ! CORE::open($io," $pipefrom |");
      }

      $this->{IO}=$io;
      $type=FILE;
    }
  }
  elsif ($type eq ARRAY)
  { $this->{ARRAY}=shift;
  }
  elsif ($type eq SCALAR)
  { my($scal)=shift;
    $this->{ARRAY}=[ ref($scal) ? $$scal : $scal ];
    $type=ARRAY;
  }
  elsif ($type eq Source)
  { $this->{DS}=shift;
  }
  else
  { warn "$::cmd: Source::new: unknown type \"$type\"";
    my@c=caller;warn "\tfrom[@c]";
    return undef;
  }

  $this->{TYPE}=$type;

  bless $this, $class;

  if ($cs::Source::_UseIO
   && exists $this->{IO}
   && ($this->{FLAGS}&$cs::IO::F_ASYNC))
  { cs::IO::selAddSource($this);
  }

  $this;
}

sub _new_FILE($;$$)
{ my($path,$rewind,$complex)=@_;
  $rewind=0 if ! defined $rewind;
  $complex=0 if ! defined $complex;

  my($f,@f);

  ::need(cs::IO);
  ($f,@f)=($complex
		? cs::IO::choose($path,$rewind ? undef : '')
		: $path);

  if (@f && ! $rewind)
  { warn "$::cmd: tried to tail \"$f\" [@f]";
    return undef;
  }

  my($io)=cs::IO::openR($f,@f);

  if (defined $io)
  { if (! $rewind)
    { if (! ($cs::Source::_UseIO
		    ? IO::Seekable::seek($io,0,2)
		    : sysseek($io,0,2)))
      { warn "$::cmd: sysseek($io,0,2): $!";
      }
    }

    return wantarray ? ($io,$f) : $io;
  }

  ##warn "openR($f,[@f]) failed";

  undef;
}

sub DESTROY
{ my($this)=@_;
  my($type)=$this->{TYPE};

  if (length $this->{BUF})
  # restore unprocessed data to downstream source
  { my($buf)=$this->_FromBuf();
    if ($type eq Source)
    { $this->{DS}->_PushBuf($buf);
    }
    elsif ($type eq ARRAY)
    {
      unshift(@{$this->{ARRAY}},$buf);
    }
  }

  if (! $cs::Source::_UseIO
   && $type eq FILE
   && ! ($this->{FLAGS}&$cs::IO::F_NOCLOSE))
  { close($this->{IO}) || warn "$::cmd: close($this->{IO}): $!";
  }
  else
  {
#	    warn "$::cmd: not try to close "
#		.cs::Hier::h2a($this)
#		.", made from [@{$this->{CALLER}}]"
#		if $type eq FILE;
  }
}

sub Handle	# return filehandle name
{ my($this)=@_;
  exists $this->{IO} ? $this->{IO} : undef;
}

sub Warn	# issue warning with context
{ my($this)=shift;
 
  my $type = $this->{TYPE};
  my $context="cs::Source($type)";
  if ($type eq FILE)
  {
    if (defined $this->{PATH})
    { $context.=": \"$this->{PATH}\"";
    }

    $context.=", line ".HANDLE->input_line_number($this->{IO});
  }

##  if ($this->{TYPE} eq ARRAY || $this->{TYPE} eq Source)
##    $type=ARRAY;
##      $type=FILE;
##      $type=Source;
##    $type=FILE;
##    $type=FILE;
##    $type=shift;

  warn "$::cmd: $context: @_";
}

sub Fetch
{ my($s)=@_;
  my(@a)=$s->GetAllLines();
  return @a if wantarray;
  join('',@a);
}

# skip forward in a source
# returns the number of bytes skipped
# or undef on error
# NB: hitting EOF gets a short skip (including zero), not error
# NB: an unspecified portion of the stream may have been read before an error
#
# works for any subclass provided BUF and TYPE are honoured
sub Skip
{ my($this,$n)=@_;
  my($on)=$n;		# $n gets used up

  local($_);

  # skip buffered data, if any
  if (length($_=$this->_FromBuf($n)))
  { $n-=length;

    # all from buffer
    return $on if $n == 0;
  }

  if ($this->Seekable())
  # a seekable thing
  { my($to)=$this->Tell()+$n;

    if (! $this->Seek($to))
    { warn "$::cmd: Skip($n): Seek($to): $!\n";
      return undef;
    }
  }
  elsif ($this->{TYPE} eq Source)
  # pass skip command downstream in case it's got an efficient
  # Skip method
  { $on=$this->{DS}->Skip($n);
  }
  else
  {
    my($rn);	# partial read size
    my($dummy);

    # we read in $_BUFSIZ chunks to avoid
    # malloc()ing obscene quantities of space
    # if someone asks to skip an immense void

    SKIP:
      while ($n > 0)
      { $rn=::min($n,$cs::Source::_BUFSIZ);
	$dummy=$this->Read($rn);
	return undef if ! defined $dummy;
	last SKIP if ! length $dummy;
	$n-=length($dummy);
      }

    $on-=$n;	# how much not skipped
  }

  return $on;
}

# works for any subclass
sub SkipTo
{ my($this,$pos)=@_;
  my($curr)=$this->Tell();

  if ($pos < $curr)
  { warn "$::cmd: SkipTo($pos): can't skip backwards!";
  }
  else
  { my($skipped)=$this->Skip($pos-$curr);

    return undef if ! defined $skipped;
  }

  $this->Tell();
}

sub Tell
{ shift->{POS};
}

sub Seekable
{ my($this)=shift;
  my($type)=$this->{TYPE};
  $type eq FILE && $this->{IO}->stat() && -f _
	|| $type eq Source && $this->{DS}->Seekable();
}

sub Seek
{ my($this,$where)=@_;
  warn "$::cmd: Seek(@_): where not defined: caller=".join('|',caller)
	if ! defined $where;

  if (! $this->Seekable())
  { warn "$::cmd: Seek($where) on ".cs::Hier::h2a($this,0);
    return undef;
  }

  my($type)=$this->{TYPE};
  my($retval);

  if ($type eq FILE)
  { my($io)=$this->{IO};
    if (! ($retval=($cs::Source::_UseIO
			  ? $io->seek($where,0)
			  : sysseek($io,$where,0))))
    { warn "$::cmd: seek($where,0): $!\n";
      return undef;
    }
  }
  elsif ($type eq Source)
  { if (! ($retval=$this->{DS}->Seek($where)))
    { return undef;
    }
  }
  else
  { warn "$::cmd: don't know how to Seek($where) on ".cs::Hier::h2a($this,0);
    return undef;
  }

  $this->{POS}=$where;
  $this->{BUF}='';

  return $retval;
}

sub PollIn
{ my($this,$size)=@_;
  $size=$this->ReadSize() if ! defined $size;

  my($n);
  local($_)='';

  my($io)=$this->{IO};

  $n=($cs::Source::_UseIO ? $io->sysread($_,$size) : sysread($io,$_,$size));
  return undef if ! defined $n;

  warn "$::cmd: n ($n) != length (".length($_).")"
	if $n != length($_);

  $this->_AppendBuf($_) if length;

  length;
}

sub HasData
{
  die "HasData(@_) when ! \$cs::Source::_UseIO" if ! $cs::Source::_UseIO;

  my($this)=@_;

  return 1 if length $this->{BUF};

  my($type)=$this->{TYPE};

  if ($type eq FILE)
  { my($io)=$this->{IO};
    $io->can_read(0);
  }
  elsif ($type eq Source)
  { $this->{DS}->HasData();
  }
  elsif ($type eq ARRAY)
  { @{$this->{ARRAY}};
  }
  else
  { warn "$::cmd: no HasData() method for cs::Source of type \"$type\"";
    0;
  }
}

sub ClearEOF
{ shift->{FLAGS}&=~$cs::IO::F_HADEOF;
}

sub Read
{ my($this,$size)=@_;

  $size=$this->ReadSize() if ! defined $size;

  my($type)=$this->{TYPE};
  local($_);
  my($n);

  # check for buffered data
  if (length ($_=$this->_FromBuf($size)))
  # pending data
  { ## warn "returned buffered data [$_]\n";
    return $_;
  }

  # nothing buffered, get data from the source

  # for some weird reason IO reseeks to 0 on EOF on SunOS,
  # hence this hack
  if ( ($this->{FLAGS}&($cs::IO::F_STICKYEOF|$cs::IO::F_HADEOF))
    == ($cs::IO::F_STICKYEOF|$cs::IO::F_HADEOF)
     )
  { return '';
  }

  if ($type eq FILE)
  { my($io)=$this->{IO};
    $n=($cs::Source::_UseIO
	  ? $io->sysread($_,$size)
	  : sysread($io,$_,$size));
    if (! defined $n)
    { warn "$::cmd: Source::Read($this($io),$size): $!";
      return undef;
    }

    ## warn "read $n bytes [$_]";

    # clear error flag if we hit EOF
    if ($n == 0)
    { 
#      warn "_UseIO=$cs::Source::_UseIO, io=[$io]";
#      { my $o = tied $io;
#	if ($o)
#	{ warn "$io is tied to ".cs::Hier::h2a($io,1);
#	}
#      }

      if ($cs::Source::_UseIO)	{ IO::Seekable::seek($io,0,1); }
      else			{ # sysseek($io,0,1);
				}

      $_='';
    }
  }
  elsif ($type eq ARRAY)
  { my($a)=$this->{ARRAY};

    return '' if ! @$a;		# EOF

    $_=shift(@$a);
    if (length > $size)
    { $this->_PushBuf(substr($_,$[+$size));
      substr($_,$[+$size)='';
    }
    ## warn "post Read a=[@$a], BUF=[$this->{BUF}]";
  }
  elsif ($type eq Source)
  { $_=$this->{DS}->Read($size);
    return undef if ! defined;
  }
  else
  { die "no cs::Source::Read method for type \"$type\"";
  }

  if (length)	{ $this->{POS}+=length; }
  else		{ $this->{FLAGS}|=$cs::IO::F_HADEOF;
		  ## warn "============= HADEOF ===============";
		  ## warn "FLAGS=".$this->{FLAGS};
		}

  $_;
}

sub NRead
{ my($this,$n)=@_;
  local($_);
  my($rd,$rn);

  while ($n > 0)
  { $rn=::min($n,$this->ReadSize());
    $rd=$this->Read($rn);
    return undef if ! defined $rd;	# error
    return $_ if ! length $rd;		# EOF
    $_.=$rd;
    $n-=length $rd;
  }

  $_;
}

# suggest a size for the next read
sub ReadSize
{ my($this)=@_;
  my($type)=$this->{TYPE};

  # return pending size
  if (length $this->{BUF})
  { return length $this->{BUF};
  }

  my($size)=$cs::Source::_BUFSIZ;

  if ($type eq FILE)
  {}
  elsif ($type eq ARRAY)
  { if (@{$this->{ARRAY}})
    { $size=length ${$this->{ARRAY}}[$[];
    }
  }
  elsif ($type eq SCALAR)
  { $size=length ${$this->{SCALAR}};
  }
  elsif ($type eq Source)
  { $size=$this->{DS}->ReadSize();
  }

  $size;
}

sub _FromBuf($;$)
{ my($this,$n)=@_;

  $n=length $this->{BUF}
	if ! defined $n
	|| $n > length($this->{BUF});

  local($_)=substr($this->{BUF},$[,$n);

  substr($this->{BUF},$[,$n)='';
  $this->{POS}+=length;
  
  ## warn "_FROMBUF=[$_]";

  $_;
}

sub _PushBuf($$)
{ my($this,$data)=@_;
  
  ## {my(@c)=caller;warn "_PUSHBUF=($data) from [@c]";}

  substr($this->{BUF},$[,0)=$data;
  $this->{POS}-=length $data;
}

sub _AppendBuf($$)
{ my($this,$data)=@_;
  
  ## warn "_APPENDBUF=($data)";

  $this->{BUF}.=$data;
  $this->{POS}-=length $data;
}

# get a line
# return undef on error, '' on EOF, line-with-newline otherwise
sub GetLine
{ my($this)=shift;
  my($i);
  local($_);	# the line

  # check for line in the buffer
  if (($i=index($this->{BUF},"\n")) >= $[)
  { return $this->_FromBuf($i-$[+1);
  }

  # hmm - buffer has incomplete line
  # fetch entire buffer so that calls to read
  # return new data
  # we will push the unused stuff back when we're done

  my($buf)=$this->_FromBuf();

  # loop getting data until we hit EOF or a newline
  while (defined ($_=$this->Read()) && length)
  {
    if (($i=index($_,"\n")) >= $[)
    # line terminator
    { $buf.=substr($_,$[,$i-$[+1);
      $this->_PushBuf(substr($_,$[+$i+1));	# save for later
      return $buf;
    }

    $buf.=$_;
  }

  # EOF or error
  # save unprocessed data
  $this->_PushBuf($buf);

  return '' if defined;	# EOF

  undef;		# error
}

sub GetAllLines
{ my($this)=shift;
  my(@a);
  local($_);

  while (defined ($_=$this->GetLine()) && length)
      { push(@a,$_);
      }

  wantarray ? @a : join('',@a);
}

sub GetContLine
{ my($this,$contfn)=@_;
  $contfn=sub { $_[0] =~ /^[ \t]/ } if ! defined $contfn;

  my($cline)=$this->GetLine();
  return undef if ! defined($cline) || ! length($cline);

  local($_);

  CONT:
    while (defined ($_=$this->GetLine()) && length)
	{ last CONT if ! &$contfn($_);
	  $cline.=$_;
	}

  # push back unwanted line
  if (defined && length)
	{ $this->_PushBuf($_);
	}

  $cline;
}

# collect whole file
# (well, to first EOF mark)
sub Get
{ my($this,$toget)=@_;
  local($_);
  my($got)='';

  my($limited)=defined $toget;

  ## warn "Get(@_): limited=$limited, toget=$toget";

  while ((! $limited || $toget > 0)
      && defined ($_=$this->Read($toget)) && length)
  { $got.=$_;
    $limited && ($toget-=length);

    ## warn "got[$_], length=".length($got);
  }

  ## warn "got=[$got]";

  $got;
}

sub UnGet
{ my($this)=shift;

  for (reverse @_)
  { $this->_PushBuf($_);
  }
}

sub CopyTo
{ my($this,$sink,$max)=@_;
  my($copied)=0;
  local($_);

  COPY:
  while ((! defined $max || $max > 0)
      && defined ($_=$this->Read(defined $max
				      ? $max
				      : $this->ReadSize()))
      && length)
  { $copied+=length;
    if (! $sink->Put($_))
    { warn "$::cmd: CopyTo: Put fails";
      last COPY;
    }
    ## warn "copied [$_]";
  }

  $copied;
}

# duplicate a source - consumes the original, so returns 2 copies
sub Dup	# (source) -> (copy1, copy2)
{
  my($this)=@_;

  my($c1,$c2);

  $c1=$this->Get();
  $c2=$c1;

  ((new cs::Source (SCALAR,\$c1)),
   (new cs::Source (SCALAR,\$c2))
  );
}

# link Dup, this consumes the source, so we return (FILE,copy)
# in an array context; in a scalar context the source is lost
sub DupToFILE
{ my($this)=@_;
  my($FILE)=cs::IO::tmpfile();

  my($c1);

  if (wantarray)
  { my($c1);

    $c1=$this->Get();
    print $FILE $c1;
    sysseek($FILE,0,0) || warn "$::cmd: rewind(tmpfile): $!";

    return ($FILE,(new cs::Source (SCALAR,\$c1)));
  }

  ::need(cs::Sink);
  my($sink)=new cs::Sink (FILE,$FILE);

  $this->CopyTo($sink);

  sysseek($FILE,0,0) || warn "$::cmd: rewind(tmpfile): $!";

  return $FILE;
}

sub PipeThru($$)
{ my($this,$shcmd)=@_;

  my $pid;

  my $pr = cs::IO::mkHandle();
  my $pw = cs::IO::mkHandle();
  pipe($pr,$pw) || die "$::cmd: pipe(): $!";

  $pid=fork();
  die "$::cmd: fork(): $!" if ! defined $pid;
  if ($pid > 0)
  # mainline program - just read from pipe
  {
    close($pw) || warn "$::cmd: close($pw): $!";
    return new cs::Source(FILE,$pr);
  }

  # child - fork into filter and feeder
  $::cmd="$::cmd: PipeThru-child";
  close($pr) || warn "$::cmd: close($pr): $!";
  CORE::open(STDOUT,">&$pw") || warn "$::cmd: dup($pw,STDOUT): $!";
  close($pw) || warn "$::cmd: close($pw): $!";

  pipe($pr,$pw) || die "$::cmd: pipe(): $!";

  $pid=fork();
  die "$::cmd: fork(): $!" if ! defined $pid;
  if ($pid > 0)
  # immediate child - exec shell command
  {
    close($pw) || warn "$::cmd:: close($pw): $!";
    CORE::open(STDIN,"<&$pr") || die "$::cmd:: dup($pr,STDIN): $!";
    close($pr) || warn "$::cmd: close($pr): $!";
    exec('/bin/sh','-c',$shcmd);
    die "$::cmd: exec(sh -c $shcmd): $!";
  }

  # grandchild - copy $this to pipe
  $::cmd="$::cmd: PipeThru-grandchild";
  close($pr) || warn "$::cmd: close($pr): $!";
  close(STDOUT) || warn "$::cmd: close(STDOUT): $!";
  close(STDIN) || warn "$::cmd: close(STDIN): $!";
  $this->CopyTo(new cs::Sink(FILE,$pw));
  exit(0);
}

1;
