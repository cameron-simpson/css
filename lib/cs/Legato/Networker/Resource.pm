#!/usr/bin/perl
#
# cs::Legato::Networker::Resource: a tape in Legato's Networker backup system
#	- Cameron Simpson <cs@zip.com.au> 11oct2000
#

=head1 NAME

cs::Legato::Networker::Resource - a resource in Legato's Networker backup system

=head1 SYNOPSIS

use cs::Legato::Networker::Resource;

=head1 DESCRIPTION

The B<cs::Legato::Networker::Resource> module
accesses the Legato resource database.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Object;

package cs::Legato::Networker::Resource;

require Exporter;

@cs::Legato::Networker::Resource::ISA=qw(cs::Object);

=head1 GENERAL FUNCTIONS

=over 4

=item loadResourceFile(I<filename>)

Open the named resource file
(eg "B</nsr/res/nsr.res>")
and read its contents.
Return a hash keyed on the aliases for each record.

=cut

sub loadResourceFile($)
{ my($file)=@_;

  if (! open(RESOURCES, "< $file\0"))
  { warn "$::cmd: can't read from $file: $!\n";
    return undef;
  }

  return loadResourceHandle(RESOURCES,$file);
}

=item loadResourceHandle(I<handle>,I<filename>)

Read the contents of an open file handle attached to a resource file.
Return a hash keyed on the aliases for each record.

=cut

sub loadResourceHandle($$)
{ my($fh,$file)=@_;

  my %H;
  my $R;
  my $prev;
  my $done=0;

  local($_);

  LINE:
  while (<$fh>)
  { chomp;

    if (defined $prev)
    { $prev.=$_;
    }
    else
    { $prev=$_;
    }

    next LINE if ! length;

    if (/,$/)
    # expect another line
    {}
    else
    # end of field:value
    { $_=$prev;
      undef $prev;

      # not last record?
      if (/;$/)
      { $_=$`;
      }
      else
      { $done=1;
      }

      if (! /^([^:]+):\s*/)
      { warn "$::cmd: stdin, line $.: bad line: $_\n";
      }
      else
      { my $field=$1;
	my $value=$';
	$field=uc($field);
	$field =~ s/[-\s]+/_/g;

	if (! defined $R)
	{ $R={};
	}

	$R->{$field}=$value;
      }
    }

    if ($done)
    { _addResourceNode(\%H,$R);
      undef $R;
      $done=0;
    }
  }

  return \%H;
}

sub _addResourceNode($$)
{ my($H,$R)=@_;

  if (! exists $R->{RESOURCE_IDENTIFIER})
  { ##warn "$::cmd: no 'resource identifier' field in record:\n".cs::Hier::h2a($R,1);
    return;
  }

  # turn some fields into arrays
  for my $field (ADMINISTRATOR, ALIASES, GROUP, SAVE_SET)
  { if (exists $R->{$field})
    { $R->{$field}=[ grep(length,split(/[,\s]+/, $R->{$field} )) ];
    }
  }

  $H->{$R->{RESOURCE_IDENTIFIER}}=$R;
}

=item getResource()

Return the hash of this system's resources.

=cut

$cs::Legato::Networker::Resource::_Resources=undef;

sub getResources()
{
  if (! defined $cs::Legato::Networker::Resource::_Resources)
  { $cs::Legato::Networker::Resource::_Resources=loadResourceFile('/nsr/res/nsr.res');
  }

  return $cs::Legato::Networker::Resource::_Resources;
}

=item allIDs()

Return all resource identifiers in the default resource set.

=cut

sub allIDs()
{ return keys %{getResources()};
}

=item allAliases

Return all alias names in the default resource set.
This list is has no repeats.

=cut

sub allAliases()
{
  my $RDB=getResources();
  my @aliases=();
  for my $id (keys %$RDB)
  { push(@aliases,@{$RDB->{$id}->{ALIASES}});
  }

  return ::uniq(@aliases);
}

=back

=head1 OBJECT ACCESS

=over 4

=item byID(I<id>)

Return a B<cs::Legato::Networker::Resource> object given its resource identifier.

=cut

sub byID($)
{ my($id)=@_;

  my $H = getResources();
  return undef if ! exists $H->{$id};

  bless $H->{$id};
}

=item byAlias(I<alias>)

Return a B<cs::Legato::Networker::Resource> object given its alias name.
In an array context, all resources with that alias are returned.
In a scalar context, if multiple resources have the same alias
one is chosen arbitrarily.

=cut

sub byAlias($)
{ my($alias)=@_;

  my $H = getResources();
  my @r=();

  ID:
  for my $id (keys %$H)
  { my $R=$H->{$id};
    if (grep($_ eq $alias, @{$R->{ALIASES}}))
    { $R=byID($id);
      return $R if ! wantarray;
      push(@r,bless $R);
    }
  }

  return wantarray ? @r : undef;	# !wantarray ==> nothing found during loop
}

=back

=head1 OBJECT METHODS

=over 4

=back

=head1 SEE ALSO

cs::Legato::Networker(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
