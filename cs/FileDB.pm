#!/usr/bin/perl
#
# Manipulate the image db.
#	- Cameron Simpson <cs@zip.com.au> 09nov99
#

use strict vars;

use cs::Misc;
use cs::Pathname;
use cs::DBI;
use cs::MD5;

package cs::FileDB;

sub db()
{ cs::DBI::mydb();
}

sub new($$;$)
{ my($class,$path,$dbh)=@_;
  $dbh=db() if ! defined $dbh;

  my(@s,$md5);

  if (! (@s=stat($path)))
  { warn "$::cmd: stat($path): $!\n";
    return undef;
  }

  if (! defined ($md5=cs::MD5::md5file($path)))
  { warn "$::cmd: md5file($path) fails\n";
    return undef;
  }

  ## not binary any more - prints badly :-(
  ## # convert to raw binary
  ## $md5 =~ s/[a-f\d]{2}/chr(hex($&))/eg;

  my($type,$subtype)=mimeType($path);

  my $ins = cs::DBI::insert($dbh,FILES,TYPE,SUBTYPE,SIZE,MD5,MTIME);

  my $rec = { TYPE => $type,
	      SUBTYPE => $subtype,
	      SIZE => $s[7],
	      MD5 => $md5,
	      MTIME => $s[9]
	    };

  $ins->ExecuteWithRec($rec) || return undef;

  $rec->{ID}=cs::DBI::last_id();

  bless $rec, $class;

  $rec->_NoteBase($path);

  $rec;
}

sub fileById($)
{ my($fileid)=@_;

  my(@a)=cs::DBI::find(db(),FILES,FILE_ID,$fileid);

  return undef if ! @a;
  warn "$::cmd: multiple FILES with id $fileid" if @a > 1;
  
  my $rec = $a[0];

  $rec->{ID}=$fileid;

  bless $rec;
}

sub Id($)
{ shift->{ID};
}

sub _NoteBase($$)
{ my($this,$path)=@_;

  my $base = lc(cs::Pathname::basename($path));
  $base =~ tr/_/-/;

  my $ins = cs::DBI::insert(db(),BASENAMES,FILE_ID,BASENAME);
  $ins->ExecuteWithRec({ FILE_ID => $this->Id(), BASENAME => $base });
}

sub Basenames
{ my($this)=@_;

  my @a = cs::DBI::find(db(),BASENAMES,FILE_ID,$this->Id());
  map($_->{BASENAME}, @a);
}

# return records with FILED_ID, ARCH_ID, PATH
# for all archive entries for this file
sub IsArchived
{ my($this)=@_;

  cs::DBI::find(db(),ARCHIVED,FILE_ID,$this->Id());
}

sub mimeType($)
{ local($_)= uc cs::Pathname::basename($_[0]);

  return ('application','octet-stream') unless /.*\.(.+)/;
  $_=$1;

  if ($_ eq GIF)	{ return ('image','gif'); }
  if ($_ eq JPG || $_ eq JPEG)
			{ return ('image','jpeg'); }
  if ($_ eq HTML)	{ return ('text','html'); }

  return ('application','octet-stream');
}

#####################################################
# Methods for the archive records.
#

sub Archive($$$)
{ my($this,$archid,$rpath)=@_;
}

sub newArchive($)
{ my($class,$medium)=@_;

  my $rec = { MEDIUM => $medium };

  cs::DBI::insert(db(),ARCHIVES,MEDIUM)->ExecuteWithRec($rec) || return undef;

  $rec->{ID}=cs::DBI::last_id();     

  bless $rec, $class;
}

sub archiveById($)
{ my($archid)=@_;

  my(@a)=cs::DBI::find(db(),ARCHIVES,ARCH_ID,$archid);

  return undef if ! @a;
  warn "$::cmd: multiple ARCHIVES with id $archid" if @a > 1;
  
  my $rec = $a[0];

  $rec->{ID}=$archid;

  bless $rec;
}

sub MediumName($)
{ shift->{MEDIUM};
}

1;
