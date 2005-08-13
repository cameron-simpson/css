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
{ my($class,$attrs)=@_;

  my $this = bless {}, $class;

  $this->{USER_SUBFORMAT}='uid=%s, ou=People';
  $this->{GROUP_SUBFORMAT}='gid=%s, ou=Groups';

  for my $a (keys %$attrs)
  { $this->{$a}=$attrs->{$a};
  }

  return $this;
}

sub Attr($$)
{ $_[0]->{$_[1]};
}

sub BaseDN($)
{ $_[0]->Attr(BASEDN);
}

sub UserSubFmt($)
{ $_[0]->Attr(USER_SUBFORMAT);
}

sub GroupSubFmt($)
{ $_[0]->Attr(GROUP_SUBFORMAT);
}

sub UserSubDN($)
{ my($this)=@_;
  return $this->Attr(USER_SUBZONE).", ".$this->BaseDN();
}

sub GroupSubDN($)
{ my($this)=@_;
  return $this->Attr(GROUP_SUBZONE).", ".$this->BaseDN();
}

sub UserDN($$)
{ my($this,$id)=@_;
  if (ref $id) { $id=$this->Attr(USER_OBJ2ID)->($id); }
  sprintf($this->UserSubFmt(), $id).", ".$this->UserSubDN();
}

sub GroupDN($$)
{ my($this,$id)=@_;
  if (ref $id) { $id=$this->Attr(GROUP_OBJ2ID)->($id); }
  sprintf($this->GroupSubFmt(), $id).", ".$this->UserSubDN();
}

# $Z->UserSearch(Net::LDAP,user) -> @Net::LDAP::Entry
sub UserSearch($$$)
{ my($this,$L,$id)=@_;
  if (ref $id)
  { my $srchidfn=$this->Attr(USER_SEARCHID);
    if (! defined $srchidfn) { $srchidfn=$this->Attr(USER_OBJ2ID); }
    $id=$srchidfn->($id);
  }
  my %q = ( base => $this->UserSubDN(),
	    filter => sprintf("(&(".$this->Attr(USER_SEARCH)."))",$id),
	    scope => 'sub',	# vs 'one'
	  );
  return $L->search(%q);
}

# $Z->GroupSearch(Net::LDAP,group) -> @Net::LDAP::Entry
sub GroupSearch($$$)
{ my($this,$L,$id)=@_;
  if (ref $id)
  { my $srchidfn=$this->Attr(GROUP_SEARCHID);
    if (! defined $srchidfn) { $srchidfn=$this->Attr(GROUP_OBJ2ID); }
    $id=$srchidfn->($id);
  }
  my %q = ( base => $this->GroupSubDN(),
	    filter => sprintf("(&(".$this->Attr(GROUP_SEARCH)."))",$id),
	    scope => 'sub',	# vs 'one'
	  );
  ##warn "search=".cs::Hier::h2a(\%q,0);
  return $L->search(%q);
}

1;
