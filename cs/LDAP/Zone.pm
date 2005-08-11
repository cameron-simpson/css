#!/usr/bin/perl
#
# Module to express stuff about an LDAP zone.
#       - Cameron Simpson <cs@zip.com.au> ...
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

use strict qw(vars);
     
package cs::LDAP::Zone;

sub new
{ my($class,$basedn,$attrs)=@_;

  my $this = bless {}, $class;

  $this->{BASEDN}=$basedn;
  $this->{USER_SUBFORMAT}='uid=%s, ou=People';
  $this->{GROUP_SUBFORMAT}='gid=%s, ou=Groups';

  for my $a (keys %$attrs)
  { $this->{$a}=$attrs->{$a};
  }

  return $this;
}

sub BaseDN($)
{ $_[0]->{BASEDN};
}

sub UserSubFmt($)
{ $_[0]->{USER_SUBFORMAT};
}

sub GroupSubFmt($)
{ $_[0]->{GROUP_SUBFORMAT};
}

sub UserDN($$)
{ my($this,$id)=@_;
  sprintf($this->UserSubFmt(), $id).", ".$this->BaseDN();
}

sub GroupDN($$)
{ my($this,$id)=@_;
  sprintf($this->GroupSubFmt(), $id).", ".$this->BaseDN();
}

1;
