#!/usr/bin/perl
#
# cs::Web::CSW::History: a history entry associated with a page
#	- Cameron Simpson <cs@zip.com.au> 28feb2000
#

=head1 NAME

cs::Web::CSW::History - a history entry associated with a page

=head1 SYNOPSIS

use cs::Web::CSW::History;

=head1 DESCRIPTION

The cs::Web::CSW::History module tracks the user's path through the web.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::URL;
use cs::Web::CSW;

package cs::Web::CSW::History;

require Exporter;

@cs::Web::CSW::History::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=item byId(I<id>)

Return the history object with the specified I<id>.

=cut

sub byId($)
{ my($id)=@_;

  my $hdb = cs::Web::CSW::table(HISTORY,ID);

  return undef if ! exists $hdb->{$id};

  bless $hdb->{$id}, cs::Web::CSW::History;
}

=back

=head1 OBJECT CREATION

=over 4

=back

=head1 OBJECT METHODS

=over 4

=item Id()

Return the HISTORY_ID of this window.

=cut

sub Id($)
{ shift->{HISTORY_ID};
}

=item UrlId()

Return the URL_ID for this entry.

=cut

sub UrlId($)
{ shift->{URL_ID};
}

=item UrlObj()

Return the B<cs::Web::CSW::Url> object for this entry.

=cut

sub UrlObj($)
{ cs::Web::CSW::Url::byId(shift->UrlId());
}

=item Forward(I<url>)

Return a new history object reflecting advancing to the specified I<url>.

=cut

sub Forward($$)
{ my($this,$url)=@_;

  my $previd = $this->Id();

  my $dbh = cs::Web::CSW::dbh();

  my $U = cs::Web::CSW::Url::byId($url);

  my $id = cs::DBI::addRow($dbh,'HISTORY', { URL_ID => $U->Id(),
					     ANCESTOR_ID => $previd,
					   });

  byId($id);
}

=item BackId()

Return the HISTORY_ID of the ancestor history object.
=cut

sub BackId()
{ shift->{ANCESTOR_ID};
}

=item Back()

Return the previous history object.

=cut

sub Back($)
{ byId(shift->BackId());
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.au<gt>

=cut

1;
