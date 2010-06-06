#!/usr/bin/perl
#

use cs::XRef;
use cs::MD5Index;
use cs::XRef;
use cs::Hier;
use cs::HTML;

# $ndx=new cs::MD5Index;
# $key="http://ftpsearch.ntnu.no/ftpsearch";
# $ndx->{FRED}={ KEY => $key };
# warn "ndx=".cs::Hier::h2a($ndx,1);
# undef $ndx;

$refs=[ { HREF	=> 'ref1',
	  TITLE => 'title of ref1',
	  CATS => [ "REGIONAL/C/AU/NSW/SYD", "MOTO/GROUP/MLIST/REGIONAL" ]
	},
      ];
#$legend={ CATS => { REGIONAL => { DESC => 'Regions',
#				  CATS => { AU => { DESC => 'Australia',
#						    CATS => {},
#						  },
#					    US => { DESC => "US of A",
#						    CATS => {},
#						  },
#					  },
#				},
#		     MOTO    => { DESC => 'Motorcycling',
#				  CATS => { MLIST => { DESC => 'Mailing Lists',
#						       CATS => {},
#  						     },
#					  },
#				},
#		   },
#	};

$cats=cs::XRef::loadLegend("$ENV{HOME}/p/legend");
die "load failed" if ! defined $cats;

$legend={ DESC => "Top Level", CATS => $cats };

$xr=new cs::XRef;

for (@$refs)
	{ $xr->Add($_);
	}

# warn "xr=\n".cs::Hier::h2a($xr,1);

@html=$xr->HTML(1,'xr',$legend,'TOP');
# warn "HTML=\n".cs::Hier::h2a(\@html);

print cs::HTML::tok2a(@html);
