#!/usr/bin/perl
#
#	- Cameron Simpson <cs@zip.com.au> 
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::HASH;
use cs::RemappedHash;
use cs::FlatHash;
use cs::DB;

package cs::DB::Meta;

@cs::DB::Meta::ISA=qw(cs::HASH);

%cs::DB::Meta::Class
    =(
	EXT		=> { TABLE	=> [ 'wiring', 'phones' ],
			     PATH	=> {
					   },
			   },
	HOST		=> { TABLE	=> 'hosts',
			     PATHS	=> {
					   },
			   },
	DESK		=> { TABLE	=> 'floorplan',
			     PATHS	=> { HOST => "HOST{DESK}",
					     USER => "USER{DESK}",
					   },
			   },
	PORT		=> { TABLE	=> sub { my($pdb)={};
						 my($ddb)=db(DESK);

						 my($d);

						 DESK:
						  for my $desk (keys %$ddb)
						   { $d=$ddb->{$desk};
						     next DESK if ! exists $ddb->{$desk};
						     for my $psfx (A,B,C)
						       { $pdb->{"$desk$psfx"}->{DESK}=$desk;
						       }
						   }

						 ## warn "PORTS=".cs::Hier::h2a($pdb,1);
						 $pdb;
					       },
			     PATHS	=> { USER	=> [ "HOST{PORT}",
							     "->USER" ],
					     HOST	=> "HOST{PORT}",
					     SWITCH	=> "SWITCH{PORT}",
					   },
			   },
	ETHER		=> { TABLE	=> sub { my($edb)={};
						 my($hdb)=db(HOST);
						 my($H,$P,$eaddr);

						 HOST:
						   for my $host (keys %$hdb)
						    { $H=$hdb->{$host};
						      if (! exists $H->{PORT})
							{ ## warn "$::cmd: ethers: no ports on \"$host\"!\n";
							  next HOST;
							}

						      PORT:
						       for my $port (keys %{$H->{PORT}})
							{ $P=$H->{PORT}->{$port};
							  if (! exists $P->{ETHADDR})
							  { warn "$::cmd: host \"$host\" port \"$port\": no ethernet address!\n"
								if $H->{TYPE} ne HUB && $H->{TYPE} ne SWITCH && $port !~ /^unpatched/;
							    next PORT;
							  }
							
							  $eaddr=$P->{ETHADDR};
							  if ($eaddr !~ /^[0-9a-f]?[0-9a-f](:[0-9a-f]?[0-9a-f]){5}$/i)
								{ warn "$::cmd: host \"$host\" port \"$port\": bad ethernet address \"$eaddr\"\n";
								}
							  else
							  { $edb->{$eaddr}={ HOST => $host, PORT => $port };
							  }
							}
						    }

						 $edb;
					       },
			     PATHS	=> {
					   },
			   },
	SWITCH		=> { TABLE	=> sub { cs::FlatHash::flattened(
						   cs::DB::db(['wiring',
								  'switches'],@_),
								 2)
					       },
			     PATHS	=> { DESK	=> ["->PORT",
							    PORT,
							    "->DESK"],
					     HOST	=> ["->PORT",
							    "HOST{PORT}"],
					     USER	=> [ "->PORT",
							     "HOST{PORT}",
							     "->USER",
							     USER,
							   ],
					     ETHER	=> "ETHER{PORT}",
					   },
			   },
     );
%cs::DB::Meta::DBs=();

sub toplevel
{
  my($db)={};

  tie (%$db, cs::DB::Meta, TOPLEVEL, @_)
	|| die "$::cmd: can't tie to TOPLEVEL";

  $db;
}

sub db
{ my($db)=toplevel();

  tie (%$db, cs::DB::Meta, DB, @_)
	|| die "$::cmd: can't tie to TOPLEVEL";

  $db;
}

# get key from named db
# (convenience routine)
sub get
{ my($dbname,$key,$rw)=@_;
  $rw=0 if ! defined $rw;

  db($dbname,$rw)->{$key};
}

sub TIEHASH
{ my($class,$type)=(shift,shift);

  if ($type eq DB)
	{ return dbTIEHASH($class,@_);
	}
  elsif ($type eq TOPLEVEL)
	{ return bless { TYPE => TOPLEVEL }, $class;
	}
  else
  { my(@c)=caller;
    die "TIEHASH on bogus type \"$type\" with args (@_) from [@c]";
  }
}

