#!/usr/bin/perl
#
# cs::Palm::App::PalmPix: a module for the Address Palm app
#	- Cameron Simpson <cs@zip.com.au> 10sep2000
#

=head1 NAME

cs::Palm::App::PalmPix - interface to a PDB file for the Kodak PalmPix image file

=head1 SYNOPSIS

use cs::Palm::App::PalmPix;

=head1 DESCRIPTION

The cs::Palm::App::PalmPix module
accesses the database
for the Kodak PalmPix image database.
It is a subclass of the B<cs::Palm::PDB> class.

=cut

use strict qw(vars);

use cs::Palm;
use cs::Palm::PDB;

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Palm::App::PalmPix;

require Exporter;

@cs::Palm::App::PalmPix::ISA=qw(cs::Palm::PDB);

=head1 GENERAL FUNCTIONS

=over 4

=cut

=back

=head1 OBJECT CREATION

=over 4

=item new cs::Palm::App::PalmPix I<file>

Creates a new B<cs::Palm::App::PalmPix> object attached to the specified I<file>.

The B<ArchImage.pdb> file
appears to be a sequence of record as follows:

	image:
	record 0: 196 bytes
	record 1: 32 bytes title: string0...
	record 2: ^A
	record 3: 2400 bytes
			0x87 letters (39)
			0x88 letters ...
	records 4..7: 4 x ~7k records

=cut

sub new($$)
{ my($class,$file)=@_;

  my $this = new cs::Palm::PDB $file, 'COCO', 'ArchImage';
  return undef if ! defined $this;

  bless $this, $class;

  ## warn "this=".cs::Hier::h2a($this,1);

  my @r = ();
  my $nr = 0;

  while ($nr < $this->SUPER::NRecords())
  {
    my $rec0=$this->SUPER::Record($nr++);

  #   my $i;
  #   for ($i=0; $i<length($rec0); $i+=4)
  #   { my $n4=unpack("L",substr($rec0,$i,4));
  #     my($ss,$mm,$hh,$mday,$mon,$year,$wday,$yday,$isdst)
  #     =
  #     localtime($n4);
  #
  #     print "n=$n4 $mday/$mon/$year $hh:$mm:$ss\n";
  #   }

    my $rec1=$this->SUPER::Record($nr++);

    my($title,$rec1)=cs::Palm::parseString0($rec1);
    warn "image title = \"$title\"";

    my $rec2 = $this->SUPER::Record($nr++);
    warn "Whoa! Expects single byte record! NR=".($nr-1)
	  if length($rec2) != 1;

    my $rec3 = $this->SUPER::Record($nr++);
    warn "Whoa! Expected 2400 byte record! NR=".($nr-1)
	  if length($rec3) != 2400;

    my $imdata='';
    my $rec;
    IMDATA:
    while ($nr < $this->SUPER::NRecords()
	&& defined ($rec=$this->SUPER::Record($nr++)))
    { if (length($rec) == 196)
      # overshot into next record set
      { $nr--;
	last IMDATA;
      }

      $imdata.=$rec;
    }
    warn "length(imdata)=".length($imdata);

    my$depth;
    for ($depth=2; $depth <=8; $depth++)
    {
      open(IM, "> imdata$depth.ppm") || die "open(imdata$depth.ppm): $!";
      print IM "P3\n320 240\n255\n";
      my $j;
      my $blen=8*length($imdata);
      my $d3=$depth*3;
      my $scale = 1<<(8-$depth);
      my $np=0;
      for ($j=0; $j<$blen; )
      { my $r = vec($imdata,$j,$depth)*$scale;	$j+=$depth;
	my $g = vec($imdata,$j,$depth)*$scale;	$j+=$depth;
	my $b = vec($imdata,$j,$depth)*$scale;	$j+=$depth;
	$np+=3;
	print IM "$r $g $b\n";
      }
      close(IM);
      warn "depth=$depth: $np pixels\n";
    }
    die;
  }

  $this;
}

=back

=head1 OBJECT METHODS

=over 4

=item Record(I<n>)

Return an object containing the content of the I<n>th record.

=cut

sub Record($$)
{ my($this,$nr)=@_;

  my $raw = $this->SUPER::Record($nr);
  return undef if ! defined $raw;

  my $R = {};
  my @f = unpack("CCCCCCCCC",$raw);
  $raw=substr($raw,9);

  my @s = ();
  while ($raw =~ /^([^\0]*)\0/)
  { my $s = $1;
    $raw=$';
    push(@s,$s);
  }

  $R->{F}=[@f];
  $R->{S}=[@s];

  bless $R, cs::Palm::App::PalmPix;
}

=back

=head1 SEE ALSO

cs::Palm(3), cs::Palm::PDB(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
