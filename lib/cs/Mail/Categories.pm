#!/usr/bin/perl
#
# Miscellaneous mail things.
#	- Cameron Simpson <cs@zip.com.au> 24jun99
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Mail::Misc;
use cs::CachedFile;
use cs::Source;

package cs::Mail::Categories;

@cs::Mail::Categories::ISA=qw(cs::CachedFile);

sub END
{ undef %cs::Mail::Categories::_Known;
}

sub categorise($;$)
{ my($H,$file)=@_;

  my $this = defined $file ? categories($file) : categories();

  $this->Categorise($H);
}

sub categories(;$)
{ my($file)=@_;
  $file="$ENV{MAILRCDIR}/categories" if ! defined $file;

  if (! defined $cs::Mail::Categories::_Known{$file})
  { $cs::Mail::Categories::_Known{$file}
		=new cs::Mail::Categories
			("$ENV{HOME}/rc/mail/categories.old",
			 \&cs::Mail::Categories::_load_categories
			);
  }

  $cs::Mail::Categories::_Known{$file};
}

sub _load_categories
{ my($this)=@_;

  my $path = $this->Path();
  my $state= $this->State();

  my $s;

  return if ! defined ($s=new cs::Source (PATH,$path));

  my @cats = ();

  local($_);

  RULE:
    while (defined($_=$s->GetContLine()) && length)
    { chomp;

      if (! /^(\S+)\s+/)
      { warn "$::cmd: $path: bad rule: $_\n";
	next RULE;
      }

      my $key = $1;
      my $code = $';

      # perl expr or subr
      if ($code =~ /^\(/ && $code =~ /\)$/
       || $code =~ /^\{/ && $code =~ /\}$/
	 )
      { my $subr = eval "sub { $code; }";
	if ($@)
	{ warn "$::cmd: $path: $key: $code: $@\n";
	  next RULE;
	}
	else
	{ push(@cats,[$key,SUB,$subr]);
	}
      }
      else
      { my($unparsed);

	($code,$unparsed)=cs::Hier::a2h($code);
	$unparsed =~ s/^\s+//;

	if (length $unparsed)
	{ warn "$::cmd: $path: $key: bad value: $code\n";
	  next RULE;
	}

	if (ref $code)
	{ warn "$::cmd: $path: $key: not a scalar: $code\n";
	  next RULE;
	}

	my(@hlist);

	if ($code =~ /^(\w[-\w_]+)\s*:\s*/)
	# hdrlist:string
	{ my $hlist = $1;
	  $code = $';

	  @hlist=grep(length, split(/\s*,\s*/,$hlist));
	}
	else
	# string
	{ @hlist=(TO,CC,RESENT_TO,APPARENTLY_TO);
	}

	$code=cs::Mail::Misc::normaddr($code);

	push(@cats,[$key,HDR,[@hlist],$code]);
      }
    }

  $state->{CATEGORIES}=[ @cats ];
  warn "new categories loaded from \"$path\"\n";
}

# match categories (default returns only the first match)
sub Categorise($$;$)
{ my($this,$H,$all)=@_;
  $all=0 if ! defined $all;

  $this->Poll();

  my $state = $this->State();

  if (! exists $state->{CATEGORIES})
  { return wantarray ? () : undef;
  }

  my $C = $state->{CATEGORIES};

  ## warn "_match_category(@_)";

  my(@cats)=();
  my($key,$type,$matched);
  my %addrcombos;
  my $addrcombo;

  RULE:
  for my $rule (@$C)
  {
    $key  = $rule->[0];
    $type = $rule->[1];
    $matched = 0;
    ## warn "try rule $key\n";

    if ($type eq SUB)
    { my $subr = $rule->[2];
      $matched = &$subr($H);
    }
    elsif ($type eq HDR)
    { my @hlist = @{$rule->[2]};
      my $string= $rule->[3];

      $addrcombo = "@hlist";

      if (! exists $addrcombos{$addrcombo})
      { $addrcombos{$addrcombo}=[ map(cs::Mail::Misc::normaddr($_),
					keys %{$H->Addrs(@hlist)}) ];
	## my @a = @{$addrcombos{$addrcombo}};
	## warn "stash [$addrcombo] = [@a]";
      }

      $matched = grep($_ eq $string, @{$addrcombos{$addrcombo}});
    }
    else
    { warn "$::cmd: bad rule - unsupported type \"$type\": "
	  .cs::Hier::h2a($rule,0);
      next RULE;
    }

    push(@cats,$key) if $matched;

    last RULE if ! $all && @cats;
  }

  ## warn "matched rules: [@cats]\n";
  wantarray ? @cats : $cats[0];
}

1;
