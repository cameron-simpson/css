#!/usr/bin/perl
#
# cs::GNUInfocs::GNUInfo::Node: a node in a cs::GNUInfo object
#	- Cameron Simpson <cs@zip.com.au> 5nov2000
#

=head1 NAME

cs::GNUInfo::Node - a node in a cs::GNUInfo object

=head1 SYNOPSIS

use cs::GNUInfo::Node;

=head1 DESCRIPTION

This module provides the node description
used by the B<cs::GNUInfo> object.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Object;

package cs::GNUInfo::Node;

require Exporter;

@cs::GNUInfo::Node::ISA=qw(cs::Object);

sub dbg { &cs::GNUInfo::dbg; }

=head1 GENERAL FUNCTIONS

=over 4

=back

=head1 OBJECT CREATION

Preamble on creation methods.

=over 4

=item new cs::GNUInfo::Node I<type>, I<name>

Creates a new node of the specified I<type> (B<FILE>, B<INDIRECT>, etc)
optionally named I<name>.

=cut

sub new($$;$)
{ my($class,$type,$name)=@_;

  my $this={ TYPE		=> $type,
	     TITLE		=> '',
	     FIELDS		=> {},
	     DATA		=> [],
	     HADNL		=> 1,
	     INDENT		=> 0,
	     INLIST		=> 0,
	     SUBNODES		=> [],
	     SUBNODENAMES	=> [],
	   };

  if (defined $name)
  { $this->{NAME}=$name;
  }

  bless $this, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=item Type()

Get the B<TYPE> of this node.

=cut

sub Type($)
{ shift->{TYPE};
}

=item Name(I<name>)

Get or set the node name.

=cut

sub Name($;$)
{ my($this)=shift;
  $this->GetSet(NAME,@_);
}

=item Fields(I<hashref>)

if the optional parameter I<hashref> is supplied,
set values in the B<FIELDS> hash from those in I<hashref>.
Returns a reference to the B<FIELDS> hash.

=cut

sub Fields($;$)
{ my($this,$F)=@_;

  my $fields = $this->{FIELDS};

  if (defined $F)
  { for (keys %$F)
    { $fields->{$_}=$F->{$_};
    }
  }

  $fields;
}

=item Field(I<name>,I<value>)

Set or get the B<FIELDS> entry named I<name>.

=cut

sub Field($$;$)
{ my($this,$name,$value)=@_;

  my $F = $this->Fields();

  $F->{$name}=$value if defined $value;

  $F->{$name};
}

=item Level(I<level>)

Set or get the B<LEVEL> of this node,
used to determine the heading level.

=cut

sub Level($;$)
{ my($this)=shift;
  $this->GetSet(LEVEL,@_);
}

=item SetLevels(I<level>)

Recursively mark this node and its subsidiaries with their depth.
The I<level> parameter is normally omitted, defaulting to B<1>.

=cut

sub SetLevels($;$)
{ my($this,$level)=@_;
  $level=1 if ! defined $level;

  local(%cs::GNUInfo::Node::_Active);
  $this->_SetLevels($level);
}

sub _SetLevels($$;$)
{ my($this,$level,$super)=@_;

  return if exists $cs::GNUInfo::Node::_Active{$this}
	 && $cs::GNUInfo::Node::_Active{$this};

  $cs::GNUInfo::Node::_Active{$this}=1;

  my $urlevel = $this->Level();
  if (! defined $urlevel || $urlevel > $level)
  { $this->Level($level);
    $this->Super($super) if defined $super;
  }

  while (@{$this->{SUBNODENAMES}})
  { my $name = shift(@{$this->{SUBNODENAMES}});
    my $N = $this->ByName($name);
    if (defined $N)
    { $this->AddSubNode($N);
    }
  }

  for my $subN ($this->SubNodes())
  { $subN->_SetLevels($level+1,$this);
  }

  delete $cs::GNUInfo::Node::_Active{$this};
}

=item Info(I<info>)

Set or get the B<INFO> of this node,
a reference to the parent B<cs::GNUInfo> object.

=cut

sub Info($;$)
{ my($this)=shift;
  $this->GetSet(INFO,@_);
}

=item ByName(I<name>)

Return the node named I<name>
by consulting the parent B<cs::GNUInfo> object.

=cut

sub ByName($$)
{ my($this,$name)=@_;

  my $info = $this->Info();
  warn "no INFO to look up \"$name\"" if ! defined $info;
  return undef if ! defined $info;

  $info->Node($name);
}

=item Title(I<title>)

Set or get the B<TITLE> of this node.

=cut

sub Title($;$)
{ my($this)=shift;
  $this->GetSet(TITLE,@_);
}

=item SubNodes()

Return the array of subsidiary nodes.

=cut

sub SubNodes($)
{ @{shift->{SUBNODES}};
}

=item AddSubNode(I<subnode>)

Attach the specified I<subnode>
as a child of this node.

=cut

sub AddSubNode($$)
{ my($this,$subnode)=@_;

  if (! grep($_ eq $subnode, $this->SubNodes()))
  { push(@{$this->{SUBNODES}}, $subnode);
    $subnode->Super($this);
  }
}

=item Super(I<parent>)

Record the node I<parent>
as the superior node of this one.

=cut

sub Super($;$)
{ my($this)=shift;
  $this->GetSet(SUPER,@_);
}

=item Data()

Return a reference to the B<DATA> array.

=cut

sub Data($)
{ shift->{DATA};
}

=item AddDatum(I<datum>)

Push the I<datum> onto the end of the B<DATA> array.

=cut

sub AddDatum($$)
{ my($this,$datum)=@_;

  push(@{$this->Data()}, $datum);
}

=item Indent()

Return the indent of the last line added to the node.

=cut

sub Indent($)
{ shift->{INDENT};
}

=item HadNL()

Return whether the last line added to the node was blank.

=cut

sub HadNL($)
{ shift->{HADNL};
}

=item AddLine(I<line>,I<source>,I<filename>)

Add the supplied line
(from the B<cs::Source> I<source>,
named I<filename>)
to the node.

If this is the first nonblank line and is a heading
then set the title for the node and discard the line
and add the first line after the heading.

=cut

sub AddLine($$$$)
{ my($this,$line,$s,$fname)=@_;

  chomp;
  s/\s+$//;

  my $data = $this->Data();

  # skip leading blank lines
  while (!@$data && !length)
  { $_=$s->GetLine();
    return if ! length;
    chomp;
    s/\s+$//;
  }

  # grab first line to see if it's a title
  if (! @$data)
  {
    my $possibletitle = $_;

    # grab second line to see if it underlines the first
    $_=$s->GetLine();
    if (! length)
    # no next line - stash first line and get out
    { $this->AddDatum($possibletitle);
      return;
    }
    chomp;
    s/\s+$//;

    if (length == length($possibletitle)
     && $_ eq substr($_,$[,1) x length
       )
    # underlined title found
    { $this->Title($possibletitle);
    }
    else
    # not a title I guess
    { $this->AddDatum($possibletitle);
      $this->AddDatum($_);
    }

    $_=$s->GetLine();
    return if ! length;
  }

  chomp;
  s/\s+$//;
  $_=::detab($_);

  # watch indent changes
  # this is only here to do some really gross intuition of itemised
  # lists from indent changes
  if (/^\s+/)
  { my $nindent = length($&);
    $_=$';

    if ($nindent == 4
     && ! $this->{HADNL}
     && $this->{INDENT} == 0)
    {
    }

    $this->{INDENT}=$nindent if length;	# blank lines don't change indent
  }

  if (/^\*\s+([^:]+)::\s*(.*)/			# * hook:: comment
   || /^\*\s+([^:]+):\s+(\S[^\.]*)\s*/		# * hook: node, comment
     )
  # note subsidary nodes
  {
  }

  # save line
  $this->AddDatum($_);
  $this->{HADNL}=(length == 0);
}

=item Pod2s(I<sink>)

Transcribe this node and its subsidiaries
to the B<cs::Sink> I<sink>.

=cut

sub Pod2s($$)
{ my($this,$s)=@_;

  my $neednl=0;

  my $title = $this->Title();

  dbg("transcribe node \"$title\"");

  my $name = $this->Name();
  if (defined $name)
  {
    if (exists $cs::GNUInfo::Node::SeenNode{$name})
    {
      ## dbg("already seen \"$name\"");
      return;
    }

    $cs::GNUInfo::Node::SeenNode{$name}=1;

    if (! length $title)
    { $title=$name;
      $this->Title($title);
    }
  }

  if (length $title)
  { my $level = $this->Level();
    $level=2 if ! defined $level;
    if ($level == 1 || $level == 2)
    { $s->Put("=head$level $title\n\n");
    }
    else
    { $s->Put("\nB<$title>\n\n");
    }
  }

  my $data = $this->Data();
  ## dbg("NO DATA!") if ! @$data;

  for my $D (@$data)
  { 
    if (! ref $D)
    # plain text
    {
      $D =~ s/[<>]/$& eq '<' ? 'E<lt>' : 'E<gt>'/eg;

      # * hook:: comment
      if ($D =~ /^\*\s+([^:]+)::\s*(.*)/)
      { if (length $2)
	{ $D="$2: see L<\"$1\">\n";
	}
	else
	{ $D="See also L<\"$1\">\n";
	}
      }
      # * hook: node, comment
      elsif ($D =~ /^\*\s+([^:]+):\s+(\S[^\.]*)\s*/)
      { $D="$1: see L<\"$2\">";
	$D.=", $'" if length $';
	$D.="\n";
      }
      else
      # plain text
      { $D =~ s/\*note\s+([^:\s][^:]*)::/see L<"$1">/ig;
      }

      $D =~ s/`([^`']+)'/`B<$1>'/g;

      ## dbg("PUT $D");
      $s->Put($D);
      $s->Put("\n");
      $neednl=length($D);
    }
    else
    # object
    { my($dtype,@detc)=@$D;

      if ($dtype eq MENU)
      {
	dbg("MENU: no data!") if ! @{$detc[0]};

	for my $M (@{$detc[0]})
	{
	  if (! ref $M)
	  { $s->Put("$M\n");
	  }
	  else
	  { $s->Put("L<$M->{HOOK}|\"$M->{NODE}\">\t$M->{COMMENT}\n");
	  }
	}
      }
      else
      { warn "$::cmd: Pod2s(): unknown FILE datum type \"$dtype\" in block";
      }
    }
  }

  if ($neednl)
  { $s->Put("\n");
  }

  for my $subN ($this->SubNodes($name))
  { $subN->Pod2s($s);
  }
}

=back

=head1 SEE ALSO

cs::GNUInfo(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
