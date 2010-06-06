#!/usr/bin/perl
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Stat;

# new(pathname) or new(FILE,filehandle) or new(PATH,pathname)
sub new
	{ my($class)=shift;
	  my($type);

	  if (@_ == 1)	{ $type=PATH; }
	  else		{ $type=shift; }

	  my(@s);

	  if ($type eq PATH)	{ my($path,$follow)=@_;
				  @s=($follow ? stat($path) : lstat($path));
				}
	  elsif ($type eq FILE)	{ my($FILE)=@_;
				  die "Stat::new cs::FILE: bad handle \"$FILE\""
					unless $FILE =~ /^(\w+::)*\w+$/;

				  eval "\@s=stat($FILE)";
				}
	  else			{ die "Stat::new: unknown type \"$type\"";
				}

	  return undef unless @s;

	  my($this);

	  $this={ STAT	=> [ @s ],
		  DEV	=> $s[0],
		  INO	=> $s[1],
		  MODE	=> $s[2],
		  NLINK	=> $s[3],
		  UID	=> $s[4],
		  GID	=> $s[5],
		  RDEV	=> $s[6],
		  SIZE	=> $s[7],
		  ATIME	=> $s[8],
		  MTIME	=> $s[9],
		  CTIME	=> $s[10],
		  BLKSIZE=>$s[11],
		  BLOCKS=>$s[12],
		};

	  bless $this, $class;
	}

sub _ifStat
	{ my($key)=shift;
	  my($this)=new cs::Stat @_;
	  return undef if ! defined $this;
	  $this->{$key};
	}

sub Dev	{ shift->{DEV}; }
sub dev	{ _ifStat(DEV,@_); }
sub Ino	{ shift->{INO}; }
sub ino	{ _ifStat(INO,@_); }
sub Mode{ shift->{MODE}; }
sub mode{ _ifStat(MODE,@_); }
sub NLink{shift->{NLINK}; }
sub nlink{_ifStat(NLINK,@_); }
sub UID	{ shift->{UID}; }
sub uid	{ _ifStat(UID,@_); }
sub GID	{ shift->{GID}; }
sub gid	{ _ifStat(GID,@_); }
sub RDev{ shift->{RDEV}; }
sub rdev{ _ifStat(RDEV,@_); }
sub Size{ shift->{SIZE}; }
sub size{ _ifStat(SIZE,@_); }
sub ATime{shift->{ATIME}; }
sub atime{_ifStat(ATIME,@_); }
sub MTime{shift->{MTIME}; }
sub mtime{_ifStat(MTIME,@_); }
sub CTime{shift->{CTIME}; }
sub ctime{_ifStat(CTIME,@_); }
sub BlkSize{shift->{BLKSIZE}; }
sub blksize{_ifStat(BLKSIZE,@_); }
sub Blocks{shift->{BLOCKS}; }
sub blocks{_ifStat(BLOCKS,@_); }

1;
