#!/usr/bin/perl
#
# cs::Browse::URL - trivial URL object for browse history.
#	- Cameron Simpson <cs@zip.com.au> 1feb2001
#

=head1 NAME

cs::Browse::URL - trivial URL object for browse history

=head1 SYNOPSIS

use cs::browse::URL;

=head1 DESCRIPTION

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::URL;
use cs::DBI;
use cs::MyDB;
use cs::Date;

package cs::Browse::URL;

require Exporter;

@cs::Browse::URL::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=cut

sub _db
{ cs::MyDB::mydb(undef,WEB_DB);
}

sub _byURL
{ cs::DBI::hashtable(_db(),URLS,URL);
}

sub _byId
{ cs::DBI::hashtable(_db(),URLS,URL_ID);
}

=back

=head1 OBJECT CREATION

Preamble on creation methods.

=over 4

=item new cs::Browse::URL I<url>

Obtains a new object.

=cut

sub new($$;$)
{ my($class,$url,$forcenew)=@_;

  my $U = new cs::URL $url;
  $url=$U->Text();

  my $urlndx = _byURL();

  if (! exists $urlndx->{$url})
  { $urlndx->{$url}={};
  }

  my $this = $urlndx->{$url};
  warn "this=".cs::Hier::h2a($this,0);

  bless $this, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=item Id()

Return the B<URL_ID> of the URL.

=cut

sub Id($)
{ shift->{URL_ID};
}

=item Touch()

Set the B<LAST_VISIT> timeshtamp of the URL.

=cut

sub Touch()
{ shift->{LAST_VISIT}=cs::Date::timecode(time,1);
}

=item Url()

Return a B<cs::URL> object representing the URL.

=cut

sub Url($)
{ new cs::URL (shift->{URL});
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
