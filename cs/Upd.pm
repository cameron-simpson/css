#!/usr/bin/perl
#
# Simple line update routine; assumes \r and \b
# do carriage return and non-destructive backspace.
# Assumes 8 character tabs.
#	- Cameron Simpson, cs@zip.com.au, DoD#743
#
# Added tput code and \r optimisation. - Cameron, 08dec93
# Made into a module.		       - Cameron, 15may96
#
# new cs::Upd [FILE]
#	Creates a new cs::Upd object attached to FILE (or STDOUT if no FILE
#	specified. Returns existing Upd if there's one already attached to
#	that file. Sets this as the default Upd stream.
#
# Methods:
#   Out(args)
#	Update currently visible line to match argument.
#   NL(args)
#	Update current line to match argument then print a newline and
#	clear the internal state.
#   Err(args)
#	Clear current line, then print error message on STDERR,
#	then restore current line.
#   Select
#	Make this Upd object the default.
#
# Subroutines (exported to main, too)
#  out(args)	Out(args) for the default object.
#  err(args)	Err(args) for the default object.
#  nl(args)	NL(args) for the default object.
#
# Fields
#  CLIP		Clip lines to this length before display.
#		0 ==> no clipping.
#		Default: 79.
#  TPUT		Call the tput command to get this terminal's
#		clear-to-EOL sequence, and use than instead of
#		writing spaces.
#		Default: false.
#

=head1 NAME

cs::Upd - update dynamic progress line

=head1 SYNOPSIS

	use cs::Upd;

	out(message);
	nl(message);
	err(message-with-\n);

	$U = new cs::Upd (STATUSTTY, TTY);
	$U->Out(message);
	$U->Nl(message);
	$U->Err(message-with-\n);

=head1 DESCRIPTION

This module provides a progress line
for reporting actions of a script,
on B<STDERR> by default, though any stream may be used for this purpose.
It provides B<out> and B<nl> functions for setting the progress line
and printing an actual scrolled line respectively,
B<err> for printing error messages,
and intercepts perl's B<warn> and B<die> calls
to do the right thing.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

require 'flush.pl';

# hard exported to main
sub out		{ if (@_ && ! defined $_[0])
		  { my@c=caller;warn "\@_ && ! defined \$_[0] from [@c]";
		  }
		  cs::Upd::out(@_);
		}
sub err		{ cs::Upd::err(@_); }
sub nl		{ cs::Upd::nl(@_); }
sub promptfor	{ cs::Upd::promptfor(@_); }
sub ask		{ cs::Upd::ask(@_); }

sub outif	{ my($level)=shift(@_);
		  if ($cs::Misc::debug_level >= $level)
		  { out(@_);
		  }
		}
sub nlif	{ my($level)=shift(@_);
		  if ($cs::Misc::debug_level >= $level)
		  { nl(@_);
		  }
		}
sub progress	{ outif(1,@_); }
sub verbose	{ nlif(2,@_); }
sub debug	{ nlif(3,@_); }

package cs::Upd;

sub setDefault($);

$cs::Upd::_Clip=79;	# default clip length

setDefault(STDERR);	# default upd structure
$SIG{__WARN__}=sub { my($curr)=current();
		     err(@_);
		     out($curr);
		   };
$SIG{__DIE__} =sub { my($curr)=current();
		     $curr =~ s/\s+$//;
		     out('');
		     err("$curr\n") if length $curr;
		     die(@_);
		   };

=head1 GENERAL FUNCTIONS

=over 4

=item setDefault(I<STREAM>)

Make the default update stream I<STREAM>.
The default default is B<STDERR>.

=cut

sub setDefault($)
{ $cs::Upd::This=new cs::Upd $_[0];
}

=item out(I<message>)

Display I<message> in the progress line of the default object
(normally attached to B<STDERR>).

=item nl(I<message>)

Print I<message> and a newline.

=item err(I<message>)

Print I<message> to the B<STDERR> stream.

=item promptfor(I<prompt>,I<FILE>)

Issue the I<prompt> via the default object
then read a line from the stream I<FILE>.
If omitted, I<FILE> defaults to B<STDIN>.
Return the input line.

=cut

=back

=head1 OBJECT CREATION

=over 4

=item new cs::Upd (I<FILE>,I<mode>)

