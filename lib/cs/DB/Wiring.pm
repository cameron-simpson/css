#!/usr/bin/perl
#
# The wiring database.
#	- Cameron Simpson <cs@zip.com.au> 05jul1998
#

use strict qw(vars);

use cs::DB;

package cs::DB::Wiring;

@cs::DB::Wiring::ISA=();

sub finish { cs::DB::finish(); }

sub db
{ cs::DB::db(['wiring'],@_);
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
