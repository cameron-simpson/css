#!/usr/bin/perl
#
# Save/restore a hash in a directory.
# The directory entries are %escaped for "%" and "/", and for a leading ".".
# The zero-length key is still not supported.
#	- Cameron Simpson <cs@zip.com.au> 11jun97
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::HASH;
use cs::Misc;
use cs::Hier;
use cs::Source;
use cs::Sink;
use cs::Shell;
use cs::Flags;
use cs::FlaggedObject;

package cs::Persist::Dir;

@cs::Persist::Dir::ISA=(cs::Persist,cs::FlaggedObject,cs::HASH);

$cs::Persist::Dir::DEBUG=0;

sub TIEHASH
	{
	  my($class,$dir,$rw)=@_;
	  $rw=0 if ! defined $rw;

	  my $this;

	  if (defined ($this=cs::Persist::_reg($dir)))
		{ $this->SetReadWrite($rw) if $rw;
		  return $this;
		}

	  ## warn "TIEHASH $class $dir rw=$rw";

	  $this    ={ DATA	=> {},	# bound data
		      LIVE	=> {},	# live data - seen by caller
		      DIR	=> $dir,
		      META	=> {},
		      FLAGS	=> (new cs::Flags @cs::Persist::DfltFlags),
		      DEBUG	=> 0,
		    };

	  bless $this, $class;

	  $this->_Register($dir);

	  -e $dir || mkdir($dir,0777) || warn "can't mkdir($dir): $!";

	  my($metafile)=_MetaFile($this);

	  my($ms)=new cs::Source (PATH,$metafile);
	  local($_);
	  if (defined $ms && defined ($_=$ms->GetLine) && length)
		{ $this->{META}=cs::Hier::a2h($_);
		}

	  $this->SetReadWrite($rw);

	  $this;
	}

sub _MetaFile { shift->{DIR}."/.persist-meta" }

sub DESTROY
	{ my($this)=@_;

	  $this->_Unregister($this->{DIR});
	  return if ! $this->IsReadWrite();

#	  if ($this->{DIR} =~ /cameron$/)
#		{ my(@k)=keys %{$this->{LIVE}};
#		  warn "LIVE keys=[@k]";
#		  @k=keys %{$this->{DATA}};
#		  warn "DATA keys=[@k]";
#		}

	  # save metadata
	  my($metafile)=$this->_MetaFile();
	  if (ref $this->{META} && keys %{$this->{META}})
		{ my($ms)=new cs::Sink (PATH,$metafile);
		  if (defined $ms)
			{ $ms->Put(cs::Hier::h2a($this->{META},0)."\n");
			}
		  else
		  { warn "can't save metadata to $metafile: $!";
		  }
		}
	  else
	  { -e $metafile && unlink($metafile);
	  }

	  $this->Sync();
	}

sub _newDatum
	{ my($this,$key)=@_;
	  my($h);

	  my($path)=$this->Path($key);
	  my($minDepth)=(exists $this->{META}->{MINDEPTH}
				&& $this->{META}->{MINDEPTH} > 0
			 ? $this->{META}->{MINDEPTH}
			 : undef);

	  if (defined $minDepth)
		{ -e $path || mkdir($path,0777) || warn "mkdir($path): $!";
		}

	  $this->{DATA}->{$key}=$h=cs::Persist::db($this->Path($key),
						   $this->IsReadWrite());

	  if (defined $minDepth)
		{ $h->{''}->{META}->{MINDEPTH}=$minDepth-1;
		}

	  $h;
	}

sub Sync
	{ my($this)=@_;

	  my($live,$data);

	  # sync any live copies with the store
	  SYNC:
	    for my $key (sort keys %{$this->{LIVE}})
		{
		  ## warn "sync $key...";
		  $live=$this->{LIVE}->{$key};

		  if (exists $this->{DATA}->{$key})
			{ $data=$this->{DATA}->{$key};
			  next SYNC if "$live" eq "$data";
			}
		  else	{
			  $data=$this->_newDatum($key);
			}

		  # remove extraneous keys
		  for my $subkey (keys %$data)
			{ 
			  ## warn "purge $key/$subkey";
			  delete $data->{$subkey}
				if ! exists $live->{$subkey};
			}

		  # update new or changed keys
		  for my $subkey (keys %$live)
			{
			  if (! exists $data->{$subkey}
			   || cs::Hier::hcmp($live->{$subkey},$data->{$subkey}) != 0)
				{ $data->{$subkey}=$live->{$subkey};
				}
			}

		  ## warn "data impl is $data->{''}";

		  $data->{''}->Sync();
		}
	}

sub SetReadWrite
	{ my($this,$rw)=@_;
	  $rw=1 if ! defined $rw;
	  $rw=0 if ! $rw;	# don't ask :-(

	  ## {my(@c)=caller;warn "Dir::SetReadWrite(rw=$rw,dir=$this->{DIR}) from [@c]";}
	  $rw ? $this->Set(RW) : $this->Clear(RW);
	  if ($rw)
		{ 
		  my($o);

		  for my $livekey (keys %{$this->{LIVE}})
			{ if (defined ($o=tied %{$this->{LIVE}->{$livekey}}))
				{ $o->SetReadWrite(1);
				}
			}

		  # all DATA elements are known to be tied
		  for my $datakey (keys %{$this->{DATA}})
			{ (tied %{$this->{DATA}->{$datakey}})
				->SetReadWrite(1);
			}
		}
	}

