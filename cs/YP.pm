#!/usr/bin/perl
#
# Code to support YP maps.
#	- Cameron Simpson <cs@zip.com.au> 15mar95
#
# Each map is represented by a record containing:
#	Name => official name
#	Time => last fetch, GMT
#	Map => %records
#	Keys => @keys-in-read-order
#
# &map(mname,key) -> entry or undef
# &keys(mname) -> @keys-in-map or undef if no such map
#		keys are returned in the order read in
#		at load time
# &groups(login) -> @groups
#	Computed by inverting the group->@logins map.
# &mkGECOS(finger) -> gecos
#	Make GECOS field from DAP::UDB::finger() info.
# &upd(mname,@users) -> ok
#	Update YP map `mname' with the finger info in the array @users.
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::YP;

$cs::YP::Debug=1;

# load mname -> mapname table
if (open(YPCAT,"ypcat -x|"))
{ local($_);

  while (<YPCAT>)
  { chomp;
    if (/^use "([^"]*)"\s+for map "([^"]*)"/i)
    { $cs::YP::_Mapname{$1}=$2;
    }
    else
    { warn "$::cmd: \"ypcat -x\", line $.: bad format: $_\n";
    }
  }

  close(YPCAT);
}

$cs::YP::SyncTime=10;

# '/usr/etc/yp/testsrc';
$cs::YP::YPSrc=(defined( $ENV{YPMIRROR} ) ? $ENV{YPMIRROR} : 'YPMIRROR');
%cs::YP::YPFile=(	'passwd.byname' =>	'passwd'	);

# how to edit the various maps
%cs::YP::_MapSemantics=(
	'passwd.byname'
	=>{ File	=> 'passwd',
	    KeyField	=> Login,
	    Decode	=> \&_passwdDecode,
	    Encode	=> \&_passwdEncode
	  },
	'group.byname'
	=>{File		=> 'group',
	   KeyField	=> Group,
	   Decode	=> \&_groupDecode,
	   Encode	=> \&_groupEncode
	  },
	'auto.home'
	=>{File		=> 'auto.home',
	   KeyField	=> Login,
	   Decode	=> \&_autoHomeDecode,
	   Encode	=> \&_autoHomeEncode
	  }
	       );

sub new
{ my($class,$mname)=@_;

  my $map = mapname($mname);

  bless { ONAME => $mname,
	  MNAME => $map,
	}, $class;
}

sub mapname($)
{ my($mname)=@_;
  return $mname if ! exists $cs::YP::_Mapname{$mname};
  $cs::YP::_Mapname{$mname};
}

sub Value($$)
{ my($this,$key)=@_;

  my $table=$this->_Table();
  return undef if ! exists $table->{$key};

  $table->{$key};
}

sub _Table($)
{ my($this)=@_;
  if (! exists $this->{TABLE})
  {
    my $mname = $this->{MNAME};

    ## warn "load $mname...\n";
    { open(YPCAT,"ypcat -k $mname|") || return undef;

      my $map={};
      my $mapkeys=[];

      local($_);

      YPCAT;
      while (<YPCAT>)
      { ## warn $_;
	chomp;
	if (! /^(\S+) /)
	      { warn "$::cmd: ypcat -k $mname, line $.: bad format: $_\n";
		next YPCAT;
	      }

	$map->{$1}=$';
      }
      close(YPCAT);
      if ($? == 0)
      { $this->{TABLE}=$map;
      }
    }
  }

  $this->{TABLE};
}

1;
