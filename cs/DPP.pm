#!/usr/bin/perl
#
# The smarts for the Dumb PreProcessor.
#	- Cameron Simpson, October 1993
#
# Modularised.	- cameron, 23aug1998
#

use strict qw(vars);

use cs::Pathname;
use cs::Source;

package cs::DPP;

@cs::DPP::IncPath=('.');
$cs::DPP::Verbose=0;

=head1 OBJECT CREATION

=over 4

=item new cs::DPP (I<src>,I<sink>,I<name>)

Construct a new B<cs::DPP> object
to preprocess lines from the B<cs::Source> I<src>.
Processed lines are written to the B<cs::Sink> I<sink>.
For diagnostic purposes the filename I<name> will be used,
which defaults to I<src> if omitted.

=cut

sub new($$$$)
{ my($class,$src,$sink,$name)=@_;
  $name=$src if ! defined $name;

  if (! ref $src)
  { my $s = cs::Source::open($src);
    return undef if ! defined $s;
    $src=$s;
  }

  my($this)=bless { DS	=> $src,
		    SINK=> $sink,
		    NAME=> $name,
		    PRE	=> undef,
		    POST=> undef,
		    INC	=> [ @cs::DPP::IncPath ],
		    SYM	=> {},	# symbol table
		    OLD => [],	# pushed states
		    VERBOSE => $cs::DPP::Verbose,
		    STATE => { IFLINES	=> [],
			     },
		  }, $class;

  $this;
}

=back

=head1 OBJECT METHODS

=over 4

=item PreMunge(I<sub>)

Before preprocessing any non-directive line,
place it in B<$_> and call the subroutine I<sub>.
I<sub> may be a subroutine reference
or a string. In the latter case the string

	sub { I<sub>; }

is B<eval>ed to define the subroutine.
If I<sub> is omitted,
the premunging routine is disabled.

=cut

sub PreMunge($;$)
{ my($this,$sub)=@_;

  if (! defined $sub)
  { undef $this->{PRE};
  }
  elsif (ref $sub)
  { $this->{PRE}=$sub;
  }
  else
  { eval "\$sub = sub { $sub ;}";
    if ($@)
    { warn "$::cmd: cs::DPP::PreMunge: error evalling \$sub: $@";
      warn "\t sub was: $sub\n";
      undef $this->{PRE};
    }
    else
    { $this->{PRE}=$sub;
    }
  }
}

=item PostMunge(I<sub>)

After preprocessing any non-directive line,
place it in B<$_> and call the subroutine I<sub>.
I<sub> may be a subroutine reference
or a string. In the latter case the string

	sub { I<sub>; }

is B<eval>ed to define the subroutine.
If I<sub> is omitted,
the popstmunging routine is disabled.

=cut

sub PostMunge($;$)
{ my($this,$sub)=@_;

  if (! defined $sub)
  { undef $this->{POST};
  }
  elsif (ref $sub)
  { $this->{POST}=$sub;
  }
  else
  { eval "\$sub = sub { $sub ;}";
    if ($@)
    { warn "$::cmd: cs::DPP::PostMunge: error evalling \$sub: $@";
      warn "\t sub was: $sub\n";
      undef $this->{POST};
    }
    else
    { $this->{POST}=$sub;
    }
  }
}

sub _SaveState
{ my($this)=@_;
  push(@{$this->{OLD}},{ DS => $this->{DS}, 
			 STATE => $this->{STATE},
			 NAME => $this->{NAME},
		       });
}

sub _RestoreState
{ my($this)=@_;

  die "nothing to restore!" if ! @{$this->{OLD}};

  my $old = pop(@{$this->{OLD}});

  for my $key (keys %$old)
  { $this->{$key}=$old->{$key};
  }
}

# define a symbol
sub Define
{ my($this,$sym,$defn)=@_;

  $this->{VERBOSE} && warn "define $sym $defn\n";

  $this->{SYM}->{$sym}=$defn;
}

sub Undefine
{ my($this,$sym)=@_;
  delete $this->{SYM}->{$sym};
}

# run eval in package main
sub eval
{ package main;

  eval $_[0];
}

=item Process(I<text>)

Preprocess the line I<text>.

=cut

