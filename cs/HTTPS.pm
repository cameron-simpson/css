#!/usr/bin/perl
#
# cs::HTTPS: a module for blah.
#	- Cameron Simpson <cs@zip.com.au> 17jan2001
#

=head1 NAME

cs::HTTPS - blah blah

=head1 SYNOPSIS

use cs::HTTPS;

=head1 DESCRIPTION

The B<cs::HTTPS> module
is a subclass of the B<cs::HTTP> module,
differing merely in that it establishes an SSL connection
via the s_client(1) command.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::HTTP;
use cs::SystemPort;

package cs::HTTPS;

require Exporter;

@cs::HTTPS::ISA=qw(cs::HTTP);

=head1 OBJECT CREATION

=over 4

=item new cs::HTTPS (I<host>, I<port>)

Make an SSL connection to the I<host>:I<port> specified
(I<port> defaults to 443, the standard HTTPS port).

=cut

sub new($$;$)
{ my($class,$host,$port)=@_;
  $port=443 if ! defined $port;

  my $this = new cs::SystemPort(
			'openssl', 's_client', '-connect', "$host:$port");

  if (! defined $this)
  { warn "$::cmd: can't connect to openssl s_client";
    return undef;
  }

  bless $this, $class;
}

=back

=head1 SEE ALSO

s_client(1), openssl(1), cs::HTTP(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
