#!/usr/bin/perl
#
# cs::Web::CSW::Cache: a cache of fetched pages
#	- Cameron Simpson <cs@zip.com.au> 5mar2000
#

=head1 NAME

cs::Web::CSW::Cache - a cache of fetched pages

=head1 SYNOPSIS

use cs::Web::CSW::Cache;

=head1 DESCRIPTION

The cs::Web::CSW::Cache module is a simple cache for pages.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Tk::FetchURL;

package cs::Web::CSW::Cache;

require Exporter;

@cs::Web::CSW::Cache::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=back

=head1 OBJECT CREATION

It is necessary to create a cache;
there is no default.

=over 4

=item new cs::Web::CSW::Cache (I<cachedir>)

Creates a new cache object which stored files in I<cachedir>.

=cut

sub new($$)
{ my($class,$tmpdir)=@_;

  bless { DIR => $tmpdir,
	  CACHED => {},		# fetched objects
	  PENDING => {},		# objects in progress
	}, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=item Request(I<url>,I<notify>)

Request the specified I<url> from the cache.
if I<notify> is supplied,
propagate B<cs::Tk::FetchURL> notify events back to that subroutine.

=cut

sub Request($$;$)
{ my($this,$url,$notify)=@_;

  my $cached = $this->{CACHED};

  if (exists $cached->{$url})
  { return $cached->{$url};
  }

  my $pending = $this->{PENDING};

  if (exists $pending->{$url})
  { return $pending->{$url};
  }

  $pending->{$url}=$this->_NewObject($url,$notify);
}

sub _NewObject($$$)
{ my($cache,$url,$notify)=@_;

  my $W = $cs::Tk::_mainWin;

  my $this = { STATE => PENDING,
	       URL => $url,
	       CACHE => $cache,
	       NOTIFY => $notify,
	     };

  my $fetch = new cs::Tk::FetchURL ($W,$url,\&_notify,STEPPED,$this);
  $fetch->SetPathname(cs::Pathname::tmpnam($cache->{DIR}));
  $fetch->Request();

  $this->{FETCH}=$fetch;

  bless $this, cs::Web::CSW::Cache;
}

sub _notify
{ my($fetch,$what)=@_;

  my $this = $fetch->{INFO};

  warn "$::cmd: $this->{URL}: state=$what\n";

  if (defined $this->{NOTIFY})
  { &{$this->{NOTIFY}}(@_);
  }

  $this->{STATE}=$what;

  my $url = $this->{URL};
  my $cache = $this->{CACHE};

  if ($what eq ABORT)
  { warn "abort $url\n";
    delete $cache->{PENDING}->{$url};
    return;
  }

  if ($what eq FINAL)
  { warn "move $url into the stable cache\n";
    $cache->{CACHED}->{$url}=$cache->{PENDING}->{$url};
    delete $cache->{PENDING}->{$url};
    warn "cache=".cs::Hier::h2a($cache,1);
    return;
  }
}

sub IsReady($)
{ my($this)=@_;

  $this->{INFO}->{STATE} eq FINAL;
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
