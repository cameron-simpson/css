#!/usr/bin/perl
#
# cs::GNUInfo: a module for parsing GNU info files.
#	- Cameron Simpson <cs@zip.com.au> 22sep2000
#

=head1 NAME

cs::GNUInfo - parse and transcribe GNU info files

=head1 SYNOPSIS

use cs::GNUInfo;

=head1 DESCRIPTION

The B<cs::GNUInfo> module parses GNU info(1) files
and will transcribe them to perlpod(1) format
for ready conversion to other useful formats.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Pathname;
use cs::Source;
use cs::GNUInfo::Node;

package cs::GNUInfo;

require Exporter;

@cs::GNUInfo::ISA=qw();

$cs::GNUInfo::DoDebug=defined($ENV{DEBUG_GNUINFO}) && length($ENV{DEBUG_GNUINFO});
sub dbg { return $cs::GNUInfo::DoDebug if ! @_;
	  local($_)="@_";
	  chomp;
	  warn "$_\n" if $cs::GNUInfo::DoDebug;
        }

=head1 GENERAL FUNCTIONS

=over 4

=item parseTypeLine(I<line>)

Extract the block type and following parameters
from the header line of an info block.
Returns an array of B<(I<type>,I<field1>,I<value1>,I<field2>,I<value2>,...)>.

=cut

sub parseTypeLine($)
{ local($_)=@_;

  my $type;
  my %fields;

  /^[^:]*/;
  $type=uc($&);

  while (/^([^:]+):\s*([^,]+)(,\s*)?/)
  { my($op,$arg)=(uc($1),$2);
    $_=$';

    $fields{$op}=$arg;
  }

  dbg("parseTypeLine: type=$type, fields=[".join("|",%fields)."]");

  ($type,\%fields);
}

=back

=head1 OBJECT CREATION

=over 4

=item new cs::GNUInfo I<file>

Instantiate a new B<cs::GNUInfo> object
based upon the named I<file>.

=cut

sub new($$)
{ my($class,$file)=@_;

  my $dir = cs::Pathname::dirname($file);
  $file=cs::Pathname::absname($file,$dir);

  my $this=
  bless
  { ROOTFILE	=> $file,	# context
    ROOTDIR	=> $dir,
    FILEQUEUE	=> [],		# pending files
    FILESEEN	=> {},		# files queued before
    NODEMAP	=> {},		# node mapping
    NODELIST	=> [],		# node list
    NAME	=> cs::Pathname::basename($file),
  }, $class;

  $this->NoteFile($file);

  $this;
}

=back

=head1 OBJECT METHODS

=over 4

=item RunQueue()

After instantiation
the object is initially empty,
with the named file queued for processing
(via the B<NoteFile> method below).
This method processes every queued file,
which should result in processing of the entire info section
because subsidiary files are queued during this procedure
and processed before return.

=cut

sub RunQueue($)
{ my($this)=@_;

  my $Q = $this->{FILEQUEUE};

  FILE:
  while (@$Q)
  { my $file = shift(@$Q);

    dbg("RunQueue $file ...\n");
    my $s = new cs::Source(PATH,$file,1);
    if (! defined $s)
    { warn "$::cmd: can't open $file: $!";
      next FILE;
    }

    $this->ParseSource($s,$file);
  }
}

=item NoteFile(I<file>)

Queue the named I<file> for processing.
I<file> is resolved into a full pathname
with respect to the root file of the object.

=cut

sub NoteFile($$)
{ my($this,$file)=@_;

  $file=cs::Pathname::absname($file,$this->{ROOTDIR});

  if (! exists $this->{FILESEEN}->{$file})
  { dbg("NoteFile($file)");
    push(@{$this->{FILEQUEUE}}, $file);
    $this->{FILESEEN}->{$file}=1;
  }
}

=item ParseSource(I<source>,I<filename>)

Read lines from the B<cs::Source> object I<source>
(associated with the file I<filename>),
assembling them into info structures: text, menus, etc.

=cut

