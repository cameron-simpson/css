#!/usr/bin/perl
#
# cs::Palm::PDB: a module for reading and writing Palm Pilot database files.
#	- Cameron Simpson <cs@zip.com.au> 16may2000
#

=head1 NAME

cs::Palm::PDB - read and write Palm Pilot database files

=head1 SYNOPSIS

use cs::Palm::PDB;

=head1 DESCRIPTION

The B<cs::Palm::PDB> module interfaces to a Palm Pilot database file.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Object;
use cs::Palm;

package cs::Palm::PDB;

require Exporter;

@cs::Palm::PDB::ISA=qw(cs::Object);

=head1 GENERAL FUNCTIONS

=over 4

=item toc(I<file>)

Print a table of contents of the database file.

=cut

sub toc($)
{ my($file)=@_;

  my $PDB = new cs::Palm::PDB $file;
  return if ! defined $PDB;

  ::need(cs::Hier);
  print cs::Hier::h2a($PDB,1), "\n";
}

=back

=head1 OBJECT CREATION

=over 4

=item new cs::Palm::PDB I<file>, I<creator>, I<dbname>

Create a B<cs::Palm::PDB> object attached to the I<file>.
if I<file> exists it must match the I<creator> and I<dbname> supplied.

=cut

sub new($$;$$)
{ my($class,$file,$creator,$dbname)=@_;

  my(@c)=caller;
  warn "creator \"$creator\" must be 4 bytes from [@c]"
	if defined($creator) && length($creator) != 4;

  my $this = bless { DBNAME => $dbname,
		     CREATOR => $creator,
		     FILENAME => $file,
		   }, $class;

  if (-e $file)
  { if (! open(PDB,"< $file\0"))
    { warn "$::cmd: can't open $file: $!\n";
      return undef;
    }

    if (! $this->_Load(PDB,$creator,$dbname))
    { warn "errors loading \"$file\"";
      close(PDB);
      return undef;
    }

    close(PDB);
  }
  else
  { warn "\"$file\" doesn't exist";
    my $now = time;

    for my $field (qw(CTIME MTIME BTIME))
    { $this->{$field}=$now;
    }

    $this->{NR}=0,
    $this->{R}=[];
    $this->{RECORDS}=[];
  }

  $this;
}

=back

=head1 OBJECT METHODS

=over 4

=cut

sub _Load($$$$)
{ my($this,$F,$creator,$dbname)=@_;

  local($_);

  if (read($F,$_,78) != 78)
  { warn "$::cmd: error reading header from $F";
    return 0;
  }

  delete $this->{RECORDS};

  ($this->{DBNAME},
   $this->{FLAGS},
   $this->{VERSION},
   $this->{CTIME},
   $this->{MTIME},
   $this->{BTIME},
   $this->{MNUM},
   $this->{APPINFO_OFFSET},
   $this->{SORTINFO_OFFSET},
   $this->{TYPE},
   $this->{CREATOR},
   $this->{UID_SEED},
   $this->{NEXT_REC_LISTID},
   $this->{NR}
  )
  =
  unpack("a32nnNNNNNNa4a4NNn",$_);

  # convert timestamps
  for my $field (qw(CTIME MTIME BTIME))
  { $this->{$field}=cs::Palm::palm2gmt($this->{$field});
  }

  # rip off stuff after the NUL
  $this->{DBNAME} =~ s/\0.*$//s;

  if ((defined $creator && $creator ne $this->{CREATOR})
   || (defined $dbname && $dbname ne $this->{DBNAME})
     )
  { warn "$::cmd: creator/dbname supplied \"$creator/$dbname\" doesn't match file \"$this->{CREATOR}/$this->{DBNAME}\"\n";
    return 0;
  }

  my $NR = $this->{NR};
  my $RR = $this->{R}=[];
  my $R;

  for my $nr (0..$NR-1)
  { if (read($F,$_,8) != 8)
    { warn "$::cmd: error reading record $nr from $F";
      return 0;
    }

    my @u = unpack("NCa3",$_);
    ## warn "u=".cs::Hier::h2a(\@u,0);

    my $R = {};

    ($R->{OFFSET},
     $R->{ATTRIB},
     $R->{UID}
    )
    =
    @u;

    $R->{UID}=65536*ord(substr($R->{UID},$[,1))
	     +  256*ord(substr($R->{UID},$[+1,1))
	     +      ord(substr($R->{UID},$[+2,1))
	     ;

    push(@$RR, $R);
  }

  # sort records and compute sizes
  # do not assume record order matches storage order

  # nab file size - needed for last record
  my @s = eval "stat $F";	warn "$::cmd: stat($F): $!" if ! @s;
  my $size = $s[7];
  my @posmap = sort { $a->[0] <=> $b->[0] }
		    map( [$RR->[$_]->{OFFSET}, $_],
			 0..$NR-1);

  for my $pr (0..$#posmap)
  {
    my $p = $posmap[$pr];
    my $po = $p->[0];
    my $nr = $p->[1];
    my $R = $RR->[$nr];

    $R->{SIZE} = ( $pr == $#posmap
		 ? $size
		 : $posmap[$pr+1]->[0]
		 ) - $po;
  }

  ## warn cs::Hier::h2a($this,1);

  1;
}

=item FileName(I<file>)

Set or return the filename
to which this object is attached.

=cut

sub FileName($;$)
{ my($this)=shift;
  $this->GetSet(FILENAME,@_);
}

=item NRecords()

Return the number of records in the file.

=cut

sub NRecords($)
{ shift->{NR};
}

sub _LoadRecordData($)
{ my($this)=@_;

  my $file = $this->FileName();
  if (! open(PDB, "< $file\0"))
  { warn "$::cmd: can't open $file: $!\n";
    return 0;
  }

  my $RR = $this->{R};
  my $RD = ($this->{RECORDS} = []);
  my $NR = $this->NRecords();

  for my $nr (0..$NR-1)
  {
    my $R = $RR->[$nr];

    if (tell(PDB) != $R->{OFFSET}
     && ! seek(PDB, $R->{OFFSET}, 0))
    { warn "$::cmd: can't seek to $R->{OFFSET} to read record $nr: $!";
      delete $this->{RECORDS};
      return 0;
    }
    else
    { ## warn "seek($R->{OFFSET}) ok";
    }

    if (read(PDB, $RD->[$nr], $R->{SIZE}) != $R->{SIZE})
    { warn "$::cmd: failed to read $R->{SIZE} bytes for record $nr: $!";
      delete $this->{RECORDS};
      return 0;
    }
  }

  1;
}

=item Record(I<n>)

Return the content of record I<n>
(counting from zero)
as a string.

=cut

sub Record($$)
{ my($this,$nr)=@_;

  if ($nr < 0 || $nr >= $this->NRecords())
  { warn "$::cmd: $nr is out of range";
    return undef;
  }

  if (! exists $this->{RECORDS})
  { return undef if ! $this->_LoadRecordData();
  }

  $this->{RECORDS}->[$nr];
}

=back

=head1 SEE ALSO

cs::Palm(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
