#!/usr/bin/perl
#
# My mail directory DB.
#	- Cameron Simpson <cs@zip.com.au> 
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Hier;
use cs::RFC822;

package cs::Mail::DB;

@cs::Mail::DB::ISA=qw();

sub dfltDB { "$ENV{MAILRC}.rawdb" }

sub db
{ my($db,$rw)=@_;
  $db=dfltDB() if ! defined $db;
  $rw=0 if ! defined $rw;

  new cs::Mail::DB ($db,$rw);
}

sub new
{ my($class,$db,$rw)=@_;
  $rw=0 if ! defined $rw;

  if (! ref $db)
  { $db=cs::Persist::db($db,$rw);
    return undef if ! defined $db;
  }

  map($db->{$_}=newEntry($db->{$_},$_), keys %$db);

  bless $db, $class;
}

# take addr or [addrs,...] or {EMAIL => {addr => params, ...}}
# and produce the last
sub newEntry
{ my($r,$key)=@_;

  ## warn "r:    ".cs::Hier::h2a($r,0);

  my($this)={ PHONE => {}, EMAIL => {}};

  # addr => [addrs,...]
  $r=[$r] if ! ref $r;

  # [addrs,...] => {EMAIL => {addr => params, ...}}
  if (::reftype($r) eq ARRAY)
  { map( $this->{EMAIL}->{$_}={}, @$r );
  }
  else
  {
    my($ta,$a,$v);

    SUBLIST:
      for my $sublist (EMAIL,PHONE)
      { next SUBLIST if ! exists $r->{$sublist};

	$a=$r->{$sublist};
	$ta=$this->{$sublist};

	  for (keys %$a)
	  { $v=$a->{$_};
	    if (! ref $v)
	    { $ta->{$_}={ TAGS => [$v] };
	    }
	    elsif (::reftype($v) eq ARRAY)
	    { $ta->{$_}={ TAGS => $v };
	    }
	    else
	    { $ta->{$_}=$v;
	    }
	  }
      }

    $this->{FULLNAME}=$r->{FULLNAME}
	if exists $r->{FULLNAME}
	&& length $r->{FULLNAME};
  }

  for my $sublist (EMAIL,PHONE)
  { for (keys %{$this->{$sublist}})
    { cs::Hier::fleshOut({TAGS => []},
			 $this->{$sublist}->{$_});
    }
  }

  $this->{KEY}=$key;

  ## warn "this: ".cs::Hier::h2a($this,0);

  bless $this, cs::Mail::DB;
}

sub FullName
{ my($this)=@_;

  exists $this->{FULLNAME} && length $this->{FULLNAME}
       ? $this->{FULLNAME}
       : "";
}

sub Addrs
	{ my($this)=@_;

	  ## warn "this=".cs::Hier::h2a($this,0);

	  $this->{EMAIL}={} if ! exists $this->{EMAIL};

	  wantarray ? keys %{$this->{EMAIL}} : $this->{EMAIL};
	}

sub AddrText
	{ my($this,$addr)=@_;

	  my($addrs)=scalar($this->Addrs());

	  if (! defined $addr)
		{ my(@a)=keys %$addrs;
		  return undef if ! @a;
		  $addr=shift(@a);
		}

	  my($a)=$addrs->{$addr};

	  return $this->{ADDRTEXT} if exists $a->{ADDRTEXT};

	  my($fullname)=$this->FullName();

	  $fullname =~ /[<>]/
		? "$addr ($fullname)"
		: "$fullname <$addr>"
	  ;
	}

