#!/usr/bin/perl
#
# Minimalist curses accompished by piping to vdis.
#

require 'flush.pl';

package vdis;

$VDIS='VDIS0000';

@stack=();

sub init
	{ open(++$VDIS,"|vdis") || die "can't pipe to vdis: $!";

	  return "vdis'$VDIS";
	}

sub size{ { package main; require 'cs/stty.pl'; }
	  local($speed,$cols,$rows,@etc)=&stty'get("main'STDOUT");
	  ($cols,$rows);
	}

sub move{ print $VDIS "\033p$_[0],$_[1].";
	}

sub sync{ print $VDIS ''; &'flush("vdis'$VDIS"); }

sub newpage
	{ print $VDIS "\f"; &'flush("vdis'$VDIS"); }

sub bold{ print $VDIS "\033m+B"; }
sub nobold{ print $VDIS "\033m-B"; }

sub under{print $VDIS "\033m+_"; }
sub nounder{print $VDIS "\033m-_"; }

sub normal{print $VDIS "\033m0"; }

sub end	{ close($VDIS);
	}

1;
