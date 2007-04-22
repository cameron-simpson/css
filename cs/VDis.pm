#!/usr/bin/perl
#
# Minimalist curses accompished by piping to vdis.
#	- Cameron Simpson <cs@zip.com.au>
#

use strict qw(vars);

require 'flush.pl';

package cs::VDis;

$cs::VDis::_VDIS='VDIS0000';
undef $::cs::VDis::This;
$cs::VDis::_didstty=0;

sub new
	{ my($class)=shift;

	  $cs::VDIS::_VDIS++;

	  my($FH)="${class}::$cs::VDIS::_VDIS";
	  ## warn "opening $FH to |vdis\n";
	  open($FH,"|vdis") || return undef;
	  ## warn "opened ok\n";

	  bless { FILE	=> $FH,
		}, $class;
	}

sub DESTROY
	{ my($this)=shift;

	  close($this->{FILE}) || warn "close($this->{FILE}): $!";
	  $_didstty && stty('cooked');
	}

sub _with
	{ my($fn)=shift;
	  local($::cs::VDis::This)=shift;
	  local($cs::VDis::VDIS)=$::cs::VDis::This->{FILE};
	  &$fn(@_);
	}

sub echo { stty('echo',@_); }
sub icanon{stty('icanon',@_); }
sub stty { my($mode,$status)=@_;

	   $_didstty=1;

	   $status=1 if ! defined $status;

	   if ($mode =~ /^-/)	{ $status = !$status;
				  $mode   = $';
				}

	   system("stty ".($status ? '' : '-').$mode);
	 }

sub Out	 { _with(\&out,@_); }
sub out	 { local($_)=join('',@_);
	   s/[\000-\037]/'0x'.pack('H',chr($&))/eg;
	   print $cs::VDis::VDIS $_;
	 }

sub Flush{ _with(\&flush,@_); }
sub flush{ main::flush($cs::VDis::VDIS); }

sub Size{ _with(\&size,@_); }
sub size{ { package main; require 'cs/stty.pl'; }
	  my($speed,$cols,$rows,@etc)=&stty'get("main'STDOUT");
	  ($cols,$rows);
	}
sub Rows{ (Size(@_))[1]; }
sub Cols{ (Size(@_))[0]; }

sub Move{ _with(\&move,@_); }
sub move{ print $cs::VDis::VDIS "\033p$_[0],$_[1].";
	}

sub Sync{ _with(\&sync,@_); }
sub sync{ print $cs::VDis::VDIS ''; flush(); }

sub NewPage{ _with(\&newpage,@_); }
sub newpage
	{ print $cs::VDis::VDIS "\f"; }

sub Bold{ _with(\&bold,@_); }
sub bold{ print $cs::VDis::VDIS "\033m+B"; }
sub NoBold{ _with(\&nobold,@_); }
sub nobold{ print $cs::VDis::VDIS "\033m-B"; }

sub Under { _with(\&under,@_); }
sub under{ print $cs::VDis::VDIS "\033m+_"; }
sub NoUnder { _with(\&nounder,@_); }
sub nounder{ print $cs::VDis::VDIS "\033m-_"; }

sub Normal { _with(\&normal,@_); }
sub normal{ print $cs::VDis::VDIS "\033m0"; }

sub Bell{ _with(\&bell,@_);
	}
sub bell{ print $cs::VDis::VDIS ''; }

1;
