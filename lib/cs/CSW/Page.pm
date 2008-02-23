#!/usr/bin/perl
#
# cs::Web::CSW::Page: a web page
#	- Cameron Simpson <cs@zip.com.au> 28feb2000
#

=head1 NAME

cs::Web::CSW::Page - a web page

=head1 SYNOPSIS

use cs::Web::CSW::Page;

=head1 DESCRIPTION

The cs::Web::CSW::Page module tracks the state of an active web page.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::URL;
use cs::Web::CSW;

package cs::Web::CSW::Page;

require Exporter;

@cs::Web::CSW::Page::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=back

=head1 OBJECT CREATION

=over 4

=item new cs::Web::CSW::Page I<parent>, I<url>

Establish a new B<cs::Web::CSW::Page> object showing I<url>.

=cut

sub new($$$)
{ my($class,$W,$url)=@_;

  my $dbh = cs::Web::CSW::dbh();

  my $id = cs::DBI::addRow($dbh,'WINDOWS', { SESSION_ID => 0,
					     HISTORY_ID => 0,
					     STATE => BLANK,
					   });

  my $wdb = cs::Web::CSW::table('WINDOWS');

  my $w = $W->TopLevel();
  my $page = new cs::Tk::WebPage ($w,$url);

  my $this = { REC => $wdb->{$id},
	     };

  warn "this=".cs::Hier::h2a($this,1);

  bless $this, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=item Id()

Return the WINDOW_ID of this window.

=cut

sub Id($)
{ shift->{REC}->{WINDOW_ID};
}

=item HistoryId()

Return the HISTORY_ID of this window.

=cut

sub HistoryId($)
{ shift{REC}->{HISTORY_ID};
}

=item HistoryObj()

Return the B<cs::Web::CSW::History> object for this page.

=cut

sub HistoryObj($)
{ cs::Web::CSW::History::byId(shift->Id());
}

=item UrlId()

Return the URL_ID for this page.

=cut

sub UrlId($)
{ shift->HistoryObj()->UrlId();
}

=item UrlObj()

Return the B<cs::Web::CSW::Url> object for this page.

=cut

sub UrlObj($)
{ shift->HistoryObj()->UrlObj();
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.au<gt>

=cut

1;
