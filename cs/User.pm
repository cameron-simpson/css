#!/usr/bin/perl
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::User;

undef %_U;

sub new
{ my($class,$user)=@_;
  my(@pw);

  if (defined $_U{$user})
  { return $_U{$user};
  }

  if ($user =~ /^\d+$/)
  { return undef unless (@pw=getpwuid($user));
  }
  else	{ return undef unless (@pw=getpwnam($user));
	}

  my($this);

  $this={ PW	=> [ @pw ],
	  USER	=> $pw[0],
	  CRYPT	=> $pw[1],
	  UID	=> $pw[2],
	  GID	=> $pw[3],
	  QUOTA	=> $pw[4],
	  COMMENT=>$pw[5],
	  GECOS	=> $pw[6],
	  DIR	=> $pw[7],
	  SHELL	=> $pw[8],
	};

  ($this->{NAME},$this->{OTHER})=parse_gecos($this->{GECOS});

  $_U{$this->{USER}}=$this;
  $_U{$this->{UID}}=$this;

  bless $this, $class;
}

sub _ifUser
{ my($key)=shift;
  my($this)=new cs::User @_;
  return undef if ! defined $this || ! exists $this->{$key};
  $this->{$key};
}

sub User{ shift->{USER}; }
sub user{ _ifUser(USER,@_); }
sub Crypt{ shift->{CRYPT}; }
sub crypt{ _ifUser(CRYPT,@_); }
sub Uid	{ shift->{UID}; }
sub uid	{ _ifUser(UID,@_); }
sub Gid	{ shift->{GID}; }
sub gid	{ _ifUser(GID,@_); }
sub Quota{ shift->{QUOTA}; }
sub quota{ _ifUser(QUOTA,@_); }
sub Comment{ shift->{COMMENT}; }
sub comment{ _ifUser(COMMENT,@_); }
sub Dir	{ shift->{DIR}; }
sub dir	{ _ifUser(DIR,@_); }
sub Shell{ shift->{SHELL}; }
sub shell{ _ifUser(SHELL,@_); }
sub Name{ shift->{NAME}; }
sub name{ _ifUser(NAME,@_); }
sub Other{ shift->{OTHER}; }
sub other{ _ifUser(OTHER,@_); }

sub parse_gecos
{ local($_)=@_;
  my($n,$o);

  s/^[\s,]+//;	# strip leading and trailing junk
  s/[\s,]+$//;
  s/\s*,+\s*/,/;# tidy up first comma

  if (/,/)
	{ $n=$`; $o=$'; }
  else	{ $n=$_; $o=''; }

  ($n,$o);
}

1;
