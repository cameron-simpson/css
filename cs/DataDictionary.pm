#!/usr/bin/perl
#
# Load and use data dictionaries.
#	- Cameron Simpson <cs@zip.com.au> 11mar1997
#

use strict qw(vars);

use cs::Hier;
use cs::Source;

package cs::DataDictionary;

sub new
	{ my($class,$file)=@_;

	  my($DD)=bless [], $class;

	  if (ref $file eq ARRAY)
		{ push(@$DD,@$file);
		}
	  elsif (ref $file eq HASH)
		{ map(push(@$DD,$_,@{$file->{$_}}),keys %$file);
		}
	  else
	  # must be a filename
	  {
	    my($s);

	    return undef if ! defined ($s=new cs::Source PATH, $file);

	    local($_);
	    my($n);

	    $n=0;

	    DD:
	      while (defined ($_=$s->GetLine()) && length)
		{ $n++;
		  chomp;
		  s/\s+$//;
		  if (! /^\s*(\w+)\s*=>\s*/)
			{ warn "$file, line $n: bad format: $_";
			  next DD;
			}

		  push(@$DD,$1,scalar(cs::Hier::a2h($')));
		}
	  }

	  $DD;
	}

# add fields to a form from the DD
# returns nothing - use the form methods to get the resultant HTML
sub ExtendForm
	{ my($DD,$F,$intable)=@_;
	  $intable=0 if ! defined $intable;

	  my(@html)=();
	  my(@dd)=@$DD;
	  my($dd,$field,$type,$annotation,$content);

	  if ($intable)
		{ $F->MarkUp("<TABLE>\n");
		}

	  while (@dd)
		{ ($field,$dd)=(shift(@dd),shift(@dd));
		  $type=shift(@$dd);
		  if (! @$dd || ref $dd[0])
			{ $annotation=$field;
			}
		  else	{ $annotation=shift(@$dd);
			}

		  if ($intable)
			{ $F->MarkUp("<TR>\n",
				     "  <TD>$annotation\n",
				     "  <TD>");
			}
		  else	{ $F->MarkUp("$annotation\n");
			}

		  if ($type eq SELECTONE)
			{ my(@map)=_mkmap(shift(@$dd));
			  my(%map)=@map;

			  $F->Popup($field,\%map);
			}
		  elsif ($type eq SELECTMANY)
			{ my(@map)=_mkmap(shift(@$dd));
			  my(%map)=@map;

			  $F->ScrollingList($field,\%map,1);
			}
		  elsif ($type eq CARDINAL || $type eq TEXT || $type eq KEYWORDS)
			{ $F->TextField($field,undef,70);
			}
		  else	{ warn "no query for unknown field \"$field\" of type \"$type\"";
			}

		  if ($intable)	{ $F->MarkUp("\n"); }
		  else		{ $F->MarkUp("<BR>\n"); }
		}

	  if ($intable)
		{ $F->MarkUp("</TABLE>\n");
		}
	}

sub _mkmap
	{ my($mapinfo)=shift;
	  my(@map)=();
	  my($key,$desc);

	  if (ref $mapinfo eq ARRAY)
		{ while (@$mapinfo)
			{ $key=shift(@$mapinfo);
			  push(@map,$key,$key);
			}
		}
	  else	{ for $key (sort keys %$mapinfo)
			{ $desc=$mapinfo->{$key};
			  push(@map,$key,(length $desc ? $desc : $key));
			}
		}

	  @map;
	}

1;
