#!/usr/bin/perl
#
# FVWM hooks, to be used by modules.
#	- Cameron Simpson <cs@zip.com.au> 15oct94
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::IO;
use cs::Upd;
use cs::Hier;

package cs::FVWM;

# module contexts
$cs::FVWM::C_NO_CONTEXT=0;
$cs::FVWM::C_WINDOW=1;
$cs::FVWM::C_TITLE=2;
$cs::FVWM::C_ICON=4;
$cs::FVWM::C_ROOT=8;
$cs::FVWM::C_FRAME=16;
$cs::FVWM::C_SIDEBAR=32;
$cs::FVWM::C_L1=64;
$cs::FVWM::C_L2=128;
$cs::FVWM::C_L3=256;
$cs::FVWM::C_L4=512;
$cs::FVWM::C_L5=1024;
$cs::FVWM::C_R1=2048;
$cs::FVWM::C_R2=4096;
$cs::FVWM::C_R3=8192;
$cs::FVWM::C_R4=16384;
$cs::FVWM::C_R5=32768;

# fvwm message types
$cs::FVWM::M_NEW_PAGE=1;
$cs::FVWM::M_NEW_DESK=1<<1;
$cs::FVWM::M_ADD_WINDOW=1<<2;
$cs::FVWM::M_RAISE_WINDOW=1<<3;
$cs::FVWM::M_LOWER_WINDOW=1<<4;
$cs::FVWM::M_CONFIGURE_WINDOW=1<<5;
$cs::FVWM::M_FOCUS_CHANGE=1<<6;
$cs::FVWM::M_DESTROY_WINDOW=1<<7;
$cs::FVWM::M_ICONIFY=1<<8;
$cs::FVWM::M_DEICONIFY=1<<9;
$cs::FVWM::M_WINDOW_NAME=1<<10;
$cs::FVWM::M_ICON_NAME=1<<11;
$cs::FVWM::M_RES_CLASS=1<<12;
$cs::FVWM::M_RES_NAME=1<<13;
$cs::FVWM::M_END_WINDOWLIST=1<<14;
$cs::FVWM::M_ICON_LOCATION=1<<15;
$cs::FVWM::M_MAP=1<<16;
$cs::FVWM::M_ERROR=1<<17;
$cs::FVWM::M_CONFIG_INFO=1<<18;
$cs::FVWM::M_END_CONFIG_INFO=1<<19;

sub new
	{ my($class,@ARGV)=@_;

	  die "bad ARGV for Module (ARGV=[@ARGV])"
		unless @ARGV >= 5;

	  my($towmfd,$fromwmfd,$wmrc,$wid,$context,@etc)=@ARGV;
	  my($towm,$fromwm);

	  die "can't attach to pipes (tofd=$towmfd, fromfd=$fromwmfd)"
		unless open(($towm=IO::newHandle()),">&$towmfd")
		    && open(($towm=IO::newHandle()),"<&$fromwmfd");

	  my($this)={ TO	=> $towm,
		      FROM	=> $fromwm,
		      RC	=> $wmrc,
		      WID	=> $wid,
		      CONTEXT	=> $context,
		      ARGV	=> [ @etc ],
		    };

	  bless $this, $class;
	}

$cs::FVWM::_dummyhdr=pack("LLLL",0,0,0,0);
sub Recv
	{ my($this)=shift;
	  my($hdr)=nread($this->{FROM},length $_dummyhdr);

	  return undef if ! defined $hdr;

	  die "bad header (length=".length($hdr).)"
		if length($hdr) != length($_dummyhdr);

	  my($start,$type,$len,$date)=unpack("LLLL",$hdr);

	  die sprintf("bad header lead-in (0x%08lx)",$start)
		if $start != 0xffffffff;
	}

sub nread
	{ my($FILE,$n)=@_;
	  local($_);
	  my($i);

	  while ($n > 0)
		{ $i=read($FILE,$_,$n,length);
		  if (! defined $i)
			{ if (length)
				{ warn "read($FILE,$n): $!\n\tsofar=[$_]";
				}

			  return undef;
			}

		  $n-=$i;
		}
	  $_;
	}

sub Send
	{ my($this,$msg,$wid)=@_;
	  my($to)=$this->{TO};

	  print $to pack("LI",$wid+0,length($msg)), $msg;
	  &'flush($to);
	}

1;