Create a new B<cs::Upd> object
attached to the stream I<FILE>
in the mode I<mode>.
If I<FILE> is omitted, use B<STDOUT>.
I<mode> is one of B<TTY> or B<FILE>.
In B<TTY> mode,
B<cs::Upd> makes use of the B<\r> and B<\b> characters
to do an optimal rewrite of the line
(to use minimum bandwidth for users operating over a low bandwidth connection)
when the B<out> function or B<Out> method is called.
In B<FILE> mode,
simply prints a B<\n> and the new line.
If I<mode> is omitted
the stream I<FILE> is B<stat>()ed to choose a mode.

=cut

sub new($;$$)
{ my($class,$FILE,$mode)=@_;

  $FILE=main::STDOUT if ! defined $FILE;
  $FILE =~ s/'/::/g;
  $FILE =~ s/^::/main::/;

  if (! defined $cs::Upd::_U{$FILE})
  { $cs::Upd::_U{$FILE}
   =bless { TYPE	=> FILE,
	    FILE	=> $FILE,
	    STATE	=> '',
	    ERRSTATE	=> '',
	    TPUT	=> 0,
	    CLIP	=> $cs::Upd::_Clip,
	    CLREOL	=> undef,
	  }, $class;

    $cs::Upd::_U{$FILE}->SetMode($mode);
  }

  $cs::Upd::This=$cs::Upd::_U{$FILE};
}

sub DESTROY
{ my($this)=shift;
  $this->Out('');
  delete $cs::Upd::_U{$this->{FILE}};
}

sub END
{ for my $key (keys %cs::Upd::_U)
  { $cs::Upd::_U{$key}->Out('');
  }
}

=back

=head1 OBJECT METHODS

=over 4

=item SetMode(I<mode>)

Set the mode of the object to I<mode>.
If omitted, B<stat>() the stream to choose a mode.
If I<mode> is B<TTY>, try to set the clip width to the tty column count - 1.

=cut

sub SetMode($;$)
{ my($this,$mode)=@_;
  
  my($FILE)=$this->{FILE};
  $mode=(-t $FILE ? TTY : FILE) if ! defined $mode;

  if ($mode eq TTY)
  { my $fn = fileno($FILE);
    my $cols = `stty -a <&$fn 2>/dev/null`;
    if ($cols =~ / columns (\d+);/)
    { $this->{CLIP}=$1-1;
      ##warn "set clip size to $this->{CLIP}";
    }
  }

  $this->{MODE}=$mode;
}

=item Select()

Make this object the default
for use by the B<out> and B<nl> functions.

=cut

sub Select($)
{ my($U)=@_;
  $U=new cs::Upd $U if ! ref $U;
  $cs::Upd::This=$U;
}

