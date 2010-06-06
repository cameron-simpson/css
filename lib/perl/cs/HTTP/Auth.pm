#!/usr/bin/perl
#
# Do HTTP-related authority stuff.
#	- Cameron Simpson <cs@zip.com.au> 12mar98
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::HTTP;
use cs::Persist;
use cs::MIME::Base64;

package cs::HTTP::Auth;

@cs::HTTP::Auth::ISA=qw();

sub finish
{ cs::Persist::finish();
}

# hdr -> (scheme,label)
sub parseWWW_AUTHENTICATE
{ my($wahdr)=@_;
  if (ref $wahdr)
  # we can take an RFC822 object, too
  { $wahdr=$wahdr->Hdr(WWW_AUTHENTICATE);
  }

  ## warn "WWW_AUTHENTICATE=[$wahdr]";

  if ($wahdr =~ /^\s*(\w+)\s+realm\s*=\s*"([^"]*)\"/i)
  { ## warn "1=$1, 2=$2\n";
    return ($1,$2);
  }

  return undef;
}

sub new
{ my($class,$db,$rw)=@_;
  $db="$ENV{HOME}/.http-auth-db" if ! defined $db;
  $rw=0 if ! defined $rw;

  if (! ref $db)
  { return undef if ! defined ($db=cs::Persist::db($db,$rw))
  }

  bless { DB => $db,
	}, $class;
}

# Look up authority for challenge.
# (scheme,host,label) -> { USERID => userid, PASSWORD => password }
sub GetAuth
{ my($this,$scheme,$host,$label)=@_;

  my($h)=$this->{DB};
  my(@keys)=(uc($scheme),lc($host),$label);

  local($_);

  while (@keys)
  { $_=shift(@keys);
    return undef if ! exists $h->{$_};
    $h=$h->{$_};
  }

  $h;
}

sub SaveAuth
{ my($this,$scheme,$host,$label,$userid,$password)=@_;

  my($db)=$this->{DB};

  $scheme=uc($scheme);
  $host=lc($host);

  $db->{$scheme}={} if ! exists $db->{$scheme};
  $db=$db->{$scheme};
  $db->{$host}={} if ! exists $db->{$host};
  $db=$db->{$host};

  $db->{$label}={ USERID => $userid, PASSWORD => $password };
}

# annotate some headers with an authority
sub HdrsAddAuth
{ my($this,$hdrs,$scheme,$auth)=@_;
  
  ## warn "auth=".cs::Hier::h2a($auth,0)."\n";

  $hdrs->Add([AUTHORIZATION,
	"$scheme ".base64("$auth->{USERID}:$auth->{PASSWORD}")]);
}

sub base64
{ cs::MIME::Base64::encode(@_);
}

1;
