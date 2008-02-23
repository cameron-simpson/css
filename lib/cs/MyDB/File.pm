#!/usr/bin/perl
#
# cs::MyDB::File: operations on the CS_DB::FILES table
#	- Cameron Simpson <cs@zip.com.au> 21jul2000
#

=head1 NAME

cs::MyDB::File - entities in the FILES table

=head1 SYNOPSIS

use cs::MyDB::File;

=head1 DESCRIPTION

The B<cs::MyDB::File> module provides blah blah blah.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::DBI;
use cs::MyDB;
use Digest::MD5;

package cs::MyDB::File;

require Exporter;

@cs::MyDB::File::ISA=qw(cs::DBI::Table::RowObject);

=head1 GENERAL FUNCTIONS

=over 4

=cut

sub db
{ my $dbh = cs::MyDB::mydb();
  return wantarray ? ($dbh, 'FILES') : $dbh;
}

sub _byIdHash()
{ my($dbh,$table)=db();
  cs::DBI::hashtable($dbh,$table,FILE_ID,'',0);
}

=item byId(I<id>)

Given the I<id> of a file,
return an object attached to its database record.

=cut

sub byId($)
{ my($id)=@_;

  my($dbh,$table)=db();

  my $F = fetch cs::DBI::Table::RowObject ($id,$db,$table,FILE_ID,'',0);
  return undef if ! defined $F;

  bless $F;
}


=item byPath(I<path>)

Given the I<path> to a file,
return an object attached to its database record.

=cut

sub byPath($)
{ my($path)=@_;
my@c=caller;die "byPath unimplemented from [@c]";
}

=back

=head1 OBJECT CREATION

Preamble on creation methods.

=over 4

=item new cs::MyDB::File I<pathname>

Obtains a new record for the file located at I<pathname>.

=cut

sub new($$)
{ my($class,$path)=@_;

  # try to avoid opening non files (eg tapes)
  if (! stat($path))
  { warn "$::cmd: stat($path): $!\n";
    return undef;
  }

  if (! -f _)
  { warn "$::cmd: $path: not a regular file\n";
    return undef;
  }

  if (! open(FILE, "< $path\0"))
  { warn "$::cmd: can't open $path: $!\n";
    return undef;
  }

  my @s = stat(FILE);
  if (! @s)
  { warn "$::cmd: fstat($path): $!\n";
    close(FILE);
    return undef;
  }

  # just in case
  if (! -f _)
  { warn "$::cmd: $path: not a regular file\n";
    close(FILE);
    return undef;
  }

  my $size = $s[7];

  my $MD5 = new Digest::MD5;
  $MD5->addfile(FILE);
  close(FILE);

  my $rec={ SIZE => $size, MD5 => $MD5->digest() };

  my($dbh,$table)=db();

  cs::DBI::insert($dbh,$table,SIZE,MD5))->ExecuteWithRec($rec);
}

=back

=head1 OBJECT METHODS

=over 4

=item Method1(I<arg1>...

Does thing ...

=cut

sub Method1($
{ my($this,
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