sub _diff($$)	 # (oldline,newline) -> updstr
{ my($oldline,$newline)=@_;
  local($_);

  if ($oldline =~ /\t/)
  { $oldline=::detab($oldline);
  }

  my($clip)=$cs::Upd::This->{CLIP};

  if ($clip > 0 && length($oldline) > $clip)
  { $oldline=substr($oldline,$[,$clip);
  }

  if ($newline =~ /\t/)
  { $newline=::detab($newline);
  }

  if ($clip > 0 && length($newline) > $clip)
  { $newline=substr($newline,$[,$clip);
  }

  # find length of matching prefix
  my($len);
  if (length $oldline == 0 || length $newline == 0)
  # must be zero
  { $len=0;
  }
  else
  # binary chop for comparison
  { my($pow,$nlen);

    for ($pow=1;
	 $pow<=length($oldline) && $pow<=length($newline);
	 $pow+=$pow)
    {}

    $pow=int($pow/2);
    for ($len=0; $pow>0; $pow=int($pow/2))
    { $nlen=$len+$pow;
      if (substr($oldline,$[,$nlen) eq substr($newline,$[,$nlen))
      { $len=$nlen;
      }
    }
  }

  # choose shortest rewrite string
  if (length($newline)+1 < length($oldline)+length($newline)-$len-$len)
  { $_="\r".$newline;
  }
  else
  { $_=("\b" x (length($oldline)-$len));
    $_.=substr($newline,$[+$len) if length $newline > $len;
  }

  # now consider trailing unerased characters
  $len=length($oldline)-length($newline);

  my($tput)=$cs::Upd::This->{TPUT};

  if ($tput)
  { if (! defined($cs::Upd::This->{CLREOL}))
    { $cs::Upd::This->{CLREOL}=`tput el`;
      if ($?)
      { $cs::Upd::This->{TPUT}=0;
	undef $cs::Upd::This->{CLREOL};
      }
    }
  }

  if ($tput && $len > length($cs::Upd::This->{CLREOL}))
  { $_.=$cs::Upd::This->{CLREOL};
  }
  elsif ($len > 0)
  { $_.=' '  x $len
      . "\b" x $len;
  }

  $_;
}

=item PromptFor(I<prompt>,I<FILE>)

Issue the I<prompt> and then read a line from the stream I<FILE>.
If omitted, I<FILE> defaults to B<STDIN>.
Return the input line.

=cut

sub PromptFor($$;$){ local($cs::Upd::This)=shift; &promptfor; }
sub promptfor($;$)
{ my($prompt,$FILE)=@_;
  local($_);

  $FILE=STDIN if ! defined $FILE;

  out($prompt);
  if (! defined ($_=<$FILE>))
  { return undef;
  }

  $cs::Upd::This->{STATE}='';

  $_;
}

=item Ask(I<prompt>,I<FILE>)

Issue the I<prompt> with "B< (Y/n)? >" appended
and then read a line from the stream I<FILE>.
If omitted, I<FILE> defaults to B<STDIN>.
Return true if the line commences with a B<y>.

=cut

sub Ask($$;$){ local($cs::Upd::This)=shift; &ask; }
sub ask($;$)
{ my($prompt,$FILE)=@_;
  local($_);

  $FILE=STDIN if ! defined $FILE;

  if (! defined ($_=promptfor("$prompt (Y/n)? ")))
  { return undef;
  }

  /^y/i;
}

=item Current()

Return the current content of the progress line.

=cut

sub Current($)
{
  local($cs::Upd::This)=shift;
  &current;
}
sub current()
{ if (@_)
  { if (! defined $_[0])
    { my(@c)=caller;warn "hmm2: \@_=[@_] from [@c]";
    }
    $cs::Upd::This->{STATE}=$_[0];
  }

  if (! defined $cs::Upd::This->{STATE})
  { my@c=caller;warn "undef STATE from [@c]";
  }
  $cs::Upd::This->{STATE};
}

sub Out	{ local($cs::Upd::This)=shift; &out; }
sub out
{
  ## if (@_ && ! defined $_[0])
  ## { my(@c)=caller;warn "hmm \@_=[@_] from [@c]";
  ## }

  local($_)=join('',@_);
  my($F)=$cs::Upd::This->{FILE};

  # s/\s+$//;	# XXX - broke prompting

  s/[^\t -\377]/sprintf("0x%02x",ord($&))/eg;

  if ($cs::Upd::This->{MODE} eq TTY)
  { print $F _diff($cs::Upd::This->{STATE},$_);
    ::flush($F);
  }
  else
  { ## silent - we only do nl() if going to a log file
    ## print $F "$_\n" if length && $_ ne $cs::Upd::This->{STATE};
  }

  warn "ouch" if ! defined;
  $cs::Upd::This->{STATE}=$_;
}

sub Flush { local($cs::Upd::This)=shift; &flush; }
sub flush
{
  my($F)=$cs::Upd::This->{FILE};
  ::flush($F);
}

sub Err	{ local($cs::Upd::This)=shift; &err; }
sub err	# (@errargs) -> void
{ my($old)=$cs::Upd::This->{STATE};
  my($msg)=join('',@_);
  return if ! length $msg;
  if ($msg !~ /\n$/)
  { my(@c)=caller;
    $msg.=" at [@c]\n";
  }
  if ($old ne $cs::Upd::This->{ERRSTATE})
  { nl($old);
    $cs::Upd::This->{ERRSTATE}=$old;
  }
  out('');
  flush();
  print STDERR $msg;
  out($old);
}

sub NL	{ local($cs::Upd::This)=shift; &nl; }
sub nl
{ my($old)=$cs::Upd::This->{STATE};

  my $F = $cs::Upd::This->{FILE};
  out('');
  print $F join('',@_)."\n";
  out($old);
}

sub Warn{ local($cs::Upd::This)=shift; &warn; }
sub warn{ err(@_); }
sub Die { local($cs::Upd::This)=shift; &die; }
sub die { err(@_); }

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
