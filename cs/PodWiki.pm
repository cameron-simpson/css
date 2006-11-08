#!/usr/bin/perl
#
# Translate Perl POD format into Wiki format (specificly that of MediaWiki).
# A horrible idea, but people like Wikis. No idea why:-(
#       - Cameron Simpson <cs@zip.com.au> 01nov2006
#

use Pod::Parser;

package cs::PodWiki;

@ISA = qw(Pod::Parser);

sub write
{ my($self,@args) = @_;
  my $fh = $self->output_handle();
  print $fh @args;
}

sub expand
{ my($self,$text)=@_;
  $self->write($self->interpolate($text, $line_num));
}

sub head
{ my($level,$text)=@_;
  $_last_head=$level;
  my $eq = '=' x $level;
  "\n$eq$text$eq\n";
}

sub dotag
{ my($otag,$oattrs,$ipod,$listpfx)=@_;
  $ipod =~ s/^\s+//;
  $ipod =~ s/\s+$//;
  my $pod="";
  if ($otag eq 'A')
  { my $href = $attrs['HREF'];
    if ($href !~ /^(https?|ftp|mailto):\/\//)
    { if ($href !~ /^\//)
      { $href="/sysadmin/$href";
      }
      $href="http://web$href";
    }
    $pod.="[$href $ipod]";
  }
  elsif (grep($otag eq $_, HTML, HEAD, BODY, TITLE))
  { $pod=$ipod;
  }
  elsif ($otag eq 'IMG')
  { $pod.="[[Image:$oattrs->{SRC}]]";
  }
  elsif ($otag eq 'HR')
  { $pod.="\n----\n";
  }
  elsif ($otag eq 'BR')
  { $pod.="<$otag>";
  }
  elsif ($otag eq 'P')
  { $pod.="\n\n$ipod";
  }
  elsif ($otag =~ /^H(\d+)$/)
  { $pod.=head($1+0,$ipod);
  }
  elsif ($otag eq 'EM')
  { $pod.="''$ipod''";
  }
  elsif (grep($otag eq $_, TT, BLOCKQUOTE, PRE, CENTER))
  { $pod.="<$otag>$ipod</$otag>";
  }
  elsif ($otag eq 'UL') { $pod.=$ipod; $listpfx=pop(@listpfx); }
  elsif ($otag eq 'OL') { $pod.=$ipod; $listpfx=pop(@listpfx); }
  elsif ($otag eq 'DL') { $pod.=$ipod; $listpfx=pop(@listpfx); }
  elsif ($otag eq 'LI')
  { $pod.="\n$listpfx$ipod\n";
  }
  elsif ($otag eq 'DT')
  { $pod.="$listpfx$ipod";
  }
  elsif ($otag eq 'DD')
  { $pod.="$listpfx : $ipod";
  }
  elsif ($otag eq 'TABLE')
  { $ipod =~ s/\n\n+/\n/g;
    $pod.="\n{| border=\"1\"\n$ipod\n|}\n";
  }
  elsif ($otag eq 'TBODY')
  { $pod.=$ipod;
  }
  elsif ($otag eq 'TR')
  { $pod.="\n|-\n$ipod\n";
  }
  elsif ($otag eq 'TD')
  { $ipod =~ y/\n/ /;
    $pod.="\n| $ipod\n";
  }
  else
  { die "unhandled tag <$otag>" unless grep($_ eq $otag, I, B);
    $pod.="<$otag";
    for my $k (sort keys %$oattrs)
    { $pod.=" $k";
      my $v = $oattrs{$k};
      if (defined $v)
      { $pod.="\"$v\"";     # TODO: other quotes
      }
    }
    $pod.=">$ipod</$otag>";
  }
  return $pod;
}

sub html2wiki
{ local($_)=@_;
  my $ohtml = $_;
  my $pod="";
  my @tags=();
  my @pod=();
  my $listpfx="";
  my @listpfx=();

  my $poptag = sub {
                my($otag,$oattrs,$opod)=@{pop(@tags)};
                ##warn "POPTAG($otag)\n";
                my $ipod = $pod; $pod=$opod;
                $pod.=dotag($otag,$oattrs,$ipod,$listpfx);
                return ($otag,$oattrs);
                   };
  my $pushtag = sub{
                push(@tags,[$_[0],$_[1],$pod]);
                $pod="";
                   };
  my $popto  = sub {
                my($totag)=@_;
                my($otag,$oattrs);
                POPTO:
                while(1)
                { ($otag,$oattrs)=&$poptag();
                  if ($otag == $totag)
                  { last POPTO;
                  }
                }
                return ($otag,$oattrs);
                   };

  HTML:
  while (length)
  { if (/^[^&<]+/)
    { $pod.=$&;
      $_=$';
      next HTML;
    }

    if (/^&(\d+|[a-z]+);?/)
    { $pod.=$&;
      $_=$';
      next HTML;
    }

    if (/^<\s*!--.*-->/s)
    { $_=$';
      next HTML;
    }

    if (!/^<\s*/)
    { die "html parse failure, \$_=[$_]";
    }

    $_=$';
    if (!/^((\/\s*)?)([^>\s]+)\s*/)
    { die "tag not found inside <...>: \$_=[$_]";
    }
    my $closing=(length($1) > 0);
    my $tag=uc($3);
    $_=$';
    if ($tag eq TH)     # we don't honour TH
    { $tag=TD;
    }

    my $attrs={};
    while (/^([^>=\s]+)(=('[^']*'|"[^"]*"|[^'">]+))?\s*/)
    { my($attr,$value)=(uc($1),$3);
      $_=$';
      if ($value =~ /^'(.*)'$/ || $value =~ /^"(.*)"$/)
      { $value=$1;
      }
      $attrs[$attr]=$value;
    }

    if (!/^>/)
    { die "no closing >, \$_=[$_], pod=[$pod, @pod]\n\t$ohtml\n\ttag=$tag, closing=$closing";
    }
    $_=$';

    if (!$closing)
    {
      if (grep($tag eq $_, BR, HR))
      { $pod.=dotag($tag,$attrs,'',$listpfx);
      }
      else
      {
        if (grep($tag eq $_, TR, TD))
        {
          # locate most recent table tag
          my $tableT = undef;
          TAGSTACK:
          for my $T (reverse @tags)
          { if (grep($_ eq $T->[0], TABLE, TR, TD))
            { $tableT=$T;
              last TAGSTACK;
            }
          }
          my $ttag = (defined($tableT) ? $tableT->[0] : "");

          # insert missing structure, close extra structure
          if ($tag eq TD)
          { if ($ttag eq "")
            { &$pushtag(TABLE,{});
              &$pushtag(TR,{});
            }
            elsif ($ttag eq TD)
            { &$popto(TD);
            }
            elsif ($ttag eq TABLE)
            { &$pushtag(TR,{});
            }
          }
          elsif ($tag eq TR)
          { if ($ttag eq "")
            { &$pushtag(TABLE,{});
            }
            elsif ($ttag eq TR || $ttag eq TD)
            { &$popto(TR);
            }
          }
        }

        push(@tags,[$tag,$attrs,$pod]);
        $pod="";

        if ($tag eq 'UL') { push(@listpfx,$listpfx); $listpfx.="*"; }
        elsif ($tag eq 'OL') { push(@listpfx,$listpfx); $listpfx.="#"; }
        elsif ($tag eq 'DL') { push(@listpfx,$listpfx); $listpfx.=";"; }
      }

      next HTML;
    }

    CLOSE:
    while (@tags)
    { my($otag,$oattrs)=&$poptag();
      if ($tag eq $otag)
      { last CLOSE;
      }
    }
  }

  # flush tag stack
  while (@tags)
  { my($otag,$oattrs)=&$poptag();
  }

  return $pod;
}

@_begin=();
$_begin="";

$_int_cmd={
        'head1' => sub { head(1,$_[0]) },
        'head2' => sub { head(2,$_[0]) },
        'head3' => sub { head(3,$_[0]) },
        'head4' => sub { head(4,$_[0]) },
        'image' => sub { my($im)=@_;
                         $im =~ s:.*/::;
                         "\n[[Image:$im]]\n"
                       },
        'over'  => sub { $_last_head++;
                         "";
                       },
        'item'  => sub { my($type,$etc)=split(/\s+/,$_[0],2);
                         ##warn "type=[$type] etc=[$etc]";
                         if ($type ne '*')
                         { $etc="$type $etc";
                           $etc =~ s/ +$//;
                           $type="*";
                         }
                         return head($_last_head,$etc);
                       },
        'back'  => sub { $_last_head--;
                         "";
                       },
        'for'   => sub { my($what,$etc)=split(/\s+/,$_[0],2);
                         if (uc($what) eq 'HTML')
                         { return "\n".html2wiki($etc)."\n";
                         }

                         "\n<$what> [$etc]\n"
                       },
        'begin' => sub { push(@_begin,$_begin=uc($_[0]));
                         "";
                       },
        'end'   => sub { $_begin=pop(@_begin);
                         "";
                       },
          };

sub command {
  my ($self, $command, $paragraph, $line_num) = @_;

  $paragraph =~ s/\r?\n/ /g;
  $paragraph =~ s/ +$//;

  if (exists $_int_cmd->{$command})
  { $self->expand($_int_cmd->{$command}("$paragraph"));
    return;
  }
  
  die "UNSUPPORTED =$command [$line_num] [$paragraph]";
}

sub verbatim {
  my ($self, $paragraph, $line_num) = @_;

  if (@_begin)
  { my($mode)=$_begin[$#_begin];
    if ($mode eq 'HTML')
    { $self->write(html2wiki($paragraph));
      return;
    }
    die "inside unsupported =begin $mode";
  }

  ##warn "VERB [$paragraph]\n";
  $self->write("\n", $paragraph);
}

sub textblock {
  my ($self, $paragraph, $line_num) = @_;

  if (@_begin)
  { my($mode)=$_begin[$#_begin];
    if ($mode eq 'HTML')
    { $self->write(html2wiki($paragraph));
      return;
    }
    die "inside unsupported =begin $mode";
  }

  $paragraph =~ s/\r?\n/ /g;
  $paragraph =~ s/ +$//;
  $self->expand("\n$paragraph\n");
}

$_int_code={
        'B'     => sub { "<B>$_[0]</B>" },
        'C'     => sub { "<TT>$_[0]</TT>" },
        'F'     => sub { "<TT>$_[0]</TT>" },
        'I'     => sub { "<I>$_[0]</I>" },
        'E'     => sub { if ($_[0] eq 'gt') { return ">"; }
                         if ($_[0] eq 'lt') { return "<"; }
                         die "unsupported INTERIOR_SEQUENCE E<$_[0]>";
                       },
        'L'     => sub { my($link,$text);
                         if ($_[0] =~ /\|/)
                         { $link=$';
                           $text=$`;
                         }
                         else
                         { $link=$_[0];
                           $text='';
                         }
                         if ($link =~ m;^(https?|ftp)://;i)
                         { return length($text) ? "[$link|$text]" : $link;
                         }

                         $link =~ s/::/#/;

                         return length($text) ? "[[$link|$text]]" : "[[$link]]";
                       },
           };

sub interior_sequence {
  my ($self, $seq_command, $seq_argument) = @_;

  if (exists $_int_code->{$seq_command})
  { return $_int_code->{$seq_command}($seq_argument);
  }

  die "unsupported INTERIOR_SEQUENCE $seq_command<$seq_argument>";
}

