#!/usr/bin/perl
#
# A clock (subclass of Tk::Canvas).
#	- Cameron Simpson <cs@zip.com.au> 19dec99
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Math;
use cs::Date;

package cs::Tk::Clock;

@cs::Tk::Clock::ISA=qw(Tk::Canvas);

sub new
{ my($class,$parent)=(shift,shift);

  my $w = $parent->Canvas(@_);
  $w->CanvasBind('<Configure>', [ \&_Paint, $w ]);

  bless $w, cs::Tk::Clock;

  # first tick
  $w->after((60-time%60)*1000,[ \&_Tick, $w ]);

  $w;
}

sub _Tick
{ my($w,@etc)=@_;

  # redraw
  $w->_PaintHands();

  # reschedule
  $w->after((60-time%60)*1000,[ \&_Tick, $w ]);
}

sub _Paint
{ my($w)=@_;

  my($dx,$dy)=($w->width, $w->height);
  my($cx,$cy)=($dx/2,$dy/2);

  $w->_PaintTicks();
  $w->_PaintHands();
}

sub _PaintTicks($)
{ my($w)=@_;

  my($dx,$dy)=($w->width, $w->height);
  my($cx,$cy)=($dx/2,$dy/2);
  my($rx,$ry)=($dx/2,$dy/2);

  $w->delete("ticks");

  for my $m (0..59)
  { my $th = $m/60*2*$cs::Math::PI;
    my $rx2 = $rx*($m%5 == 0 ? .9 : .95);
    my $ry2 = $ry*($m%5 == 0 ? .9 : .95);

    my($si,$co)=(sin($th),cos($th));
    my ($x1,$y1,$x2,$y2)=( $cx + $rx *$si, $cy - $ry *$co,
			   $cx + $rx2*$si, $cy - $ry2*$co
			 );

    $w->createLine($x1,$y1,$x2,$y2, -tags => "ticks", -fill => "green");
  }
}

sub _PaintHands($)
{ my($w)=@_;

  my($dx,$dy)=($w->width, $w->height);
  my($cx,$cy)=($dx/2,$dy/2);
  my($rx,$ry)=($dx/2*0.85,$dy/2*0.85);

  my $now = new cs::Date;

  my $hh = $now->Hour(1);
  my $min= $now->Min(1);

  my $thh = $hh/12*2*$cs::Math::PI;
  my $sih = sin($thh);
  my $coh = cos($thh);
  my $thm = $min/60*2*$cs::Math::PI;
  my $sim = sin($thm);
  my $com = cos($thm);

  $w->delete("hands");
  $w->_PaintHand($cx,$cy,$rx,$ry,$thm);
  $w->_PaintHand($cx,$cy,$rx*0.6,$ry*0.6,$thh);
}

sub _PaintHand($$$$$)
{ my($w,$cx,$cy,$rx,$ry,$th)=@_;

  my $si = sin($th);
  my $co = cos($th);

  $w->createPolygon( $cx+$rx*$si, $cy-$ry*$co,

		     $cx-$rx*$co*0.05,
				   $cy-$ry*$si*0.05,
		     $cx-$rx*$si*0.05,
				   $cy+$ry*$co*0.05,
		     $cx+$rx*$co*0.05,
				   $cy+$ry*$si*0.05,
		     -tags => "hands",
		     -fill => "cyan"
		   );
}

1;
