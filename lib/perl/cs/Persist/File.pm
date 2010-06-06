#!/usr/bin/perl
#
# Save/restore a hash in a text file.
#	- Cameron Simpson <cs@zip.com.au> 11jun97
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::HASH;
use cs::Hier;
use cs::IO;
use Fcntl;
use cs::Source;
use cs::Sink;
use cs::Pathname;
use cs::Flags;
use cs::FlaggedObject;

package cs::Persist::File;

@cs::Persist::File::ISA=(cs::Persist,,cs::FlaggedObject,cs::HASH);

$cs::Persist::File::DEBUG=0;

$cs::Persist::File::CvsPath='/usr/local/bin/cvs';

sub TIEHASH
{
  my($class,$fname,$rw,@c)=@_;
  $rw=0 if ! defined $rw;

  my $this;

  if (defined ($this=cs::Persist::_reg($fname)))
  { $this->SetReadWrite($rw) if $rw;
    return $this;
  }

  $this    ={ LIVE	=> {},	# live copy, seen by caller
	      META	=> {},
	      FNAME	=> $fname,
	      FLAGS	=> (new cs::Flags @cs::Persist::DfltFlags),
	      PID	=> $$,
	      DEBUG	=> 0,
	      CHANGELOG	=> [],
	    };

  bless $this, $class;

  $this->_Register($fname);

  ## warn "TIEHASH(fname=$fname,rw=$rw)";
  $this->SetReadWrite($rw);
  $this->_Load($fname) || $this->SetReadWrite(0);

  $this;
}

sub DESTROY
{
  my($this)=@_;

  ## warn "DESTROY($this->{FNAME},FLAGS=@{$this->Flags()})\n";

  return if ! $this->IsReadWrite();
  $this->Sync();
  $this->_Unregister($this->{FNAME});
}

sub Sync
{ my($this,$force)=@_;
  $force=0 if ! defined $force;

  ##warn "Sync($this->{FNAME})" if $this->{FNAME} =~ /\.toc\.db/;
  return if ! $this->IsReadWrite() || $this->{PID} != $$;

##warn "flushing $this->{FNAME}\n" if $this->{FNAME} =~ /\.toc\.db/;

  if (-e $this->{FNAME} && ! -w $this->{FNAME})
  { warn "can't rewrite \"$this->{FNAME}\"";
    warn "new data was:\n".cs::Hier::h2a($this->{LIVE},1)."\n";
    warn "old data was:\n".cs::Hier::h2a($this->{DATA},1)."\n";
  }
  else
  { ## warn "REWRITE $this->{FNAME}\n";
    $this->WriteSelf($this->{FNAME});
  }
}

sub _CvsFile
	{ return;

	  my($this)=@_;
	  my($fname)=$this->{FNAME};
	  my($fdir)=cs::Pathname::dirname($fname);
	  my($fbase)=cs::Pathname::basename($fname);
	  my($cvsdir)="$fdir/CVS";
	  my($why)=join("\n",@{$this->{CHANGELOG}});

	  if (-d $cvsdir)
		{ $why  =~ s/'/'\\''/g;
		  $fdir =~ s/'/'\\''/g;
		  $fbase=~ s/'/'\\''/g;
		  system("exec >&2; set -x; cd '$fdir' || exit \$?; $cs::Persist::File::CvsPath -q commit -m '$why' '$fbase'");
		  $this->{CHANGELOG}=[];
		}
	  else	{ ## warn "no $cvsdir - CVS skipped for $fname";
		}
	}

sub LogChange
	{ my($this)=shift;
	  push(@{$this->{CHANGELOG}},@_);
	}

sub _Load
{ my($this,$fname)=@_;
  die "\$fname not defined" if ! defined $fname;

  $this->{FNAME}=$fname;

  my $ok = 1;

  ###########################
  # load initial data
  if (! stat($fname))
	{ }
  elsif (-d _)
	{ warn "$::cmd: $fname is a directory!";
	  $ok=0;
	}
  else
  { my $s;

    if (! defined ($s=new cs::Source (PATH,$fname)))
    { warn "$::cmd: can't open $fname: $!";
      undef $ok;
    }

    ## warn "opened($fname)";

    # load data from file
    { my($key,$datum);

      ## warn "load($fname)..." if exists $ENV{USER} && $ENV{USER} eq 'cameron' && -t 2;
      ## cs::DEBUG::pstack();

      my $kvline;

      HASHLINE:
      while (defined ($kvline=cs::Hier::getKVLine($s,1)))
      { last HASHLINE if $kvline eq EOF;

	if (! ref $kvline)
	{ warn "$::cmd: $fname: getKVLine said \"$kvline\", marking table as read/only";
	  $this->SetReadWrite(0);
	  $ok=0;
	}
	else
	{ my ($key,$datum)=@$kvline;
	  if (! length $key)
	  { $this->{META}=cs::Hier::a2h($datum);
	  }
	  else
	  { $this->STORE($key,$datum,1);
	  }
	}
      }
    }
  }
  ## warn "loaded\n";

  $ok;
}

