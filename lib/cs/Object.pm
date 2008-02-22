#!/usr/bin/perl
#
# Core object. Really just a dummy DESTROY so we can always call
# SUPER::DESTROY.
#	- Cameron Simpson <cs@zip.com.au> 21jul97
#

=head1 NAME

cs::Object - root class for objects

=head1 SYNOPSIS

use cs::Object;

@ISA=(cs::Object);

=head1 DESCRIPTION

The B<cs::Object> module provided a few common methods for most objects.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Object;

@cs::Object::ISA=();

=head1 GENERAL FUNCTIONS

=over 4

=item reTIEHASH(I<preserve>,I<hashref>,I<class>,I<tiehashargs>...)

Tie the hash referenced by I<hashref>
to the specified I<class>,
passing the I<tiehashargs> to the B<tie> call.
If the optional parameter I<preserve> is B<1>,
store the original contents of the hash
in the tied object.

=cut

# ([preserve,]hashref,class[,TIEHASH-args])
sub reTIEHASH
{
  {my(@c)=caller;warn "reTIEHASH(@_) from [@c]";}

  my($preserve)=($_[0] =~ /^[01]$/
		? shift(@_)
		: 1
		);
  my($phash,$impl)=(shift,shift);

  my %tmp;

  if ($preserve)
  {
    # copy the contents
    for my $key (keys %$phash)
    { $tmp{$key}=$phash->{$key};
    }
  }

  tie(%$phash,$impl,@_)
	|| die "tie($phash,$impl,@_) fails";

  if (! defined $preserve)
  # ignore
  {}
  elsif ($preserve)
  # overwrite
  {
    # put the contents back
    for my $key (keys %tmp)
    { $phash->{$key}=$tmp{$key};
    }
  }
  else
  # supply if missing - a bit dubious
  {
    for my $key (keys %tmp)
    { $phash->{$key}=$tmp{$key}
	    if ! exists $phash->{$key};
    }
  }
}

=back

=cut

sub DESTROY
{}

=head1 OBJECT METHODS

=over 4

=item GetSet(I<field>,I<value>)

If the optional parameter I<value> is supplied,
set the specified I<filed> of the object to I<value>.
Otherwise
return the current value of I<field>
or B<undef> if it does not exist.

=cut

sub GetSet($$;$)
{ my($this,$field,$value)=@_;

  if (@_ > 2)
  { $this->{$field}=$value;
  }
  else
  { if (! exists $this->{$field})
    { ## my@c=caller;warn "no $field in $this\n\tfrom [@c]\n\t";
      return undef;
    }

    $this->{$field};
  }
}

=back

=head1 AUTHOR

Cameron Simpson <cs@zip.com.au> 21jul1997

=cut

1;