sub ParseSource($$$)
{ my($this,$s,$fname)=@_;

  local($_);

  BLOCK:
  while (defined($_=$s->GetLine()) && length)
  {
    if (/^\037$/)
    # commence block
    {
      # get header line
      if (! defined ($_=$s->GetLine()) || ! length)
      # end of file
      { dbg("EOF");
	last BLOCK;
      }

      # commence next block
      if (/^\037$/)
      { $s->UnGet($_);
	next BLOCK;
      }

      chomp;

      my($type,$F)=parseTypeLine($_);
      my $N = new cs::GNUInfo::Node $type;
      $N->Fields($F);
      if (exists $F->{NODE})
      { dbg("Nodename is \"$F->{NODE}\"");
	$N->Name($F->{NODE})
      }

      my $data = $N->Data();

      LINE:
      while (defined ($_=$s->GetLine()) && length)
      {
	# beginning of next block
	if (/^\037$/)
	{ $s->UnGet($_);
	  last LINE;
	}

	if ($type eq FILE)
	{ ## dbg("FILE: addline $_");
	  $N->AddLine($_,$s,$fname);
	}
	elsif ($type eq INDIRECT)
	{ $this->_LineINDIRECT($_,$s,$fname,$F,$data);
	}
	else
	{ chomp;
	  dbg("$type: push \"$_\"");
	  push(@$data, $_);
	}
      }

      my $nd = scalar(@$data);
      dbg("AddNode type=$type");
      $this->AddNode($N);
    }
    else
    # lines outside structure - ignore
    {
      dbg("SKIP: $_");
    }
  }

  $s->UnGet($_) if defined && length;
}

sub _LineINDIRECT($$$$$)
{ my($this)=shift;
  local($_)=shift;
  my($s,$fname,$F,$data)=@_;

  # file: byte offset
  if (/^([^:]+):\s*\d+$/)
  { $this->NoteFile($1);
  }
  else
  { warn "$::cmd: $fname: unparsed INDIRECT block line: $_\n";
    push(@$data,$_);
  }
}

sub AddNode($$)
{ my($this,$N)=@_;

  # back reference
  $N->Info($this);

  my $nl = $this->Nodes();
  push(@$nl, $N);

  my $name = $N->Name();
  if (defined $name)
  {
    my $nm = $this->NodeMap();

    if (exists $nm->{$name})
    { warn "$::cmd: AddNode(): repeated nodes named \"$name\", keeping last";
    }

    $nm->{$name}=$N;
  }

  $N;
}

sub Nodes($)
{ my($this)=@_;

  my $nl = $this->{NODELIST};

  wantarray ? @$nl : $nl;
}

sub NodeMap($)
{ my($this)=@_;

  my $nm = $this->{NODEMAP};

  wantarray ? %$nm : $nm;
}

=item Node(I<nodename>)

Return the B<cs::GNUINfo::Node> object for the supplied I<nodename>.

=cut

sub Node($$)
{ my($this,$name)=@_;

  my $nm = $this->NodeMap();
  return undef if ! exists $nm->{$name};

  $nm->{$name};
}

=item Pod2s(I<sink>)

Write a perlpod(1) transcription of the info object
to the B<cs::Sink> object I<sink>.

=cut

sub Pod2s($$)
{ my($this,$s)=@_;

  $s->Put("=head1 NAME\n\n".$this->{NAME}." - $this->{NAME}\n\n");

  local($_);
  local (%::SeenNode);

  my $neednl=0;

  my $nl = $this->Nodes();

  NODE:
  for my $N (@$nl)
  {
    my $type = $N->Type();

    if ($type eq FILE)
    {
      $N->Pod2s($s);
    }
    elsif (grep($_ eq $type, "INDIRECT", "TAG TABLE","END TAG TABLE"))
    {
      dbg("skip node of type \"$type\"");
    }
    else
    { warn "$::cmd: Pod2s(): unhandled node type \"$type\"";
    }
  }
}

=back

=head1 BUGS

"B<*note>" tags spanning two lines are not recognised,
and remain in the text.

=head1 SEE ALSO

info2pod(1), info2man(1), pod2man(1), perlpod(1)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
