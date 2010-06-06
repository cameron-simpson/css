#!/usr/bin/perl
#
# Present a paged view of an array of lines of text.
# In normal use the array is tied to some object.
#	- Cameron Simpson <cs@zip.com.au> 29mar1997
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Page;

@cs::Page::ISA=qw();

sub new
	{ my($class,$ary,$rows,$cols)=@_;
	  $cols=80 if ! defined $cols;
	  $rows=24 if ! defined $rows;

	  $this=
	  bless { ARRAY	=> $ary,
		  ROWS	=> $rows,
		  COLS	=> $cols,
		  LINE	=> 0,
		  OFFSET => 0,
		}, $class;

	  $this->_Reset();

	  $this;
	}

sub _Reset
	{ my($this)=@_;
	  my($rows)=$this->{ROWS};
	  my($cols)=$this->{COLS};
	  my($c)=[];			# cache

	  my($l,$o)=($this->{LINE},$this->{OFFSET});
	  local($_)=($l < @{$this->{ARRAY}} ? $this->{ARRAY}->[$l] : '');
	  my($r);

	  $r=0;
	  while ($r < $rows)
		{ push(@$c,substr($_,$o,$cols));
		}
	  continue
		{ $r++;
		  $o+=$cols;
		  if ($o >= length)
			{ $l++;
			  $o=0;
			  $_=$this->{ARRAY}->[$l];
			}
		}

	  $this->{CACHE}=$c;
	}

sub Resize
	{ my($this,$rows,$cols)=@_;
	  warn "missing size (\@_=[@_])" if @_ != 3;

	  if ($cols != $this->{COLS})
		{ $this->{OFFSET}-=$this->{OFFSET}%$cols;
		}

	  $this->{ROWS}=$rows;
	  $this->{COLS}=$cols;

	  $this->_Reset();
	}

# return the nth line of the page
sub Line
	{ my($this,$row)=@_;

	  $this->{CACHE}->[$row];
	}

sub Scroll
	{ my($this,$rows)=@_;

	  if ($rows < 0)	{ $this->ScrollUp(-$rows); }
	  elsif ($rows > 0)	{ $this->ScrollDown($rows); }
	}

sub ScrollUp
	{ my($this,$rows)=@_;
	  my($cols)=$this->{COLS};

	  my($l,$o)=($this->{LINE},$this->{OFFSET});
	  local($_);

	  LINE:
	    while ($rows > 0)
		{ if ($o > 0)
			{ $o-=$cols;
			}
		  elsif ($l > 0)
			{ $l--;
			  $_=$this->{ARRAY}->[$l];
			  $o=length($_)-length($_)%$cols;
			  $reset=1;
			}
		  else
		  { last LINE;
		  }

		  $rows--;
		}

	  $this->{LINE}=$l;
	  $this->{OFFSET}=$o;

	  $this->_Reset();
	}

sub ScrollDown
	{ my($this,$rows)=@_;
	  my($cols)=$this->{COLS};

	  my($l,$o)=($this->{LINE},$this->{OFFSET});
	  local($_)=$this->{ARRAY}->[$l];
	 
	 LINE:
	    while ($rows > 0)
		{ if ($o < length($_)-$cols)
			{ $o+=$cols;
			}
		  else
		  { $l++;
		    $_=$this->{ARRAY}->[$l];
		    $o=0;
		  }

		  $rows--;
		}

	  $this->{LINE}=$l;
	  $this->{OFFSET}=$o;

	  $this->_Reset();
	}

sub Goto
	{ my($this,$line,$offset)=@_;
	  $offset=0 if ! defined $offset;

	  local($_)=$this->{ARRAY}->[$line];

	  if (length)
		{ $offset=length($_)-1 if $offset > length;
		  $offset-=$offset%$cols;
		}
	  else
	  { $offset=0;
	  }

	  $this->{LINE}=$line;
	  $this->{OFFSET}=$offset;

	  $this->_Reset();
	}

1;
