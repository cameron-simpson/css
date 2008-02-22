#!/usr/bin/perl
#
# Code to deal with geographic information.
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Geog;

%country_aka=(
		AUSTRALIA	=> AU,
		AUST		=> AU,
		CANADA		=> CA,
		DEN		=> DK,
		DENMARK		=> DK,
		ENGLAND		=> UK,	# approximately
		FIN,		=> FI,
		FINLAND		=> FI,
		FRANCE		=> FR,
		GER,		=> DE,
		GERMANY		=> DE,
		IRELAND		=> UK,	# even more approximately
		ITAL		=> IT,
		NETH,		=> NL,
		NOR,		=> NO,
		NORWAY,		=> NO,
		OZ		=> AU,
		SCOTLAND	=> UK,	# about as approximately
	      # SPAIN		=> ???
		SWE		=> SE,
		SWEDEN		=> SE,
		SWITZERLAND	=> CH,
		JAPAN		=> JP
	     );

%rawhint=(	OKLAHOMA	=> OL
	 );

%parentregion=(
		'AU:Brisbane'	=> Qld,
		'AU:Melbourne'	=> Vic,
		'AU:Melb'	=> Vic,
		'AU:Sydney'	=> NSW,
		'AU:UNSW'	=> 'NSW:Sydney'
	      );

%region_aka=(	'CA:Ont'	=> 'CA:Ontario',
		'AU:Vic:Melb'	=> 'AU:Vic:Melbourne'
	    );

sub z { 1 || print STDERR @_, "\n"; }
sub locate	# (email[,code/subregion]) -> (country-code,subregion)
	{ local($_,$hint)=@_;
	  local($code,	# country-code
		$region
	       );

	  if ($region =~ /,/)
		{ z("email=$_, hint=[$hint]");
		}

	  if (length($hint) == 0)
		{ z("no hint");
		  if (/\.([a-z]+)$/i)
			{ $code=$1;
			}
		  else
		  { $code=World;
		  }
		}
	  else
	  { if (defined $rawhint{$hint})
		{ $hint=$rawhint{$hint};
		}

	    # Try to guess some info from the hint.
	    # The hint is mostly based on the geography field of the
	    # DoD membership list.
	    if ($hint =~ m:^[A-Z][A-Z]$:) { z(USA); $code=US; $region=$hint; }
	    elsif ($hint =~ m:^/([A-Z][A-Z])\s*;\s*(.*\S)\s*:)
					{ z("code;stuff");$code=$1; $region=$2; }
	    elsif ($hint =~ m:^\s*(.*\S)\s*/([A-Z][A-Z])\s*:)
					{ z("stuff/code");$code=$2; $region=$1; }
	    else
	    { $hint =~ s/(\w)(\w+)/\U$1\L$2/g;
	      if ($hint =~ m:^\s*(.*\S)\s*,\s*(\w+)\s*:)
					{ z("stuff, code");$code=$2; $region=$1; }
	      elsif ($hint =~ m:/:)	{ z("stuff/code(2)");$code=$'; $region=$`; }
	      elsif ($hint =~ m:^[A-Z][a-z]+$:) { z("text");$code=$hint; $region=''; }
	      elsif ($hint =~ m:^[\w,\s]+$:) { z("words");
					    local(@words)=split(/[,\s]+/,$hint);
					    $code=pop @words;
					    $region=join(':',reverse @words);
					  }
	      else			{ $code=Other; $region=$hint; }
	    }
	  }

	  $code =~ tr/a-z/A-Z/;
	  $region =~ s/\b([A-Z])([A-Z]\w+)/$1\L$2/g;

	  if (defined $country_aka{$code})
		{ $code=$country_aka{$code};
		}

	  if (defined $parentregion{"$code:$region"})
		{ $region=$parentregion{"$code:$region"}.":$region";
		}

	  if (defined $region_aka{$region})
		{ $region=$region_aka{$region};
		}

	  if ($code eq AU)
		{ local($ozregion)=&locate_oz($_);
		  if ($ozregion ne Oz)
			{ $region=$ozregion;
			}
		}

	  ($code,$region);
	}

sub locate_oz	# (email) -> subregion of Oz
	{ local($_)=@_;
	  local($email,$CLASS)=($_,Oz);

	  s/.*\@//;
	  tr/A-Z/a-z/;

	  # general ACSnet migration
	  s/\.oz$/$&.au/;	# .oz -> .oz.au

	  # special cases
	  s/\b(unsw|su|mu|monash|bu)\.oz\.au/$1.edu.au/;
	  s/\bcs\.unsw\.edu\.au/cse.unsw.edu.au/;

	  if (/\.au$/)
	    { $_=$`;
	      if (/\.oz$/)
		{ $_=$`;
	      	  if (/\bcomnet$/)	{ $CLASS='NSW:Sydney'; }
	      	  elsif (/\btrl$/)	{ $CLASS='Vic:Melbourne'; }
	      	  elsif (/\bjpl$/)	{ $CLASS='Vic:Melbourne'; }
		}
	      elsif (/\.edu$/)
		{ $_=$`;
		  if (/\b(unimelb|mu)$/){ $CLASS='Vic:Melbourne:MU'; }
		  elsif (/\bmonash$/)	{ $CLASS='Vic:Melbourne:Monash'; }
		  elsif (/\badelaide$/)	{ $CLASS='Vic:Adelaide:AdelaideU'; }
		  elsif (/\bsu$/)	{ $CLASS='NSW:Sydney:SU'; }
		  elsif (/\banu$/)	{ $CLASS='ACT:ANU'; }
		  elsif (/\bdeakin$/)	{ $CLASS='Vic:Deakin'; }
		  elsif (/\bcanberra$/)	{ $CLASS='ACT:CanberraU'; }
		  elsif (/\bqut$/)	{ $CLASS='Qld:QUT'; }
		  elsif (/\busq$/)	{ $CLASS='Qld:USQ'; }
		  elsif (/\bbu$/)	{ $CLASS='Qld:BU'; }
		  elsif (/\buwa$/)	{ $CLASS='WA:UWA'; }
		  elsif (/\bunsw$/)	{ $CLASS='NSW:Sydney:UNSW'; }
		  elsif (/\buow$/)	{ $CLASS='NSW:Woolongong:UOW'; }
		  elsif (/\bunisa$/)	{ $CLASS=SA; }
		  elsif (/\buws$/)	{ $CLASS='NSW:Sydney:UWS'; }
		}
	      elsif (/\b.gov$/)
		{ $_=$`;
		  if (/\bansto$/)	{ $CLASS='NSW:Sydney:ANSTO'; }
		  elsif (/\bcis\.dsto$/){ $CLASS='Vic:Melbourne'; }
		  elsif (/\bdefcen$/)	{ $CLASS=ACT; }
		}
	      elsif (/\b.csiro$/)
		{ $_=$`;
		  if (/\brp$/)		{ $CLASS='NSW:Sydney'; }
		  if (/\bdap$/)		{ $CLASS='NSW:Sydney'; }
		  if (/\bmlb.geomechanics$/){ $CLASS='Vic:Melbourne'; }
		}
	      elsif (/\bsoftway$/)	{ $CLASS='NSW:Sydney'; }
	      elsif (/\bqpsx$/)		{ $CLASS=WA; }
	      elsif (/\bnms\.otc\.com$/){ $CLASS='NSW:Sydney'; }
	    }

	  if ($CLASS eq Oz)
		{ print STDERR "where is $email?\n";
		}

	  $CLASS;
	}

1;
