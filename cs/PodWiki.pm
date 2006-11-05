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
  elsif ($otag eq 'P')
  {  $pod.="\n\n$ipod";
  }
  elsif ($otag =~ /^H(\d+)$/)
  { $pod.=head($1+0,$ipod);
  }
  elsif ($otag eq 'EM')
  { $pod.="''$ipod''";
  }
  elsif ($otag eq 'UL') { $listpfx=pop(@listpfx); }
  elsif ($otag eq 'OL') { $listpfx=pop(@listpfx); }
  elsif ($otag eq 'LI')
  { $pod.="\n$listpfx$ipod\n";
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

    if (substr($_,4) eq '<!--') { warn "[$_]"; }
    if (/^<\s*\!--.*-->/)
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
    my $closing=length($1);
    my $tag=uc($3);
    $_=$';

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
    { push(@tags,[$tag,$attrs]);
      push(@pod,$pod); $pod="";

      if ($tag eq 'UL') { push(@listpfx,$listpfx); $listpfx.="*"; }
      elsif ($tag eq 'OL') { push(@listpfx,$listpfx); $listpfx.="#"; }

      next HTML;
    }


    CLOSE:
    while (@tags)
    { my($otag,$oattrs)=@{pop(@tags)};
      my($ipod)=$pod; $pod=pop(@pod);
      $pod.=dotag($otag,$oattrs,$ipod);
      if ($tag eq $otag)
      { last CLOSE;
      }
    }
  }

  # flush tag stack
  while (@tags)
  { my($otag,$oattrs)=@{pop(@tags)};
    my($ipod)=$pod; $pod=pop(@pod);
    $pod.=dotag($otag,$oattrs,$ipod);
  }

  return $pod;
}

@_begin=();

$_int_cmd={
        'head1' => sub { head(1,$_[0]) },
        'head2' => sub { head(2,$_[0]) },
        'head3' => sub { head(3,$_[0]) },
        'image' => sub { "[[Image:$_[0]]]" },
        'over'  => sub { $_last_head++;
                         "";
                       },
        'item'  => sub { my($type,$etc)=split(/\s+/,$_[0],2);
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
        'begin' => sub { push(@_begin,uc($_[0]));
                       },
        'end'   => sub { pop(@_begin);
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
  $self->expand("$paragraph\n");
}

$_int_code={
        'B'     => sub { "<B>$_[0]</B>" },
        'C'     => sub { "<TT>$_[0]</TT>" },
        'F'     => sub { "<TT>$_[0]</TT>" },
        'I'     => sub { "<I>$_[0]</I>" },
           };

sub interior_sequence {
  my ($self, $seq_command, $seq_argument) = @_;

  if (exists $_int_code->{$seq_command})
  { return $_int_code->{$seq_command}($seq_argument);
  }

  die "unsupported INTERIOR_SEQUENCE $seq_command<$seq_argument>";
}

