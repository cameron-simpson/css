#!/usr/bin/perl
#
# Attach to a Source (presumed non-seekable) and cache its contents as
# requested.
# Return a Seekable() Source.
#	- Cameron Simpson <cs@zip.com.au> 31mar97 (2nd cut)
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::IO;
use cs::Source;
use cs::Hier;

package cs::CacheSource;

@cs::CacheSource::ISA=qw(cs::Source);

sub new
	{ my($class)=shift;

	  # made cache file
	  my($F)=cs::IO::cacheFile();
	  return undef if ! defined $F;

	  # create Source
	  my($s)=new cs::Source (@_);

	  return undef if ! defined $s;

	  bless { DS		=> $s,	# actual source of data
		  CACHE		=> $F,	# seekable cache of data
		  CACHED	=> 0,	# bytes cached
		  TYPE		=> "CacheSource",
		  BUF		=> '',
		  POS		=> 0,
		}, $class;
	}

# override the super's destroy
sub DESTROY
	{}
sub Seekable
	{ 1; }
sub Seek
	{ my($this,$where)=@_;

	  warn "Seek(@_): where not defined: caller=".join('|',caller) if ! defined $where;

	  my($F)=$this->{CACHE};

	  if ($where > $this->{CACHED})
		# collect the intervening data
		{ if (! seek($F,0,2))
			{ die "can't skip to end of $F: $!";
			}

		  my($need)=$where-$this->{CACHED};
		  local($_);

		  COLLECT:
		    while ($need > 0)
			{ $_=$this->{DS}->Read(::min($need,
						     $this->{DS}->ReadSize()));
			  return undef if ! defined;	# error
			  last COLLECT if length == 0;	# EOF

			  if (! print $F $_)
				{ warn "print $F [$_]: $!";
				}
			  else
			  { $this->{CACHED}+=length;
			  }

			  $need-=length;
			}
		}

	  if (! seek($F,$where,0))
		{ die "can't seek to $where in $F: $!";
		}

	  $this->{BUF}='';
	  $this->{POS}=$where;

	  1;
	}

sub Read
	{ my($this,$size)=@_;
	  $size=$this->{DS}->ReadSize() if ! defined $size;

	  my($F)=$this->{CACHE};
	  local($_);

	  if (length ($_=$this->_FromBuf($size)))
		{ return $_;
		}

	  my($pos)=$this->Tell();
	  my($cached)=$this->{CACHED};

	  if ($pos < $cached)
		# return data from cache
		{ $size=::min($size,$cached-$pos);

		  $_='';
		  if (! read($F,$_,$size))
			{ warn "read($F,$size): $!";
			  return undef;
			}
		}
	  else
	  { warn "DEBUG: POS($pos) > CACHED($cached)"
		if $pos > $cached;

	    $_=$this->{DS}->Read($size);
	    return undef if ! defined;

	    if (seek($F,0,2))
		{ if (! print $F $_)
			{ warn "print $F [$_]: $!";
			}
		  else
		  { $this->{CACHED}+=length;
		  }
		}
	    else
	    { warn "seek($F,0,2): $!";
	    }

	    seek($F,$pos+length,0)
		|| warn "seek($F,".$pos+length($_).",0): $!";
	  }

	  $this->{POS}+=length;

	  $_;
	}

1;
