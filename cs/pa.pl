# parse an RFC822 address list returning a list of pairs
#	(addr-without-<>, full-text, ...)
sub parseaddrs	# (addrlist) -> @(addr, text, ...)
	{ local($_)=@_;
	  my(@parsed);
	  my($text,$addr,$atext);
	  my($comment);

	  for ($text='', $addr='', $atext=''; length; )
		{ if (/^\s+/)	{ $text.=$&;
				  $atext.=$&;
				  $_=$';
				}
		  elsif (/^\(/)
			# comment
			{ ($comment,$tail)=parse_comment($_);
			  $text.=$comment;
			  $_=$tail;
			}
		  elsif (/^,(\s*,)*/)
			# end of currently building address
			{ $_=$';
			  $text =~ s/^\s+//;
			  $text =~ s/\s+$//;

			  $atext =~ s/^\s+//;
			  $atext =~ s/\s+$//;

			  $addr=$atext if ! length $addr;

			  if (length $addr)
				{ push(@parsed,$addr,$text);
				}

			  $addr='';
			  $text='';
			  $atext='';
			}
		  elsif (/^<[^\@>]*\@[^>]*>/)
			{ $text.=$&;
			  $atext.=$&;
			  $addr=$&;
			  $_=$';
			}
		  elsif (/^\\./			# q-pair
		      || /^"(\\.|[^"\\]+)*"/	# quoted string
		      || /^[^\\"<\(,]+/)	# plain text
			{ $text.=$&;
			  $atext.=$&;
			  $_=$';
			}
		  else	{ $text.=substr($_,0,1);
			  $atext.=substr($_,0,1);
			  substr($_,0,1)='';
			}
		}

	  @parsed;
	}

sub parse_comment
	{ local($_)=shift;
	  my($comment)='';
	  my($subcomment,$tail);

	  if (! /^\(/)
		{ warn "parse_comment on a non-comment \"$_\"";
		  return ('()',$_);
		}

	  $comment=$&;
	  $_=$';

	  TOKEN:
	    while (length)
		{ last TOKEN if /^\)/;

		  if (/^\(/)
			{ ($subcomment,$tail)=parse_comment($_);
			  $comment.=$subcomment;
			  $_=$tail;
			}
		  elsif (/^\\./)
			{ $comment.=$&; $_=$'; }
		  elsif (/^[^\(\)\\]+/)
			{ $comment.=$&; $_=$'; }
		  else	{ $comment.=substr($_,0,1);
			  substr($_,0,1)='';
			}
		}

	  s/^\)//;		# eat closure if present

	  $comment.=')';	# return well-formed comment

	  ($comment,$_);
	}