sub Normalise
	{ my($this,$key)=@_;
	  if (! defined $key)
		{ my(@c)=caller;
		  warn "no key (from @c)" if ! defined $key;
		}
	  if (! length $key)
		{ my(@c)=caller;
		  warn "zero length keys forbidden (from @c)" if ! defined $key;
		}

	  # escape special names
	  if ($key eq CVS)
		{ $key =~ s/^./sprintf("%%%02x",ord($&))/e;
		}
	  else
	  # escape problematic characters
	  { $key =~ s/[%\/\000]/sprintf("%%%02x",ord($&))/eg;
	    $key =~ s/^\./%2e/;
	  }

	  # make sure
	  if ($key =~ m:/: || $key eq '' || $key eq '.' || $key eq '..')
		{ my(@c)=caller;
	  	  die "bad key ($key) from [@c]";
		}

	  $key;
	}

sub DeNormalise
	{
	  my($this,$nkey)=@_;

	  # undo escaping
	  $nkey =~ s/\%([\da-f]{2})/chr(hex($1))/egi;

	  $nkey;
	}

sub DELETE
	{ my($this,$key)=@_;
	  return undef if ! $this->EXISTS($key);

	  # my(@c)=caller;
	  # warn "DELETE($key) from [@c]";

	  # first, delete the key so we blow away the children
	  # before we blow away the parent
	  if (exists $this->{DATA}->{$key})
		{ my $o = tied($this->{DATA}->{$key});
		  if (defined $o)
			# make sure it doesn't save itself
			{ $o->SetReadWrite(0);
			}
		  delete $this->{DATA}->{$key};
		}
	  if (exists $this->{LIVE}->{$key})
		{ my $o = tied($this->{LIVE}->{$key});
		  if (defined $o)
			# make sure it doesn't save itself
			{ $o->SetReadWrite(0);
			}
		  delete $this->{LIVE}->{$key};
		}

	  my($path)=$this->Path($key);

	  # now blow away any remaining data
	  # silently leave data alone in fork()ed children
	  if (-e $path && $this->{PID} eq $$)
		{
		  if (-d $path)
			{
			  my(@s)=cs::Shell::quote('rm','-rf',$path);
			  system(@s);
			}
		  else
		  {
		    warn "unlink($path)";
		    if (! unlink($path))
			{ warn "unlink($path): $!";
			}
		  }
		}
	}

sub STORE
	{ my($this,$key,$value)=@_;

	  if (! length $key)
		{ my(@c)=caller;
		  die "can't store zero-length key from [@c]";
		}

	  if (! ref $value
	   || cs::Hier::reftype($value) ne HASH)
		{ warn "can't store nonHASH for {$key} in directory \"$this->{DIR}\"";
		  warn "value=".cs::Hier::h2a($value,0);
		  return undef;
		}

	  ## warn "stash $key=$value";

	  $this->{LIVE}->{$key}=$value;
	}

sub FETCH
	{ my($this,$key)=@_;
	  return $this if ! length $key;

	  ## warn "FETCH($key)" if $key eq 'peterb' || $key eq '19961230';

	  if (exists $this->{LIVE}->{$key})
		{ ## warn "returning LIVE copy for FETCH($key)";
		  my($ret)=$this->{LIVE}->{$key};

		  if (tied(%$ret) && $this->IsReadWrite())
			{ warn "propagate RW to $key";
			  (tied %$ret)->SetReadWrite(1);
			}

		  return $ret;
		}

	  if (! $this->EXISTS($key))
		# new item
		{ return $this->STORE($key,{});
		}
	  
	  _fetch($this,$key);
	}

sub _fetch
	{ my($this,$key)=@_;

	  if (! exists $this->{DATA}->{$key})
		# presumably $this->{DIR}/$key exists
		{
		  $this->_newDatum($key);
		}

	  ## warn "returning DATA copy ($this->{DATA}->{$key}) for FETCH($key)";

	  my($ret)=$this->{DATA}->{$key};

	  if (tied(%$ret) && $this->IsReadWrite())
		{ ## warn "propagate RW to $key";
		  (tied %$ret)->SetReadWrite(1);
		}

	  $ret;
	}

sub EXISTS
	{ my($this,$key)=@_;
	  
	  ## warn "EXISTS($key)" if $this->{DEBUG};

	  return 1 if exists $this->{LIVE}->{$key};
	  return 1 if exists $this->{DATA}->{$key};

	  my($kpath)=$this->Path($key);

	  return 1 if -e $kpath;

	  0;
	}

sub KEYS
	{
	  my($this)=@_;

	  ::uniq(   keys %{$this->{LIVE}},
		    keys %{$this->{DATA}},
		    map($this->DeNormalise($_),
			grep(/^[^.]/,cs::Pathname::dirents($this->{DIR}))));
	}

sub Path
	{ my($this,$key)=@_;
	  my($nkey)=$this->Normalise($key);

	  "$this->{DIR}/$nkey";
	}

sub LogChange
	{ my($this)=shift;

	  # log the change in subfiles
	  for my $datakey (keys %{$this->{DATA}})
		{ $this->{DATA}->{$datakey}->{''}->LogChange(@_);
		}
	}

1;