# write 
sub WriteSelf
{ my($this,$fname)=@_;
  $fname=$this->{FNAME} if ! defined $fname;

  die "WriteSelf($fname) when ! RW" if ! $this->IsReadWrite();

  if (-l $fname)
	{ warn "$::cmd: won't rewrite symlinks ($fname)";
	  return undef;
	}

  { my $s;

    if (! defined ($s=new cs::Sink (PATH, $fname)))
    { warn "$::cmd: can't save to $fname: $!";
      unlink($fname) || warn "$::cmd: unlink($fname): $!\n";
      return undef;
    }

    my($unparsed,$live)=($this->{UNPARSED}, $this->{LIVE});

    warn "$fname: \$this->{META}=".cs::Hier::h2a($this->{META})
	if ::reftype($this->{META}) ne HASH;

    if (keys %{$this->{META}})
    { ## warn "WRITE META\n";
      $s->Put(sprintf("%-15s ","\"\""));
      cs::Hier::h2s($s,$this->{META},1,0,0,16);
      $s->Put("\n");
    }

    for my $key (sort($this->KEYS()))
    {
      ## warn "write $key to $fname";
      $s->Put(sprintf("%-15s ",
		      cs::Hier::_scalar2a($key)));

      if (exists $live->{$key})
      { cs::Hier::h2s($s,$live->{$key},1,0,0,16);
      }
      else
      { $s->Put($unparsed->{$key});
      }

      $s->Put("\n");
    }
  }

  $this->_CvsFile();
}

sub DELETE
	{ my($this,$key)=@_;
	  delete $this->{UNPARSED}->{$key};
	  delete $this->{LIVE}->{$key};
	}

sub STORE
{ my($this,$key,$value,$needsparse)=@_;
  $needsparse=0 if ! defined $needsparse;

  if ($needsparse)
	{ delete $this->{LIVE}->{$key};
	  ## warn "store $key unparsed\n";
	  $this->{UNPARSED}->{$key}=$value;
	}
  else	{ delete $this->{UNPARSED}->{$key};
	  ## warn "store $key live\n";
	  $this->{LIVE}->{$key}=$value;
	}

  $value;
}

sub FETCH
{ my($this,$key)=@_;
  return $this if ! length $key;

  if (! $this->EXISTS($key))
  { ## warn "forging new hash for \"$key\"";
    return $this->STORE($key,{});
  }

  return $this->{LIVE}->{$key} if exists $this->{LIVE}->{$key};

  ## warn "parse $key\n";
  my($data,$unparsed)=cs::Hier::a2h($this->{UNPARSED}->{$key});
  $unparsed =~ s/^[ \t\r\n]+//;
  if (length $unparsed)
  { warn "$0: $this->{FNAME}, key \"$key\": unparsed data: $unparsed\n";
  }

  delete $this->{UNPARSED}->{$key};
  $this->{LIVE}->{$key}=$data;

  $data;
}

sub EXISTS
{
  my($this,$key)=@_;
  ## warn "EXISTS(@_)" if $key eq 'L5-010A';

  return 1 if exists $this->{UNPARSED}->{$key}
	   || exists $this->{LIVE}->{$key};

  0;
}

sub KEYS
{ my($this)=@_;
  ::uniq(keys %{$this->{UNPARSED}}, keys %{$this->{LIVE}});
}

sub SetReadWrite
{ my($this,$rw)=@_;
  $rw=1 if ! defined $rw;
  $rw=0 if ! $rw;	# don't ask :-(

  ## {my(@c)=caller;warn "SetReadWrite(rw=$rw,fname=$this->{FNAME}) from [@c]"}

  $rw ? $this->Set(RW) : $this->Clear(RW);
}

1;
