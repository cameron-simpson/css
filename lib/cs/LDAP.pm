#!/usr/bin/perl
#
# LDAP routines.
#	- Cameron Simpson <cs@zip.com.au> 07jan1998
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Shell;
use cs::Source;
use cs::Sink;

package cs::LDAP;

$cs::LDAP::Host=defined $ENV{LDAP_HOST} ? $ENV{LDAP_HOST} : 'ldap';
$cs::LDAP::BaseDN=$ENV{LDAP_BASEDN};
$cs::LDAP::AdminDN=defined $ENV{LDAP_ADMIN}
			? $ENV{LDAP_ADMIN}
			: "uid=admin, $cs::LDAP::BaseDN";

sub new
{ my($class,$host,$basedn,$admin)=@_;
  $host=$cs::LDAP::Host if ! defined $host;
  $basedn=$cs::LDAP::BaseDN if ! defined $basedn;
  $admin=$cs::LDAP::AdminDN if ! defined $admin;

  bless { BASEDN	=> $basedn,
	  HOST		=> $host,
	  ADMIN		=> $admin,
	  PASSWORD	=> undef,
	}, $class;
}

sub _SQParam
{ my($this,$field,$newvalue)=@_;
  $this->{$field}=$newvalue if defined $newvalue;
  $this->{$field};
}
sub BaseDN { my($this)=shift; $this->_SQParam(BASEDN,@_); }
sub Host   { my($this)=shift; $this->_SQParam(HOST  ,@_); }
sub AdminDN{ my($this)=shift; $this->_SQParam(ADMIN ,@_); }
sub Password{ my($this)=shift; $this->_SQParam(PASSWORD,@_); }

sub Query
{ my($this,$query,$basedn,$host)=@_;
  die "no query!" if ! defined $query;
  $basedn=$this->{BASEDN} if ! defined $basedn;
  $host=$this->{HOST} if ! defined $host;

  my($ldapcmd);
  $ldapcmd=cs::Shell::quote('ldapsearch','-x','-h',$host,$query);
  my($s)=new cs::Source (PIPE,$ldapcmd);
  die "$::cmd: can't pipe from \"$ldapcmd\": $!" if ! defined $s;

  _parseData($s);
}

sub _parseData
{ my($s)=@_;

  local($_);
  my(@data,$inrecord,$r,$field,$body);

  $inrecord=0;

  LINE:
    while (defined ($_=$s->GetContLine()) && length)
    { chomp;
      s/\n\s+//g;

      next LINE if /^#/;

      if (/^$/)
      { $inrecord=0;
	next LINE;
      }

      if (! $inrecord)
      { push(@data,($r={}));
	$inrecord=1;
	$field='dn';
	$body=$_;
      }
      elsif (/^(\w+)[:=]\s*/)
      { ($field,$body)=($1,$');
      }
      else
      { warn "bad data: [$_]\n";
	next LINE;
      }

      $r->{$field}=[] if ! exists $r->{$field};
      push(@{$r->{$field}},$body);
    }

  @data;
}

sub diff
{ my($old,$new,@chkkeys)=@_;
  @chkkeys=(keys %$old, keys %$new) if ! @chkkeys;

  @chkkeys=::uniq(@chkkeys);

  # unfold into lists
  { my($o,$n)=({},{});

    for (@chkkeys)
    { $o->{$_}=[ exists $old->{$_}
		 ? ref $old->{$_}
		    && ::reftype($old->{$_}) eq ARRAY
		   ? @{$old->{$_}}
		   : $old->{$_}
		 : ()
	       ];
      $n->{$_}=[ exists $new->{$_}
		 ? ref $new->{$_}
		    && ::reftype($new->{$_}) eq ARRAY
		   ? @{$new->{$_}}
		   : $new->{$_}
		 : ()
	       ];
    }

    $old=$o;
    $new=$n;
  }

  my($add,$del)=({},{});

  my($key,%h1,%h2,$n,$i);

  for $key (@chkkeys)
  { undef %h1; undef %h2;
    map($h1{$_}++, @{$old->{$key}});
    map($h2{$_}++, @{$new->{$key}});

    $add->{$key}=[];
    $del->{$key}=[];
    for (::uniq(keys(%h1), keys(%h2)))
    { $n=$h1{$_}-$h2{$_};
      if ($n < 0)
      { for $i (1..-$n)
	{ push(@{$add->{$key}},$_);
	}
      }
      elsif ($n > 0)
      { for $i (1..$n)
	{ push(@{$del->{$key}},$_);
	}
      }
    }
  }

  ($add,$del);
}

sub Diff2Modify
{ my($this,$dn,$add,$del)=@_;

  my(@modlines)=();

  my($first,$key);

  $first=1;
  for $key (sort keys %$add)
  { if (@{$del->{$key}})
    {
      if ($first)
      { $first=0;
	push(@modlines,
	      "dn: $dn\n",
	      "changetype: modify\n");
      }

      push(@modlines,"delete: $key\n");
##			  for (@{$del->{$key}})
##				{ push(@modlines,"$key: $_\n");
##				}
      push(@modlines,"-\n");
    }
  
    if (@{$add->{$key}})
    {
      if ($first)
      { $first=0;
	push(@modlines,
	      "dn: $dn\n",
	      "changetype: modify\n");
      }

      push(@modlines,"add: $key\n");
      for (@{$add->{$key}})
	    { push(@modlines,"$key: $_\n");
	    }
      push(@modlines,"-\n");
    }
  }

  @modlines;
}

# delete a DN
sub DelDN2Modify($$)
{ my($this,$dn)=@_;

  ("dn: $dn\n",
   "changetype: delete\n",
  );
}

sub NewRec2Modify
{
  my($this,$rec,$dn)=@_;
  $dn=$rec->{'dn'}->[0] if ! defined $dn;

  my(@modlines)=();

  push(@modlines,
	"dn: $dn\n",
	"changetype: add\n");
  for (@{$rec->{'objectclass'}})
  { push(@modlines,"objectclass: $_\n");
  }

  my($key);

  for $key (grep($_ ne 'dn' && $_ ne 'objectclass',
		sort keys %$rec))
  { for (@{$rec->{$key}})
    { push(@modlines,"$key: $_\n");
    }
  }

  @modlines;
}

sub Apply
{ my($this,$modref,$password,$admindn)=@_;
  die "no password!" if ! defined $password;
  $admindn=$this->AdminDN() if ! defined $admindn;

  my($ldapcmd);
  $ldapcmd=cs::Shell::quote('ldapmodify',
				'-v',
				'-h',$this->Host(),
				'-D',$admindn,
				'-w',$password);
  my($s)=new cs::Sink (PIPE,$ldapcmd);
  die "$::cmd: can't pipe to \"$ldapcmd\": $!" if ! defined $s;
  warn "piping to \"$ldapcmd\"";

  for my $mod (@$modref)
  { $s->Put(@$mod);
  }
}

1;
