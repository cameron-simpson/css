#!/usr/bin/perl
#
# cs::SystemPort: a cs::Port attached to a command
#	- Cameron Simpson <cs@zip.com.au> 17jan2001
#

=head1 NAME

cs::SystemPort - a cs::Port attached to a command

=head1 SYNOPSIS

use cs::SystemPort;

=head1 DESCRIPTION

A B<cs::SystemPort> is a subclass of B<cs::Port>
tttached to the supplied command.

=cut

use strict qw(vars);

use cs::Misc;
use cs::Port;
use IPC::Open2;

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::SystemPort;

require Exporter;

@cs::SystemPort::ISA=qw(cs::Port);

=head1 OBJECT CREATION

=over 4

=item new cs::SystemPort I<open2-cmd-args...>

Call B<IPC::Open2::open2> to attach to the command specified by
I<open2-cmd-args>.

=cut

sub new
{ my($class)=shift;

  my $from = cs::Misc::mkHandle();
  my $to   = cs::Misc::mkHandle();

  my $pid  = IPC::Open2::open2($from,$to,@_);
  if (! defined $pid)
  { warn "$::cmd: new cs::SystemPort: open2(@_) fails";
    return undef;
  }

  my $this = new cs::Port ($from,$to);

  $this->{cs::SystemPort::CMD}=[ @_ ];

  bless $this, $class;
}

=back

=head1 SEE ALSO

cs::Port(3), IPC::Open2(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
