#!/usr/bin/perl

use strict qw(vars);

use cs::Upd;

package cs::MIME::Decode;

@cs::MIME::Decode::ISA=(Decode);

# return a Decode(DataSource) object using this as its sub-source
sub new
	{ my($class,$s)=@_;
	  bless (new cs::Decoder $s, \&Decode), $class;
	}

sub Delimiter
	{ my($this,$delim)=@_;

	  if (defined $delim)
		{ $this->{DELIMITER}=$delim;
		  $this->{DELIMITER_END}=$delim.'--';
		}

	  $this->{DELIMITER};
	}

sub Encoding
	{ my($this,$code)=@_;

	  if (defined $code)
		{ $this->{ENCODING}=$code;
		}

	  ${ENCODING};
	}

sub Decode
	{ local($_)=shift;

	  cs::Upd::err("into MIME::Decode::Decode($_,[@_])\n");

	  return undef if ! defined;

	  if (defined $this->{DELIMITER})
		{ my($text)=$_;

	  	  chomp($text);

	  	  return undef if $text eq $this->{DELIMITER}
			       || $text eq $this->{DELIMITER_END};
		}

	  cs::Upd::err("leaving MIME::DataSource::Decode(): _=\"$_\"\n");

	  if (defined $this->{ENCODING})
		{ if ($this->{ENCODING} eq 'base64')
			{ my($data)='';
			  my($a,$b,$c,$d);	# unpacked values
			  my($g4);		# encoded values

			  # ignore other chars
			  s:[^A-Za-z0-9+/=]+::g;

			  while (m:^[A-Za-z0-9+/=]{4}:)
				{ $g4=$&; $_=$';

				  # break up into code values
				  ($a,$b,$c,$d)=map($MIME::_base64{$_},
						    split(//,$g4));

				  # append data, accomodating '=' padding
				  if ($a < 0)	{}
				  else
				  { $data.=pack('C',
						($a<<2) + (($b&0x30)>>4));
				    if ($b < 0)	{}
				    else
				    { $data.=pack('C',
					      (($b&0x0f)<<4) + (($c&0x3c)>>2));
				      if ($d < 0){}
				      else
				      { $data.=pack('C',
					      (($c&0x03)<<6) + $d);
				      }
				    }
				  }
				}

			  $_=$data;
			}
		  elsif ($this->{ENCODING} eq 'quoted-printable')
			{ s/=\r?\n$//;
			  s/=([\da-f][\da-f])/pack('c',hex($1))/egi;
			}
		}

	  $_;
	}

1;
