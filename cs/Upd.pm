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

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__);
      }

# use cs::Misc;

require 'flush.pl';

# hard exported to main
sub out	{ cs::Upd::out(@_); }
sub err	{ cs::Upd::err(@_); }
sub nl	{ cs::Upd::nl(@_); }
sub promptfor { cs::Upd::promptfor(@_); }

package cs::Upd;

$cs::Upd::_Clip=79;	# default clip length

$cs::Upd::This=new cs::Upd STDERR;	# default upd structure
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

sub new
{ my($class,$FILE,$mode)=@_;

  $FILE=main::STDOUT if ! defined $FILE;
  $FILE =~ s/'/::/g;
  $FILE =~ s/^::/main::/;

  if (! defined $cs::Upd::_U{$FILE})
  { $cs::Upd::_U{$FILE}
   =bless { FILE	=> $FILE,
	    STATE	=> '',
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
  delete $cs::Upd::_U{$$this->{FILE}};
}

sub END
{ for my $key (keys %cs::Upd::_U)
  { $cs::Upd::_U{$key}->Out('');
  }
}

sub SetMode
{ my($this,$mode)=@_;
  
  my($FILE)=$this->{FILE};

  $mode=(-t $FILE ? TTY : FILE) if ! defined $mode;

  $this->{MODE}=$mode;
}

sub Select
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
  { $newline=&'detab($newline);
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

sub PromptFor{ local($cs::Upd::This)=shift; &promptfor; }
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

sub Current
{ local($cs::Upd::This)=shift; &current; }
sub current
{ if (@_)	{ $cs::Upd::This->{STATE}=$_[0]; }
  $cs::Upd::This->{STATE};
}

sub Out	{ local($cs::Upd::This)=shift; &out; }
sub out
{ local($_)=join('',@_);
  my($F)=$cs::Upd::This->{FILE};

  # s/\s+$//;	# XXX - broke prompting

  s/[^\t -\377]/sprintf("0x%02x",ord($&))/eg;

  if ($cs::Upd::This->{MODE} eq TTY)
  { print $F _diff($cs::Upd::This->{STATE},$_);
    ::flush($F);
  }
  else
  { print $F "$_\n" if length && $_ ne $cs::Upd::This->{STATE};
  }

  $cs::Upd::This->{STATE}=$_;
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
  out('');
  print STDERR $msg;
  out($old);
}

sub NL	{ local($cs::Upd::This)=shift; &nl; }
sub nl
{ my($old)=$cs::Upd::This->{STATE};

  my $F = $cs::Upd::This->{FILE};
  out(@_); print $F "\n" if $cs::Upd::This->{MODE} eq TTY;
  $cs::Upd::This->{STATE}='';

  out($old) if $cs::Upd::This->{MODE} eq TTY;
}

sub Warn{ local($cs::Upd::This)=shift; &warn; }
sub warn{ err(@_); }
sub Die { local($cs::Upd::This)=shift; &die; }
sub die { err(@_); }

1;
