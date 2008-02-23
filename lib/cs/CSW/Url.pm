#!/usr/bin/perl
#
# cs::Web::CSW::Url: a record associated with a URL
#	- Cameron Simpson <cs@zip.com.au> 28feb2000
#

=head1 NAME

cs::Web::CSW::Url - a record associated with a URL

=head1 SYNOPSIS

use cs::Web::CSW::Url;

=head1 DESCRIPTION

The cs::Web::CSW::Url module specific URLs.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::URL;
use cs::Web::CSW;

package cs::Web::CSW::Url;

require Exporter;

@cs::Web::CSW::Url::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=back

=head1 OBJECT CREATION

=over 4

=item byUrl(I<url>)

Return the B<cs::Web::CSW::Url> object for a URL.

=cut

sub byUrl
{ my($class,$url)=@_;

  my $U = new cs::URL $url;
  return undef if ! defined $U;

  # normalise URL
  my $nurl = $U->Text();

  my $udb = cs::Web::CSW::table('URLS');

  if (! exists $udb->{$nurl})
  { my $id = cs::DBI::addRow($dbh,'URLS', { URL => $nurl,
					    ANCESTOR_ID => 0,
					  });

  my $this = { REC => $wdb->{$id},
	     };

  warn "this=".cs::Hier::h2a($this,1);

  bless $this, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=item Id()

Return the HISTORY_ID of this window.

=cut

sub Id($)
{ shift->{REC}->{HISTORY_ID};
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

=item ContentType(I<noparams>)

Return the MIME B<Content-Type> field value.
Return B<undef> if the URL has not been fetched yet.
If the optional I<noparams> is true,
suppress the "B<;I<param>=I<value>>" suffices.

=cut

sub ContentType($;$)
{ my($this,$noparams)=@_;
die "unimplemented";
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.au<gt>

=cut

1;
