#!/usr/bin/perl
#
# Progress reporting.
#	- Cameron Simpson <cs@zip.com.au> 17jan2002
#

=head1 NAME

cs::Progress - progress reporting

=head1 SYNOPSIS

use cs::Progress;

$P = new cs::Progress;

=head1 DESCRIPTION

This module implements a progress reporting facility
giving ETA for completion.
By default it reports to STDERR using the B<cs::Upd> module.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Upd;
use cs::Units;

package cs::Progress;

=head1 GENERAL FUNCTIONS

=over 4

=cut

=back

=head1 OBJECT CREATION

=over 4

=item new(I<tag>, I<callback>)

Create a new B<cs::Progress> object
to reflect progress on something named "I<tag>".
If supplied, the subroutine reference I<callback>
will be called on each update
with the arguments (I<this>, I<when>, I<sofar>, I<eta>, I<context>).
otherwise the B<cs::Upd> module will be used to report progress on B<STDERR>.

=cut

sub new($$;$)
{ my($class,$tag,$callback)=@_;
  $callback=\&_dfltReport if ! defined $callback;

  my $this = { TAG => $tag,
	       START => time,
	       CALLBACK => $callback,
	     };

  bless $this, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=cut

sub _dfltReport($$$$$)
{ my($this,$when,$sofar,$eta,$context)=@_;

  my $rpt = $this->{TAG};

  if (length $context)
  { $rpt.=sprintf(" - %-14s", $context);
  }

  my $size = $this->Size();

  if ($size > 0)
  {
    my $pcnt = int( ($sofar*100)/$size + 0.5 );

    $rpt.=sprintf(" %4s/%s %d%%",
		  scalar(cs::Units::bytes2human($sofar,1)),
		  scalar(cs::Units::bytes2human($size,2)),
		  $pcnt);

    if (defined $eta)
    { $rpt.=" ETA: ".scalar(cs::Units::sec2human($eta-$when,2));
    }
  }
  else
  { $rpt.=" ".cs::Units::bytes2human($sofar,2);
  }
		

  cs::Upd::out($rpt);
}

=item Size(I<total>)

Set or return the expected size for the task.

=cut

sub Size($;$)
{ my($this,$size)=@_;

  return $this->{SIZE} if ! defined $size;
  
  $this->{SIZE}=$size;
}

=item Report(I<sofar>,I<when>)

Report that we have come I<sofar> units through the file.
The I<sofar> parameter may also be an arrayref of the form [I<sofar>,I<context>]
where I<context> is a string to be presented in the progress report.
If supplied, I<when> specifies the time this progress was made.
otherwise the current time is used.

The callback function will then be called with the arguments
(I<this>, I<when>, I<sofar>, I<eta>, I<context>).

=cut

sub Report($$;$)
{ my($this,$sofar,$when)=@_;
  $when=time if ! defined $when;

  my $context;

  # handle $sofar or [$sofar,$context]
  if (! ref $sofar)
  { $context='';
  }
  else
  { $context=$sofar->[1];
    $sofar=$sofar->[0];
  }

  my $eta;

  if (exists $this->{SIZE})
  { my $size = $this->{SIZE};
    my $start = $this->{START};

    if ($size >= $sofar && $when > $start)
    { $eta = $when + ($size-$sofar) * ($when-$start) / $sofar;
    }
  }

  &{$this->{CALLBACK}}($this,$when,$sofar,$eta,$context);
}

=back

=head1 SEE ALSO

cs::Upd(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
