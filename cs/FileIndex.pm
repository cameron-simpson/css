#!/usr/bin/perl
#
#	- Cameron Simpson <cs@zip.com.au> 
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::HASH;
use cs::Stat;
use cs::MD5;
use cs::PfxIndex;

package cs::FileIndex;

@cs::FileIndex::ISA=qw(cs::HASH);

undef %cs::FileIndex::_Seen;

sub finish
	{ undef %cs::FileIndex::_Seen;
	}

sub new
	{ my($class,$path)=@_;

	  my($stat)=new cs::Stat (PATH,$path,1);
	  return undef if ! defined $stat;

	  my($fid)=join(':',
		    map($stat->{$_},
			DEV,RDEV,INO,MTIME,SIZE));

	  my($cache,$md5);

	  if (exists $cs::FileIndex::_Seen{$fid})
		{ $cache=$cs::FileIndex::_Seen{$fid};

		  # note new location
		  push(@{$cache->{PATHS}},$path)
			if ! grep($_ eq $path,@{$cache->{PATHS}});

		  return $cache;
		}

	  if (! defined ($md5=cs::MD5::md5file($path)))
		{ return undef;
		}

	  $cs::FileIndex::_Seen{$fid}
	  =
	  bless { MD5 => $md5,
		  FID => $fid,
		  PATHS => [ $path ],
		}, $class;
	}

1;
