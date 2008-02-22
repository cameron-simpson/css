#!/usr/bin/perl
#
# cs::MyDB: do stuff with my database
#	- Cameron Simpson <cs@zip.com.au> 21jul2000
#

=head1 NAME

cs::MyDB - do stuff with my database

=head1 SYNOPSIS

use cs::MyDB;

=head1 DESCRIPTION

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::DBI;

package cs::MyDB;

require Exporter;

@cs::MyDB::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=item mydb(I<mysql-id>,I<dbname>)

Return a database handle for my personal mysql installation.
I<mysql-id> is an optional string representing the database
to contact; it defaults to B<mysql@I<systemid>> where I<systemid>
comes from the B<SYSTEMID> environment variable.
This key is passed to B<cs::Secret::get> to obtain the database login keys.
I<dbname> is the name of the database to which to attach.
It defaults to B<CS_DB>.

=cut

sub mydb(;$$)
{ my($id,$dbname)=@_;
  $id="mysql\@$ENV{SYSTEMID}" if ! defined $id;
  $dbname='CS_DB' if ! defined $dbname;

  if ( ! defined $cs::DBI::_mydb{$id} )
  { ::need(cs::Secret);
    my $s = cs::Secret::get($id);
    my $login = $s->Value(LOGIN);
    my $password = $s->Value(PASSWORD);
    my $host = $s->Value(HOST);	$host='mysql' if ! defined $host;
    ## warn "$login\@$host: $password\n";

    $cs::DBI::_mydb{$id}=DBI->connect("dbi:mysql:$dbname:$host",$login,$password);
  }

  ## $cs::DBI::_mydb{$id}->trace(1,"$ENV{HOME}/tmp/mydb.trace");

  $cs::DBI::_mydb{$id};
}

=item  fileByPath(I<path>)

Return a B<cs::MyDB::File> object given a pathname.

=cut

sub fileByPath($)
{ ::need(cs::MyDB::File);
  &cs::MyDB::File::byPath;
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
