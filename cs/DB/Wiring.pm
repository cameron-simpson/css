#!/usr/bin/perl
#
# The wiring database.
#	- Cameron Simpson <cs@zip.com.au> 05jul98
#

use strict qw(vars);

use CISRA::DB;

package CISRA::Wiring;

@CISRA::UserData::ISA=();

sub finish { CISRA::DB::finish(); }

sub db
{ CISRA::DB::db(['wiring'],@_);
}

sub db_switch
{ my($switch)=shift;
  db(@_)->{'switches'}->{$switch};
}

sub db_vlan
{ db(@_)->{'vlans'};
}

sub netname2vlan
{ my($net)=@_;

  my $db = db_vlan();

  if (! exists $db->{$net})
  { warn "$::cmd: unknown net \"$net\"";
    return undef;
  }

  $db->{$net};
}

1;
