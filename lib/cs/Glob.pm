#!/usr/bin/perl
#
# Glob related routines.
# Defines
#	&glob(pattern) -> @files
#	%glob'bed{pattern} -> NULfile-list
#	%glob'ptn{pattern-part} -> regexp

=head1 NAME

cs::Glob - shell style pattern matching

=head1 SYNOPSIS

use cs::Glob;

@files=cs::Glob::glob($pattern)

=head1 DESCRIPTION

This module provides shell style pattern matching
without using perl's inbuilt I<glob()> function,
which has historically been based of I<csh(1)> (gack!).

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Pathname;

package cs::Glob;

undef %cs::Glob::_Bed;	# NUL separated globbed lists		( *.c -> a.c, b.c, ... )
undef %cs::Glob::_Ptn;	# regexp matching glob component	( *.c -> \.c$ )

=head1 FUNCTIONS

=over 4

=item glob(I<pattern>)

I<glob> takes a shell I<pattern> as an argument
and returns an array of all matching paths.

=cut

sub glob($)
{ local($_)=shift;
  my(@globbed);

  if (defined $cs::Glob::_Bed{$_})
  { @globbed=@{$cs::Glob::_Bed{$_}};
  }
  else
  { my($_orig)=$_;
    my($sofar);

    if (m:^~([^/]*):)
    { my($user)=$1;
      my($name,$passwd,$uid,$gid,$quota,$comment,$gcos,$dir,
	    $shell) = getpwnam($user);

      if (defined($dir))
      { $_=$dir.$';
      }
    }

    # get leading slashes
    m:^/*:;
    $sofar=$&; $_=$';

    @globbed=&_glob($sofar,split(m:/+:));
    $cs::Glob::_Bed{$_orig}=[ @globbed ];
  }

  @globbed;
}

sub _glob	# (prefix,@parts) -> @files
{ my($sofar)=shift;
  local($_);

  SOFAR:
    while (defined($_=shift) && !/[[\]?*]/)
    # literal, append directly
    { if (length($sofar) && $sofar !~ m:/$:)
      { $sofar.='/';
      }

      $sofar.=$_;
    }

  if (! defined)
  # pure literal, return it
  { return $sofar;
  }

  # ok, $_ must be a pattern component
  my($ptn);

  if (defined $cs::Glob::_Ptn{$_})
  # seen this before, extract regexp
  { $ptn=$cs::Glob::_Ptn{$_};
  }
  else
  { my($_orig)=$_;

    # optimise for leading *
    if (/^\*+/)	{ $ptn=''; $_=$'; }
    else		{ $ptn='^'; }

    while (length)
      { # match [range]
	if (/^\[(!)?([^]-](-[^]-])?)+\]/)
	{ $ptn.='['
	       .(length($1) ? '^' : '')
	       .$2
	       .']';
	}
	elsif (/^\*+/)
	{ $ptn.='.*';
	}
	elsif (/^\?+/)
	{ $ptn.='.' x length($&);
	}
	elsif (/^[^[*?]+/)
	{ $ptn.=$&;
	}
	else
	{ warn "can't parse shell pattern at \"$_\"\n";
	  /.*/;
	  $ptn.=$&;
	}

	$_=$';
      }

    # optimise for trailing *
    if ($ptn =~ /\.\*$/)	{ $ptn=$`; }
    else			{ $ptn.='$'; }

    $cs::Glob::_Ptn{$_orig}=$ptn;
    # print STDERR "glob $_orig -> $ptn\n";
  }

  # collect matching entries from prefix directory
  my(@matched)=grep(/$ptn/,cs::Pathname::dirents($sofar));

  if ($#matched < $[)
  # no entries; short circuit
  { return ();
  }

  my(@globbed)=();

  if (length($sofar) && $sofar !~ m:/$:)	{ $sofar.='/'; }

  if ($#_ < $[)
  # no further parts, tack globs onto $sofar
  { for (@matched)
    { push(@globbed,$sofar.$_);
    }
  }
  else
  # more components; tack onto sofar and glob further
  { for (@matched)
    { push(@globbed,&_glob($sofar.$_,@_));
    }
  }

  @globbed;
}

=back

=head1 SEE ALSO

B<glob> in the perlfunc(3) manual.

sh(1), cs::Shell(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
