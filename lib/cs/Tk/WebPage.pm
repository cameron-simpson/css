#!/usr/bin/perl
#
# cs::Tk::WebPage: a rendering of a web page
#	- Cameron Simpson <cs@zip.com.au> 05mar2000
#

=head1 NAME

cs::Tk::WebPage - display a web page

=head1 SYNOPSIS

use cs::Tk::WebPage;

=head1 DESCRIPTION

The cs::Tk::WebPage module presents a view of a web page.
It is handed a B<cs::URL> object
(hmm, or subclass? - needs some extra info).

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use Tk;

package cs::Tk::WebPage;

require Exporter;

@cs::Tk::WebPage::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=back

=head1 OBJECT CREATION

Preamble on creation methods.

=over 4

=item new cs::Tk::WebPage I<parent>,I<url>

Create a new window of the appropriate type
for the object referenced by I<url>.

=cut

sub new($$$)
{ my($class,$W,$U)=@_;

  my $type = 'text/plain';	## $U->ContentType(1);

  my $w;

  $w=$W->Text(-tabs => '8c');

  $w->insert(0,'Dummy text ['.$type.']');

  bless { W => $w,
	  URL => $U,
	}, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
