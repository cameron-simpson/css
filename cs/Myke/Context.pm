#!/usr/bin/perl
#
# cs::Myke::Context: a module for providing context in error reports.
#	- Cameron Simpson <cs@zip.com.au> 19feb2000
#

=head1 NAME

cs::Myke::Context - a module for providing context in error reports

=head1 SYNOPSIS

use cs::Myke::Context;

=head1 DESCRIPTION

The cs::Myke::Context module support for nested contexts
so that error messages may have a traceback
to accompany them to aid cuase location.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Myke::Context;

require Exporter;

@cs::Myke::Context::ISA=qw();

@cs::Myke::Context::_Current=();

=head1 GENERAL FUNCTIONS

=over 4

=item thing(I<arg1>)

Blah.

=cut

sub thing($)
{ my(
}

=back

=head1 OBJECT CREATION

Generally
a new object is created (eg for the current line from a configuration file)
and passed as an argument to other procedures or object constructions,
to be kept as a field in data structures.
Unused contexts are thus recovered immediately by the garbage collector.

=over 4

=item new cs::Myke::Context I<type>, I<args>...

Creates a new context object of the named I<type>,
with the current context as parent.

=cut

sub new
{ my($class,$type)=@_;

  my $C = {};

  $C->{PARENT}=$cs::Myke::Context::_Current[0]
  if @cs::Myke::Context::_Current;

  if ($type eq FILE)
  { ( $C->{FILE},
      $C->{LINENO}
    )=@ARGV;
  }
  else
  { my @c = caller;
    die "$::cmd: unsupported $class type \"$type\" from [@c]";
  }

  bless $C, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=item $Context->Method1(I<arg1>...

Does thing ...

=cut

sub Method1($
{ my(,
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
