#!/usr/bin/perl
#
# Save and assemble message/partial MIME objects.
#	- Cameron Simpson <cs@zip.com.au> 24sep96
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Upd;
use cs::Pathname;
use cs::Sink;

package cs::MIME::Partial;

@cs::MIME::Partial::ISA=qw(MIME);

sub Save
	{ my($this,$dir)=@_;

	  if (! defined $dir) { $dir=Misc::tmpDir(); }

	  my($pdir)="$dir/MIME-Partial";

	  die "tried to save non-\"Message/Partial\" item"
		if $this->{TYPE} ne MESSAGE
		|| $this->{SUBTYPE} ne PARTIAL;

	  my($p)=$this->{TYEPARAMS};
	  my($id,$part,$total)=( $p->{ID}, $p->{NUMBER}+0, $p->{TOTAL}+0 );

	  Upd::err("cs::MIME::Partial::Save: id=$id, part=$part, total=$total\n");

	  warn "huh? bad part($part) [total=$total, id=$id]"
		if $part < 1 || ($total > 0 && $part > $total);

	  my($savedir)="$dir/$id";
	  my($saveas)="$savedir/$part";

	  makedir($savedir) || warn "makedir($savedir): $!";

	  my($s)=(new cs::Sink PATH, $saveas);

	  return undef if ! defined $s;

	  $this->{DS}->CopyTo($s);

	  undef $s;

	  $this->_TryAssemble($dir);
	}

sub _TryAssemble
	{ my($this,$dir)=@_;
	  my($id)=$this->{TYEPARAMS}->{ID};
	  my($pdir)="$dir/MIME-Partial/$id";

	  return if ! length $this->{TYEPARAMS}->{TOTAL};

	  my($i);

	  for $i (1..$this->{TYEPARAMS}->{TOTAL})
		{ return if ! -e "$pdir/$i";
		}

	  # all parts are present - assemble
	  my($cdir)="$dir/MIME-Complete";
	  makedir($cdir) || warn "makedir($cdir): $!";
	  my($saveas)="$cdir/$id";
	  my($out)=new cs::Sink PATH, $saveas;

	  return undef if ! defined $s;

	  my($in,$ok);

	  $ok=1;

	  ASSEMBLE:
	    for $i (1..$this->{TYEPARAMS}->{TOTAL})
		{ $in=new cs::Source PATH, "$pdir/$i";
		  if (! defined $in)
			{ $ok=0;
			  last ASSEMBLE;
			}

		  $in->CopyTo($out);
		}

	  if (! $ok)
		{ if (! unlink($saveas))
			{ warn "unlink($saveas): $!";
			}

		  return undef;
		}

	  undef $out;

	  for $i (1..$this->{TYEPARAMS}->{TOTAL})
		{ if (! unlink("$pdir/$i"))
			{ warn "unlink($pdir/$i): $!";
			}
		}

	  system("rm -rf '$pdir'");

	  $saveas;
	}

1;
