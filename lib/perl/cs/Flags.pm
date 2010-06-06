#!/usr/bin/perl
#
# A flag class based on a list of strings.
# Simple but not very efficient in either space or time.
#	- Cameron Simpson <cs@zip.com.au> 26aug97
#
# Reimplement as \0-sep string.	- cameron 30jul98
#

=head1 NAME

cs::Flags - a set of string values

=head1 SYNOPSIS

use cs::Flags;

=head1 DESCRIPTION

This module
represents a set of flags
as string values,
typically uppercase basewords.

=cut

use strict qw(vars);

use cs::Misc;

package cs::Flags;

=head1 OBJECT CREATION

=over 4

=item new I<flags...>

Create a new B<cs::Flags> object
with the specified I<flags> already set.

=cut

sub new
{ my($class)=shift;

  my($f)="\0";
  my($this)=(bless \$f, $class);

  $this->Set(@_);

  $this;
}

=back

=head1 OBJECT METHODS

=over 4

=item Members()

Return an array of strings
for each flag present in the set.

=cut

sub Members($)
{ my($this)=@_;
 
  grep(length,split(/\0/,$$this));
}

=item Set(I<flags...>)

Set the specified flags.

=cut

sub Set
{ my($this)=shift;

  ## warn "Set(@_)";

  FLAG:
  for my $flag (@_)
  { my $str = "\0${flag}\0";
    $$this.="$flag\0" unless index($$this,$str) >= $[;
  }

  ## warn "flags=[$$this]";
}

=item Clear(I<flags...>)

Clear the specified I<flags>.

=cut

sub Clear
{ my($this)=shift;

  my $i;

  for my $flag (@_)
  { my $str = "\0${flag}\0";
    while (($i=index($$this,$str)) >= $[)
    { substr($$this,$i,length $str)='\0';
    }
  }
}

=item Reset()

Clear all the flags.

=cut

sub Reset($)
{ my($this)=@_;
  $$this="\0";
}

=item Intersect(I<flags...>)

Return a list of flags from I<flags>
which are currently set.

=cut

sub Intersect
{ my($this)=shift;

  my(@f)=();

  for my $flag (@_)
  { my $str = "\0${flag}\0";
    push(@f,$flag) if index($$this,$str) >= $[;
  }

  @f;
}

=item Test(I<flag>)

Test if the specified I<flag> is set.

=cut

sub Test($$)
{ my($this,$flag)=@_;
  index($$this,"\0${flag}\0") >= $[;
}

=item TestAll(I<flags...>)

Test that all the specified I<flags> are set.

=cut

sub TestAll
{ my($this)=shift;
  ## warn "TestAll(@_) against \"$$this\"";
  $this->Intersect(@_) == @_;
}

=back

=head1 CAVEATS

Flag strings must not contain NULs ("B<\0>").
This is not enforced by the code for efficiency reasons,
and will cause quiet dysfunction.

=head1 AUTHOR

Cameron Simpson <cs@zip.com.au>

=cut

1;
