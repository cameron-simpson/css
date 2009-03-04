#!/usr/bin/perl
#
# Assorted shell related things.
#	- Cameron Simpson <cs@zip.com.au>
#

=head1 NAME

cs::Shell - shell related facilities

=head1 SYNOPSIS

use cs::Shell;

=head1 DESCRIPTION

This module provides shell related facilities
such as globbing and quoting.

=cut

use strict qw(vars);

use cs::Misc;

package cs::Shell;

=head1 FUNCTIONS

=over 4

=item quote(I<args...>)

The function takes an array of I<args>
and quotes each for use in a shell command line.
In an array context
an array of quoted strings is returned.
In a scalar context the quoted strings are concatenated with spaces and returned.

=cut

sub quote
{ my(@args)=@_;
  local($_);

  for (@args)
  { if (! length || m([^-\w.:/]))
    { s/'/$&\\$&$&/g;
      $_="'$_'";
    }
  }

  wantarray ? @args : join(' ',@args);
}

=item sputvars(I<sink>,I<force>,I<mode>,I<vars...>)

Write shell assignment statements to set the environment variables I<vars>
to the B<cs::Sink> I<sink>.
If I<vars> is omitted,
write assignments for every member of B<%ENV>.
If I<force> is true
the assignments set the variables unconditionally
otherwise only if the variable is already nonempty.
I<mode> is one of B<SH> or B<CSH>,
indicating Bourne shell or csh dialect.

=cut

sub sputvars
{ my($s,$force,$mode,@vars)=@_;
  @vars=keys %ENV if ! @vars;

  ## warn "sputvars(@_): vars=[@vars]";

  die "$::cmd: bad mode \"$mode\""
	unless grep($_ eq $mode,SH,CSH,PERSIST);

  ::need(cs::Hier) if $mode eq PERSIST;

 VAR:
  for my $var (sort @vars)
  {
    next VAR if ! defined $::ENV{$var};

    if (! $force && ($mode eq SH || $mode eq CSH))
    { $s->Put("test -n \"\$$var\" || ");
    }

    my $v;
    $v=quote($::ENV{$var}) if $mode eq SH || $mode eq CSH;

    if ($mode eq CSH)
    { $s->Put("setenv $var $v\n");
    }
    elsif ($mode eq SH)
    { $s->Put("{ $var=$v; export $var; }\n");
    }
    else
    { cs::Hier::putKVLine($s,$var,$::ENV{$var});
    }
  }
}

=item putvars(I<force>,I<mode>,I<vars...>)

As for I<sputvars()> above,
but writes to the current default filehandle
(as returned by the I<select> perl function).

=cut

sub putvars
{ ::need(cs::Sink);
  my $s = new cs::Sink (FILE,select);
  sputvars($s,@_);
}

=item mkpath(I<pathnames...>)

Return the I<pathnames> supplied
concatenated with colons (':').

=cut

sub mkpath { join(':',@_); }

=item statpath(I<path>)

=item statpath(I<pathnames...>)

Return the concatenation of those I<pathnames> supplied
for which the perl I<stat()> function succeeds.
Later duplicates of earlier paths are discarded
(based on their I<dev>:I<rdev> pair
from the I<stat()>, so differnet paths resolving to the same target
are correctly considered duplicates).

=cut

sub statpath
{ my(@p)=@_;
  @p=split(/:/, $p[0]) if @p < 2;

  my %got;	# paths
  my %igot;	# dev:inode
  my @s;

  mkpath(grep(m:^[^/]:			# keep relative paths
	   ||
	      (
		length			# drop empties
	     && !$got{$_}			# new path
	     && ($got{$_}=1)
	     && (@s=stat($_))		# exists
	     && !$igot{"$s[1]:$s[2]"}	# new object
	     && ($igot{"$s[1]:$s[2]"}=1)
	      ),
		@p));
}

=item glob(I<pattern>)

Call the B<cs::Glob::glob()> function.

=cut

sub glob
{ ::need(cs::Glob);
  &cs::Glob::glob;
}

=back

=head1 SEE ALSO

sh(1), cs::Glob(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