sub Process	# (text) -> expanded
{ my($this)=shift;
  local($_)=shift;

  &{$this->{PRE}} if defined $this->{PRE};

  my $sym = $this->{SYM};

  my $lhs;

  for ($lhs=''; /{(\w+)}/; )
  { if (! exists $sym->{$1})
    { warn "$::cmd: $this->{NAME}: warning: can't replace {$1}, symbol not defined\n";
      $this->Define($1,"{$1}");
    }

    $lhs.=$`.$symbol{$1};
    $_=$';
  }

  $_=$lhs.$_;

  &{$this->{POST}} if defined $this->{POST};

  $_;
}

sub DoLine
{ my($this)=shift;
  local($_)=shift;

  my $verbose = $this->{VERBOSE};
  my $state   = $this->{STATE};

  $verbose && warn "| $_";

  s/\r?\n$//;

  if (/^#\s*if\s+/)
  { push(@{$state->{IFLINES}},$.);
    push(@ifExprs,$');
    push(@ifPart,1);
    push(@ifActive,( !@ifActive
		 ||  $ifActive[$#ifActive]
		   )
		&& &if($'));
  }
  elsif (/^#\s*else\b/)
  { while (@ifPart && !$ifPart[$#ifPart])
    { warn
	    ("$::cmd: $ARGV, line $.: popping unclosed #if $ifExprs[$#ifExprs] from line $ifLines[$#ifLines]\n");
      $xit=1;
      &popif;
    }

    if (! @ifActive)
    { warn "$::cmd: $ARGV, line $.: #else without matching #if\n";
      $xit=1;
    }
    else
    { $ifActive[$#ifActive] = !$ifActive[$#ifActive]
			   && (@ifActive == 1
			    || $ifActive[$#ifActive-1]);
      $ifPart[$#ifPart] = 0;
    }
  }
  elsif (/^#\s*endif\b/)
  { if ($#ifExprs < $[)
    { warn
	    ("$::cmd: $ARGV, line $.: #endif without matching #if\n");
      $xit=1;
    }
    else
    { &popif;
    }
  }
  elsif (@ifActive && !$ifActive[$#ifActive])
  { }
  elsif (/^#\s*include\s+/)
  { &inc($ARGV,$.,$'); }
  elsif (/^#\s*define\s+/)
  { $_=$';
    if (/^(\w+)\s*/)
    { $sym=$1; $text=&preproc($');
      &define($sym,&preproc($text));
    }
    else
    { warn "$::cmd: $ARGV, line $.: syntax error in #define $_\n";
      $xit=1;
    }
  }
  elsif (/^#\s*eval\s+/)
  { $_=$';
    if (/^(\w+)\s*/)
    { $sym=$1; $perl=&preproc($');
      $text=&eval($perl);
      if ($@)
      { warn "$::cmd: $ARGV, line $.: [$perl]: $@\n";
	$xit=1;
      }
      else
      { &define($sym,$text);
      }
    }
    else
    { warn "$::cmd: $ARGV, line $.: syntax error in #eval $_\n";
      $xit=1;
    }
  }
  elsif (/^#\s*echo\b\s*/)
  { $_=$';
    print &preproc($_), "\n";
  }
  elsif (/^#\s*warn\b\s*/)
  { $_=$';
    warn "$::cmd: $ARGV, line $.: ", &preproc($_), "\n";
  }
  elsif (/^#\s*error\b\s*/)
  { $_=$';
    warn "$::cmd: $ARGV, line $.: ", &preproc($_), "\n";
    $xit=1;
  }
  else
  { print &preproc($_), "\n";
  }
}

sub preprocfile	# (filename,FILE) -> void
{ local($ARGV,$FILE)=@_;
  local(@ifLines,@ifExprs,@ifPart,@ifActive);

  if ($FILE !~ /'/)
  { local($caller); $caller=caller;
    $FILE=$caller."'".$FILE;
  }

  $verbose && warn "processing $ARGV ($FILE)\n";

  @ifLines=();
  @ifExprs=();
  @ifPart=();
  @ifActive=();
  while (<$FILE>)
  { # $verbose && warn "| $_";
    s/\r?\n$//;
    if (/^#\s*if\s+/)
    { push(@ifLines,$.);
      push(@ifExprs,$');
      push(@ifPart,1);
      push(@ifActive,( !@ifActive
		   ||  $ifActive[$#ifActive]
		     )
		  && &if($'));
    }
    elsif (/^#\s*else\b/)
    { while (@ifPart && !$ifPart[$#ifPart])
      { warn
	      ("$::cmd: $ARGV, line $.: popping unclosed #if $ifExprs[$#ifExprs] from line $ifLines[$#ifLines]\n");
	$xit=1;
	&popif;
      }

      if (! @ifActive)
      { warn "$::cmd: $ARGV, line $.: #else without matching #if\n";
	$xit=1;
      }
      else
      { $ifActive[$#ifActive] = !$ifActive[$#ifActive]
			     && (@ifActive == 1
			      || $ifActive[$#ifActive-1]);
	$ifPart[$#ifPart] = 0;
      }
    }
    elsif (/^#\s*endif\b/)
    { if ($#ifExprs < $[)
      { warn
	      ("$::cmd: $ARGV, line $.: #endif without matching #if\n");
	$xit=1;
      }
      else
      { &popif;
      }
    }
    elsif (@ifActive && !$ifActive[$#ifActive])
    { }
    elsif (/^#\s*include\s+/)
    { &inc($ARGV,$.,$'); }
    elsif (/^#\s*define\s+/)
    { $_=$';
      if (/^(\w+)\s*/)
      { $sym=$1; $text=&preproc($');
	&define($sym,&preproc($text));
      }
      else
      { warn "$::cmd: $ARGV, line $.: syntax error in #define $_\n";
	$xit=1;
      }
    }
    elsif (/^#\s*eval\s+/)
    { $_=$';
      if (/^(\w+)\s*/)
      { $sym=$1; $perl=&preproc($');
	$text=&eval($perl);
	if ($@)
	{ warn "$::cmd: $ARGV, line $.: [$perl]: $@\n";
	  $xit=1;
	}
	else
	{ &define($sym,$text);
	}
      }
      else
      { warn "$::cmd: $ARGV, line $.: syntax error in #eval $_\n";
	$xit=1;
      }
    }
    elsif (/^#\s*echo\b\s*/)
    { $_=$';
      print &preproc($_), "\n";
    }
    elsif (/^#\s*warn\b\s*/)
    { $_=$';
      warn "$::cmd: $ARGV, line $.: ", &preproc($_), "\n";
    }
    elsif (/^#\s*error\b\s*/)
    { $_=$';
      warn "$::cmd: $ARGV, line $.: ", &preproc($_), "\n";
      $xit=1;
    }
    else
    { print &preproc($_), "\n";
    }
  }

  while ($#ifExprs >= $[)
  { warn "$::cmd: $ARGV, at EOF: popping unclosed #if $ifExprs[$#ifexprs] from line $ifLines[$#ifLines]\n";
    $xit=1;
    &popif;
  }
}

sub if	# (perlexpr) -> ok
{ local($_)=&preproc(shift);
  local($ok);

  $ok=&eval($_);
  if ($@)
  { warn "$::cmd: $ARGV, $.: syntax error in #if $_: $@\n";
    $xit=1;
    $ok=0;
  }

  $ok;
}

sub popif
{ pop(@ifLines);
  pop(@ifExprs);
  pop(@ifPart);
  pop(@ifActive);
}

sub inc	# (from,lineno,filenames) -> void
{ local($from,$lineno,$_)=@_;

  for (split)
  { if (!&inc1(&preproc($_)))
    { warn "$::cmd: $from, line $.: can't #include $_\n";
    }
  }
}

sub inc1	# file -> void
{ local($_)=@_;

  if (/^<(.*)>$/)
	{ &stdinc($1);
	}
  elsif (/^"(.*)"$/)
	{ &localinc($1) || &stdinc($1);
	}
  else
  { &localinc($_) || &stdinc($_);
  }
}

sub localinc	# (file) -> opened ok
{ local($_)=@_;
  local($F);

  $_=&'catpath(&'dirname($from),$_) if !m:^/:;

  return 0 unless defined($F=&'subopen('<',$_));

  &preprocfile($_,$F);
  !length || close($F);

  1;
}

sub stdinc	# (file) -> opened ok
{ local($_)=@_;

  if (m:^/:)
	{ &localinc($_);
	}
  else
  { local($path,$F);
  
    for $dir (@ipath)
    { $path=&'catpath($dir,$_);
      warn "$::cmd: stdinc($path)";
      if (defined($F=&'subopen('<',$path)))
      { &preprocfile($path,$F);
	close($F);
	return 1;
      }
    }

    0;
  }
}

=back

=head1 SEE ALSO

m4(1), cppstdin(1)

=head1 AUTHOR

Cameron Simpson E<lt>s@zip.com.auE<gt>

=cut

1;