sub dbTIEHASH($$;$)
{ my($class,$odbname,$rw)=@_;
  $rw=0 if ! defined $rw;

  return $cs::DB::Meta::DBs{$odbname}
	if exists $cs::DB::Meta::DBs{$odbname};

  ## warn "load db $odbname ...\n";

  my($dbname,$remapped);

  if ($odbname =~ /\{([A-Z]+)\}$/)
  { $remapped=$1;
    $dbname=$`;
  }
  else	{ $dbname=$odbname;
	}

  die "$::cmd: no MetaDB named \"$dbname\""
	if ! exists $cs::DB::Meta::Class{$dbname};

  my($cldef)=$cs::DB::Meta::Class{$dbname};
  my($table)=$cldef->{TABLE};
  my($rawdb);

  if (ref $table && ::reftype($table) eq CODE)
  {
    ## warn "calling $table($rw)";
    $rawdb=&$table($rw);
    ## warn "rawdb=$rawdb\n";
  }
  else	{
	  my($keychain)=$cldef->{TABLE};
	  $keychain=[ $keychain ] if ! ref $keychain;

	  $rawdb=cs::DB::db($keychain,$rw);

	  die "$::cmd: can't attach to [@$keychain]: $!"
		if ! defined $rawdb;
	}

  my($this);

  $this	=$cs::DB::Meta::DBs{$odbname}
	=bless { DBTYPE	=> $odbname,
		 TYPE	=> DB,
		 CLDEF	=> $cldef,
		 GOT	=> {},
	       }, $class;

  my($isRemapped)=defined $remapped;

  if ($isRemapped)
  { my($remobj);

    ($rawdb,$remobj)=cs::RemappedHash::remapped($rawdb,$remapped);
    die "$::cmd: can't remap db by \"$remapped\""
	  if ! defined $rawdb;

    $this->{REMAPOBJ}=$remobj;
  }

  $this->{DB}=$rawdb;

  $this;
}

sub finish
{ cs::DB::finish();
}

sub KEYS
{ my($this)=@_;

  my($type)=$this->{TYPE};

  return keys %cs::DB::Meta::Class if $type eq TOPLEVEL;

  return keys %{$this->{DB}} if $type eq DB;

  die "KEYS $this where type == \"$type\"";
}

sub EXISTS
{ my($this,$key)=@_;

  my($type)=$this->{TYPE};

  if ($type eq TOPLEVEL)
  { my($db)=db($key);

    return defined $db;
  }

  die "EXISTS($this,\"$key\") where type == \"$type\""
	if $type ne DB;

  return exists $this->{DB}->{$key};
}

sub FETCH
{ my($this,$key)=@_;

  my($type)=$this->{TYPE};

  if ($type eq TOPLEVEL)
  { my($db)={};

    tie (%$db, cs::DB::Meta, DB, $key)
	  || die "$::cmd: can't tie to db \"$key\"";

    return $db;
  }

  if ($type eq DB)
  { return $this->{DB}->{$key};
  }

  die "FETCH($this,\"$key\") where type == \"$type\"";
}

sub DELETE
{ my($this,$key)=@_;

  return undef if ! $this->EXISTS($key);

  my($type)=$this->{TYPE};

  if ($type eq DB)
	{ return delete $this->{DB}->{$key};
	}

  die "DELETE($this,\"$key\") where type == \"$type\"";
}

sub STORE
{ my($this,$key,$value)=@_;

  my($type)=$this->{TYPE};

  if ($type eq DB)
	{ return $this->{DB}->{$key}=$value;
	}

  die "STORE($this,\"$key\") where type == \"$type\"";
}

sub RawKey
{ my($this,$key)=@_;
  die "$::cmd: RawKey called on type \"$this->{TYPE}\""
	if $this->{TYPE} ne DB;

  if (! defined $key) {my(@c)=caller;warn "no key! from [@c]";}
  return $key if ! exists $this->{REMAPOBJ};

  my($rawkey)=scalar($this->{REMAPOBJ}->MapKey($key));

  if (! defined $rawkey)
	{my(@c)=caller;warn "no RawKey(@_) from [@c]";}

  return $rawkey;
}

# get wrapper object by key from db tieobj
sub Get(\%$;$)
{ my($this,$key,$rw)=@_;
  $rw=0 if ! defined $rw;

  die "$::cmd: Get(@_) on type \"$this->{TYPE}\""
	if $this->{TYPE} ne DB;

  return $this->{GOT}->{$key} if exists $this->{GOT}->{$key};

  my($db)=$this->{DB};

  return undef if ! exists $db->{$key};

  my($item)=
  bless { DB	=> $db,
	  TYPE	=> $this->{DBTYPE},
	  KEY	=> $key,
	  RAWKEY=> $this->RawKey($key),
	  PARENT=> $this,
	}, cs::DB::Meta;

  $this->{GOT}->{$key}=$item;
}

# get the db element from the wrapper object
sub Item
{ my($this)=@_;

  die "$::cmd: Item(@_) on type \"$this->{TYPE}\""
	if $this->{TYPE} eq DB;

  return undef if ! exists $this->{DB}->{$this->{KEY}};

  $this->{DB}->{$this->{KEY}};
}

sub PutItem(\%$)
{ my($this,$key,$value)=@_;

  $this->{DB}->{$key}=$value;
}

sub Find
{ my($this,$rel)=@_;

  my($type)=$this->{TYPE};

  die "$::cmd: Find(@_) on type \"$type\""
	if $type eq DB;

  my(@matches)=();

  my($key)=$this->{KEY};
  my($db)=$this->{DB};

  if (! exists $db->{$key})
	{ warn "$::cmd: this key \"$key\" not active any more!";
	}
  else
  { my($cldef)=$cs::DB::Meta::Class{$this->{TYPE}};
    my($paths)=$cldef->{PATHS};
    my($item)=$db->{$key};

    if (exists $paths->{$rel})
	# ignore direct hook - use this to get where we need to be
    {
      my(@sofar)=$this;
      my($path)=$paths->{$rel};
      my(@path)=(ref $path ? @$path : $path);

      ## warn "path($rel)=[@path]...\n";

      # we start here and iterate over the path,
      # doing a breadth-first traversal of the dbs
      my($ndbtie,$sub,$subitem);

      my(@umatches)=$key;
      $ndbtie=$this->{PARENT};

      PATHEL:
	for my $pathel (@path)
	{
	  ## warn "PATHEL=[$pathel], umatches=[@umatches]\n";

	  @matches=();
	  if ($pathel =~ /^->/)
		# just map to the field on this hop
	  { my($field)=$';

	    FKEY:
	      for my $pkey (@umatches)
	      {
		## warn "dbtie=$ndbtie, Get($pkey)\n";
		next FKEY if ! defined ($sub=$ndbtie->Get($pkey));
		
		$subitem=$sub->Item();
		if (exists $subitem->{$field})
		  { my($f)=$subitem->{$field};
		    if (ref $f && ::reftype($f) eq ARRAY)
			  { push(@matches,@$f);
			  }
		    else	{ push(@matches,"$f");
			  }
		  }
	      }

	    ## warn "matched [@matches] from $pathel\n";
	  }
	  else
	  # it's a db name
	  # fetch db, look up keys in that db,
	  # return real keys for its original db
	  # switch to the origin db to user the real key
	  {
	    $ndbtie=tied %{db($pathel)};
	    die "no such db as \"$pathel\"" if ! defined $ndbtie;

	    ## warn "get [@umatches] from $pathel...\n";
	    SUBKEY:
	      for my $pkey (@umatches)
	      {
		if (defined ($subitem=$ndbtie->Get($pkey)))
		{ 
		  if (! exists $subitem->{RAWKEY}
		   || ! defined $subitem->{RAWKEY})
		  {warn "no RAWKEY";
		   cs::DEBUG::phash($subitem);
		  }
		  push(@matches,$subitem->{RAWKEY});
		  if ($pathel =~ /(\w+)\{\w+\}$/)
		  { $ndbtie=tied %{db($1)};
		  }
		}
	      }

	    ## warn "matched [@matches] from $pathel";
	  }

	  @umatches=(@matches ? ::uniq(@matches) : ());
	}
    }
    else
    {
      if (! exists $item->{$rel})
      { warn "$::cmd: not rel field \"$rel\" in item \"$key\"";
      }
      else
      { my($val)=$item->{$rel};

	if (ref $val)
	{ my($reftype)=::reftype($val);

	  if ($reftype eq ARRAY)
	  { @matches=@$val;
	  }
	  elsif ($reftype eq HASH)
		{ @matches=keys %$val;
		}
	  else
	  { warn "$::cmd: \"$key\"->{$rel} is odd type ($reftype)";
	  }
	}
	else
	{ @matches=$val;
	}
      }
    }
  }

  wantarray ? @matches : $matches[0];
}

1;
