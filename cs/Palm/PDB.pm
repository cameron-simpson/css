#!/usr/bin/perl
#
# cs::Palm::PDB: a module for reading and writing Palm Pilot database files.
#	- Cameron Simpson <cs@zip.com.au> 16may2000
#

=head1 NAME

cs::Palm::PDB - read and writ Palm Pilot database files

=head1 SYNOPSIS

use cs::Palm::PDB;

=head1 DESCRIPTION

The B<cs::Palm::PDB> module interfaces to a Palm Pilot database file.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Palm;

package cs::Palm::PDB;

require Exporter;

@cs::Palm::PDB::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=item toc(I<file>)

Print a table of contents of the database file.

=cut

sub toc($)
{ my($file)=@_;

  my $PDB = new cs::Palm::PDB $file;
  return if ! defined $PDB;

  print cs::Hier::h2a($PDB,1), "\n";
}

=back

=head1 OBJECT CREATION

Preamble on creation methods.

=over 4

=item new cs::Palm::PDB I<file>

Create a B<cs::Palm::PDB> object attached to the I<file>.

=cut

sub new($$)
{ my($class,$file,$dbname)=@_;
  $dbname = "unknown" if ! defined $dbname;

  my $this = bless { DBNAME => $dbname,
		     FILENAME => $file,
		   }, $class;

  if (-e $file)
  { if (! open(PDB,"< $file\0"))
    { warn "$::cmd: can't open $file: $!\n";
      return undef;
    }

    $this->_Load(PDB);
    close(PDB);
  }
  else
  { my $now = time;

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

sub _Load($$)
{ my($this,$F)=@_;

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

  $this->{DBNAME} =~ s/\0+$//;

  my $R;

  for my $nr (1..$this->{NR})
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

    push(@{$this->{R}}, $R);
  }

  ## warn cs::Hier::h2a($this,1);

  1;
}

sub FileName($)
{ shift->{FILENAME};
}

sub NRecords($)
{ shift->{NR};
}

sub _LoadRecords($)
{ my($this)=@_;

  my $file = $this->FileName();
  if (! open(PDB, "< $file\0"))
  { warn "$::cmd: can't open $file: $!\n";
    return 0;
  }

  my @s = stat PDB;
  my $fsize = $s[7];

  my $RR = $this->{RECORDS} = [];

  for my $nr (1..$this->NRecords())
  {
    my $R = $this->{R}->[$nr-1];

    if (tell(PDB) != $R->{OFFSET}
     && ! seek(PDB, $R->{OFFSET}, 0))
    { warn "$::cmd: can't seek to $R->{OFFSET} to read record $nr: $!";
      delete $this->{RECORDS};
      return 0;
    }

    my $len = ( $nr == $this->NRecords()
	      ? $fsize-tell(PDB)
	      : $this->{R}->[$nr]->{OFFSET}-tell(PDB)
	      );

    if (read(PDB, $RR->[$nr-1], $len) != $len)
    { warn "$::cmd: failed to read $len bytes for record $nr: $!";
      delete $this->{RECORDS};
      return 0;
    }
  }

  1;
}

sub Record($$)
{ my($this,$nr)=@_;

  if ($nr < 0 || $nr >= $this->NRecords())
  { warn "$::cmd: $nr is out of range";
    return undef;
  }

  if (! exists $this->{RECORDS})
  { return undef if ! $this->_LoadRecords();
  }

  $this->{RECORDS}->[$nr];
}

=back

=head1 SEE ALSO

B<cs::Palm(3)>

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
