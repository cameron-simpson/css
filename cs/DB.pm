#!/usr/bin/perl
#
# The database, direct access.
# See cs::DB::Meta for more flexible access.
#	- Cameron Simpson <cs@zip.com.au> 15apr1997
#
# External variables:
#	$Dir	The dir containing the files,
#			default $ENV{DBDIR}
#			or $ENV{HOME}/.db
#	$DB
#

use strict qw(vars);

use cs::Persist;

package cs::DB;

$cs::DB::Dir=(length $ENV{DBDIR} ? $ENV{DBDIR} : "$ENV{HOME}/.db");

sub finish { cs::Persist::finish(); }

sub dbpath
{ join('/',$cs::DB::Dir,@_);
}

# take keychain and give hash
# XXX: make hash if missing?
# NB: makes multiple hooks
sub db
{ 
  my($keychain,$rw)=@_;
  $rw=0 if ! defined $rw;
  $keychain=[] if ! defined $keychain;

  die "keychain should be an array ref (called from [".join(' ',caller)."]"
	if ! ref $keychain;

  ## warn "keychain=[@$keychain], rw=$rw";

  my($db)=cs::Persist::db($cs::DB::Dir,0);

  for my $key (@$keychain)
  { ## warn "keychain - doing key \"$key\"";
    return undef if ! exists $db->{$key};
    $db=$db->{$key};
  }

  ## if ($rw) { my(@c)=caller;warn "cs::DB::db(rw=$rw) from [@c]" }

  (tied %$db)->SetReadWrite(1) if $rw && tied %$db;

  $db;
}

1;
