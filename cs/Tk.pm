#!/usr/bin/perl
#
# Extension to Tk.
#	- Cameron Simpson <cs@zip.com.au> 19dec99
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use Tk;
use cs::Misc;

package cs::Tk;

@cs::Tk::ISA=qw(MainWindow);

undef $cs::Tk::_mainWin;

sub mainWindow
{ $cs::Tk::_mainWin = MainWindow->new(@_);
  bless $cs::Tk::_mainWin, cs::Tk;
}

sub _widget
{ my($csClass)=shift;
  $csClass="cs::Tk::".$csClass unless $csClass =~ /^cs::Tk::/;

  ::need($csClass);
  my $w = eval "${csClass}::new($csClass,\@_)"; die $@ if $@;
  $w;
}

sub csClock
{ _widget(cs::Tk::Clock,@_);
}

sub csFetchURL
{ _widget(cs::Tk::FetchURL,@_);
}

1;
