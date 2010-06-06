#!/usr/bin/perl
#
# ImageMap routines.
#	- Cameron Simpson <cs@zip.com.au> 21mar97
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Geometry;
use cs::Table;

package cs::CGI::ImageMap;

@cs::CGI::ImageMap::ISA=(cs::Table);

sub new
	{ my($class,$pointfile,$modify)=@_;
	  die "no \$pointfile" if ! defined $pointfile;
	  $modify=0 if ! defined $modify;

	  my($this)=new cs::Table($pointfile,$modify);

	  return undef if ! defined $this;

	  my($meta)=$this->Meta();

	  $meta->{INVERSE}={};
	  $meta->{POINTLIST}=[],

	  bless $this, $class;

	  # build inverse table
	  for ($this->Keys())
		{ $this->_NoteXY($this->{$_}->{X},
				 $this->{$_}->{Y},
				 $_);
		}

	  $this;
	}
sub DESTROY
	{ cs::Table::DESTROY(@_);
	}

sub Add
	{ my($this,$key,$x,$y,%etc)=@_;

	  die if ! length $key;

	  $this->{$key}={} if ! defined $this->{$key};

	  my($p)=$this->{$key};

	  $p->{X}=$x;
	  $p->{Y}=$y;
	  for (keys %etc)
		{ $p->{$_}=$etc{$_};
		}

	  $this->_NoteXY($x,$y,$key);
	}

sub _NoteXY
	{ my($this,$x,$y,$key)=@_;
	  my($meta)=$this->Meta();
	  my($plist)=$meta->{POINTLIST};
	  my($inverse)=$meta->{INVERSE};
	  my($xy)="$x,$y";

	  if (! defined $inverse->{$xy})
		{ push(@$plist,$x,$y);
		}

	  $inverse->{$xy}=$key;
	}

sub Match	# (x,y) -> key or undef
	{ my($this,$x,$y)=@_;
	  my($meta)=$this->Meta();
	  my($plist)=$meta->{POINTLIST};
	  my($inverse)=$meta->{INVERSE};
	  my($nx,$ny)=cs::Geometry::nearest($x,$y,0,@{$plist});

	  return undef if ! exists $inverse->{"$nx,$ny"};

	  $inverse->{"$nx,$ny"};
	}

1;