sub MakeAliases
	{ my($this,$adb,@keys)=@_;
	  @keys=keys %$this if ! @keys;

	  ## warn "keys=[@keys]\n";

	  my(@addkeys,%addkeys,$goodShortName);
	  my($key);

	  for $key (@keys)
		{ my($e)=$this->{$key};
		  ## warn "e=$e\n";

		  my($addrs)=scalar($e->Addrs());
		  my(@addrs)=keys %$addrs;
		  ## warn "$key: [@addrs]\n";

		  my($addr,$aval,$sfx,$n,@sfx,$addrtext);

		  undef $goodShortName;
		  @addkeys=();
		  undef %addkeys;

		  $n=0;
		  for $addr (sort @addrs)
			{ $aval=$addrs->{$addr};
			  if (! ref $aval)
				{ @sfx=$aval;
				}
			  elsif (exists $aval->{TAGS})
				{ @sfx=@{$aval->{TAGS}};
				}
			  else	{ @sfx=();
				}

			  @sfx=grep(! /OLD/ && ! /BOGUS/, @sfx);
			  if (! @sfx)
				{ $n++;
				  @sfx=".$n";	# ($n > 1 ? $n : '');
				}

			  $addrtext=$e->AddrText($addr);

			  ## warn "$key => $addrtext\n";
			  for $sfx (@sfx)
			  	{
				  ## warn "$key$sfx => $addrtext\n";
				  if (! exists $addkeys{$key.$sfx})
					{ $addkeys{$key.$sfx}=$addrtext;
					}

				  if (! defined $goodShortName
				   && $sfx !~ /^\.\d+$/)
					{ $goodShortName=$key.$sfx;
					}
				}
			}

		  if (defined $goodShortName)
			{ $adb->Add($key,$addkeys{$goodShortName},1);
			}

		  for (sort keys %addkeys)
			{ $adb->Add($_,$addkeys{$_},0);
			}
		}
	}

sub NoteAddr	# (db,addrtext,$inv) -> record
	{ my($this,$addrtext,$inv)=@_;

	  ###########################
	  # parse the relevant bits from the addrtext
	  my($fullname,$addr)=cs::RFC822::addr2fullname($addrtext);

	  my($dotname,$rawfullname);

	  $dotname=$inv->{$addr} if defined $inv;

	  $fullname =~ s/\s+/ /g;
	  $fullname =~ s/^ //;
	  $fullname =~ s/ $//;

	  $rawfullname=$fullname;

	  $fullname =~ s/^"([^"]+)"$/$1/;
	  $fullname =~ s/^'([^']+)'$/$1/;
	  $fullname =~ s/^ //;
	  $fullname =~ s/ $//;

	  return undef if ! length $fullname;	# nothing worth noting

	  if (! defined $dotname)
	  	{ ($dotname=$fullname) =~ s/"[^"]*"//g;
		  $dotname =~ s/\s+/ /g;
		  $dotname =~ s/(\w)(\w*)/\u$1\L$2\E/g;
		  $dotname =~ s/[-\@\W]+/./g;
		  $dotname =~ s/^\.//;
		  $dotname =~ s/\.$//;
		}

	  return undef if ! length $dotname;

	  $addr =~ s/^\s+//;
	  $addr =~ s/\s+$//;
	  $addr =~ s/^<(.*)>$/$1/;
	  if ($addr !~ /\@/)
	  	{ $addr.="\@$ENV{SITENAME}" unless $addr =~ /\@/;
		}
	  $addr = lc($addr);

	  ###########################
	  # ok, we have the bits - file them away

	  my($rec)=newEntry($addr,$dotname);

	  # reverse map
	  if (defined $inv)
	  	{ $inv->{$addr}=$dotname;
		}

	  if (! exists $this->{$dotname})
		# new record
		{ ## warn "new record for $dotname: ".cs::Hier::h2a($rec,0);
		  $this->{$dotname}=$rec;
		}
	  else
		# existing record - update
		{
		  my($e)=$this->{$dotname};

		  ## my(@c)=caller;
		  ## warn "e=".cs::Hier::h2a($e,0)." from [@c]";

		  my($a)=scalar($e->Addrs());
		  ## warn "update record for $dotname: ".cs::Hier::h2a($rec,0);

		  map(exists $a->{$_} || ($a->{$_}=$rec->{EMAIL}->{$_}),
			keys %{$rec->{EMAIL}});
		}

	  $this->{$dotname}->{FULLNAME}=$fullname
		if ! exists $this->{$dotname}->{FULLNAME};

	  $this->{$dotname};
	}

1;
