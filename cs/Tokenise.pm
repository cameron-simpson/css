#!/usr/bin/perl
#
# Tokeniser base class.
#	- Cameron Simpson <cs@zip.com.au> 15oct94
#
# new(Source,Match[,State])
#	Source	Source of data.
#	Match	Ref to fn(data,state) -> (token,tail) or undef
#	State	Some sort of state info, probably a ref.
# Tok(this) -> token or undef
#	Uses Match to extract a leading token from the input.
#	Calls Input as needed to collect more data.
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Tokenise;

sub new($$$;$)	# (Source,match[,State]) -> ref
{ my($class)=shift;

  die "$::cmd: usage: Tokenise->new(matchfnref,inputfnref[,state])"
	unless @_ == 2 || @_ == 3;

  my($Source,$Match,$State)=@_;

  die "$::cmd: Source($Source) is not a ref"
	unless ref $Source;
  die "$::cmd: Match(".(ref $Match).") is not a CODE ref"
	unless ref $Match eq CODE;

  if (! ref $State)
  { my($copy)=$State;

    $State=\$copy;
  }

  bless { MATCH	=> $Match,
	  DS	=> $Source,
	  STATE	=> $State,
	  DATA	=> '',
	  PENDING => [],
	}, $class;
}

sub UnTok
{ my($this)=shift;
  unshift(@{$this->{PENDING}},@_);
}

sub Tok	# this -> token or undef
{ my($this)=@_;

  { my($pending)=$this->{PENDING};

    if (@$pending)
    { ## warn "PENDING: $pending->[0]\n";
      return shift(@$pending);
    }
  }

  my($match)=$this->{MATCH};
  my($tok,$tail);
  local($_);

  while (1)
  { if (length $this->{DATA})
    { ($tok,$tail)=&$match($this->{DATA},$this->{STATE});

      if (defined $tok)
      { $this->{DATA}=$tail;
	return $tok;
      }
    }

    # no match - extend data or fail
    if (! defined($_=$this->{DS}->Read())
     || ! length)
    # EOF, fail
    # unparsed component left in DATA
    { $this->{DATA} =~ /^[^\n]*/;
      ## warn "EOF getting data to extend [$&]\n";
      return undef;
    }

    # extend and try to match again
    $this->{DATA}.=$_;
  }
}

1;
