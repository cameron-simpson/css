#!/usr/bin/perl
#
# cs::Palm::PDB::Record: a Palm DataBase Record
#	- Cameron Simpson <cs@zip.com.au> 19may2000
#

=head1 NAME

cs::Palm::PDB::Record - a Palm Pilot database record

=head1 SYNOPSIS

use cs::Palm::PDB::Record;

=head1 DESCRIPTION

The cs::Palm::PDB::Record module
represents a record from a Palm Pilot file.
Application specific subclasses

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Object;
use cs::Flags;

package cs::Palm::PDB::Record;

require Exporter;

@cs::Palm::PDB::Record::ISA=qw(cs::Object);

=head1 GENERAL FUNCTIONS

=over 4

=item unpackAttrib(I<attrib>)

Return an array containing a B<cs::Flags> object
(containing a subset of B<SECRET>, B<BUSY>, B<DIRTY> and B<DEL_ON_SYNC>)
and the category number.

=cut

sub attribUnpack($)
{ my($attrib)=@_;

  # extract flags
  my @a = ();

  for my $A ( [ 0x10,	SECRET ],
	      [ 0x20,	BUSY ],
	      [ 0x40,	DIRTY ],
	      [ 0x80,	DEL_ON_SYNC ]
	    )
  { push(@a,$A->[1]) if $attrib & $A->[0];
  }

  ( (new cs::Flags @a), $attrib & 0x0f );
}

=item attribPack(I<flags>, I<category>)

Return the byte value representing the I<flags> and I<category> number
as obtained from B<attribUnpack>.

=back

sub attribPack($$)
{ my($F,$cat)=@_;

  my $n = 0;

  for my $A ( [ 0x10,	SECRET ],
	      [ 0x20,	BUSY ],
	      [ 0x40,	DIRTY ],
	      [ 0x80,	DEL_ON_SYNC ]
	    )
  { $n|=$A->[0] if $F->Test($A->[1]);
  }

  $n|($cat & 0x0f);
}

=head1 OBJECT CREATION

New objects are created only from the B<cs::Palm::PDB> module,
and obtained by others via the B<Record()> method of that class.
The B<cs::Palm::App::*> modules typically rebless records
obtained through that method
into their own record subclasses.

=over 4

=item new cs::Palm::PDB::Record I<parent>, I<nr>, I<new>

This creates a new record object
given the supplied B<cs::Palm::PDB> object I<parent>
and record number I<nr>
(which counts from 0).
If set to true, the optional parameter I<new>
creates a new record.

=cut

sub new($$$)
{ my($class,$parent,$nr,$new)=@_;
  $new=0 if ! defined $new;

  if ($nr < 0
   || ($new
       ? $nr != $parent->Nrecords()
       : $nr >= $parent->NRecords()
       )
     )
  { my @c = caller;
    warn "$::cmd: bad record number $nr from [@c]";
    return undef;
  }

  my $this = { NR => $nr,
	       PARENT => $parent,
	     };
}

=back

=head1 OBJECT METHODS

=over 4

=cut

sub _R($)
{ my($this)=@_;
  $this->{PARENT}->{R}->[$this->{NR}];
}

sub _AttribUnpack($)
{ my($this)=@_;

  my $R = $this->_R();

  ($this->{FLAGS}, $this->{CATEGORY})
  =
  attribUnpack($R->{ATTRIB});
}

sub _AttribRepack($)
{ my($this)=@_;

  if (! exists $this->{FLAGS})
  { my @c = caller;
    die "$::cmd: _AttribRepack() without prior _AttribUnpack on record $this->{NR}\n\tfrom [@c]";
  }

  my $R = $this->_R();

  $R->{ATTRIB}=_attribPack($this->{FLAGS}, $this->{CATEGORY});
}

=item Attribs()

Return the record attributes as a B<cs::Flags> object.

=cut

sub Attribs($)
{ my($this)=@_;

  $this->_AttribUnpack() if ! exists $this->{FLAGS};
  $this->{FLAGS};
}

=item Category(I<category>)

Return the record category number.
If the optional parameter I<category> is supplied, set the category number.

=cut

sub Category($)
{ my($this)=@_;

  $this->_AttribUnpack() if ! exists $this->{FLAGS};
  $this->GetSet(CATEGORY);
}

=back

=head1 SEE ALSO

cs::Palm::PDB(3), cs::Flags(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
