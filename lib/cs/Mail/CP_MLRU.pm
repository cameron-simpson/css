#!/usr/bin/perl
#
# Parse Carl Paukstis's mailing list roundup.
#	- Cameron Simpson <cameron@dap.csiro.au> 21jul1995
#

use strict qw(vars);

use cs::Hier;

package cs::Mail::CP_MLRU;

%cs::Mail::CP_MLRU::_fields=(
		DESCRIPTION	=> { },
		DESRIPTION	=> { CANONICAL => 'description' },
		TO_SUBSCRIBE	=> { CANONICAL => 'subscribe' },
		SUBSCRIBE	=> { },
		DIGEST_AVAIL	=> { CANONICAL => 'digest' },
		DIGEST_AVAILABLE=> { CANONICAL => 'digest' },
		ADMIN_ADDRESS	=> { CANONICAL => 'admin' },
		OWNER		=> { },
		LIST_OWNER	=> { CANONICAL => 'owner' },
		LIST_OWNERS	=> { CANONICAL => 'owner' },
		WWW_URL		=> { CANONICAL => 'url' }
	);

sub FieldType
{ my($field)=@_;

  if (defined $cs::Mail::CP_MLRU::_fields{$field}
   && defined $cs::Mail::CP_MLRU::_fields{$field}->{Type})
  { return $cs::Mail::CP_MLRU::_fields{$field}->{Type};
  }

  'Text';
}

sub FILE2a
{ my($INPUT)=@_;
  my(%lists);
  my($this,$name,$key,$field,$data);
  my($ok)=1;

  my($context);

 LINE:
  while (<$INPUT>)
  { $context="$INPUT, line $.";

    chomp;
    s/\s+$//;

    next LINE unless length;

    s/^\*//;

    if (m|^([\dA-Z][-/\w]*(\s+[\dA-Z][-/\w]*)*(\s\([^)]+\))?):?|)
    # new list
    { undef $this;

      $name=$key=$1;
      $key =~ tr/ A-Z/-a-z/;

      while (defined $lists{$key})
      { $key.="-another";
      }

      $lists{$key}={ Key => $key, Name => $name };
      $this=$lists{$key};
    }
    elsif (! defined $this)
    { warn "$context: skipping $_\n";
    }
    elsif (/^$/)
    {}
    elsif (/^\*?\s+\(see\s+([-A-Z\s]+)\)/)
    { my($refkey)=$1;

      $refkey =~ tr/ A-Z/-a-z/;

      $this->{DESCRIPTION}="see <A HREF=#mlru-key-$refkey>$1</A>";
    }
    elsif (/^\*?\s+([A-Z\s]+)[:?]\s*/)
    { $field=$1; $data=$';
      $field =~ tr/-a-z /_A-Z_/;

      next LINE if $data =~ /^\(none\)\s*$/i;

      if (! defined $cs::Mail::CP_MLRU::_fields{$field})
      { warn "$context: $key: unrecognised field \"$field\" ignored\n";
	$ok=0;
	next LINE;
      }
      else
      { my($f);
	$f=$cs::Mail::CP_MLRU::_fields{$field};
	if (defined $f->{CANONICAL})
	{ $field=$f->{CANONICAL};
	}

	if (defined $this->{$field})
	{ warn "$context: field \"$field\" already defined for list \"$this->{Key}\",\n\tignoring \"$_\"\n";
	  $ok=0;
	  next LINE;
	}

	if (defined $f->{Type} && $f->{Type} ne 'Text')
	{ my($t)=$f->{Type};

	  if ($t eq 'Boolean')
	  { if ($data =~ /^(y(es)?|no?|digest\s+only)[-:\s]*/i)
	    { my($bool,$addenda);

	      $bool=$1; $addenda=$';
	      $this->{$field}=($bool =~ /^(y|digest only)/);
	      if (length $addenda)
	      { $this->{"$field-addenda"}=$addenda;
	      }
	    }
	    else
	    { print STDERR "$context: can't convert \"$_\" to $t, ignored\n";
	      $ok=0;
	      next LINE;
	    }
	  }
	  else
	  { print STDERR "$context: don't know how to convert to type \"$t\", ignoring \"$_\"\n";
	    $ok=0;
	    next LINE;
	  }
	}
	else
	{ $this->{$field}=$data;
	}
      }
    }
  }

  map($lists{$_}, sort keys %lists);
}

1;
