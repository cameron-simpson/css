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

  if (! exists $R->{ALIASES})
  { ##warn "$::cmd: no 'aliases' field in record:\n".cs::Hier::h2a($R,1);
    return;
  }

  # turn some fields into arrays
  for my $field (ADMINISTRATOR, ALIASES, GROUP, SAVE_SET)
  { $R->{$field}=[ grep(length,split(/[,\s]+/, $R->{$field} )) ];
  }

  for my $host (@{$R->{ALIASES}})
  { $H->{$host}=$R;
  }
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

=item allAliases()

Return all alias names in the default resource set.

=cut

sub allAliases()
{ return keys %{getResources()};
}

=back

=head1 OBJECT ACCESS

=over 4

=item byAlias(I<alias>)

Return a B<cs::Legato::Networker::Resource> object 

=cut

sub byAlias($)
{ my($alias)=@_;

  my $H = getResources();
  return undef if ! exists $H->{$alias};

  bless $H->{$alias};
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
