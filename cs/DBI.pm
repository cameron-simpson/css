#!/usr/bin/perl
#
# Subclass of the DBI class. Supplies some handy things.
#	- Cameron Simpson <cs@zip.com.au> 27jun99
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use DBI;

package cs::DBI;

@cs::DBI::ISA=qw(DBI);

$cs::DBI::_now=time;

# Obtain filehandle for my personal mysql db.
sub mydb()
{ if (! defined $cs::DBI::_mydb)
  { ::need(cs::Secret);
    my $s = cs::Secret::get("mysql\@$ENV{SYSTEMID}");
    my $login = $s->Value(LOGIN);
    my $password = $s->Value(PASSWORD);
    my $host = $s->Value(HOST);	$host='mysql' if ! defined $host;
    ## warn "$login\@$host: $password\n";

    $cs::DBI::_mydb=DBI->connect("dbi:mysql:CS_DB:$host",$login,$password);
  }
  ## $cs::DBI::_mydb->trace(1,"$ENV{HOME}/tmp/mydb.trace");
  $cs::DBI::_mydb;
}

# ISO date from GMT
sub isodate
{ my($gmt)=@_;
  $gmt=$cs::DBI::_now if ! defined $gmt;

  my @tm = localtime($gmt);

  sprintf("%d-%02d-%02d", $tm[5]+1900, $tm[4]+1, $tm[3]);
}

# return a statement handle for some sql
# it is cached for speed, since most SQL gets reused
sub sql($$)
{ my($dbh,$sql)=@_;
  if (! defined $dbh)
  { my @c=caller;warn "dbh UNDEF from [@c]";
  }

  my $stkey="$dbh $sql";

  ## warn "sql($dbh,\"$sql\")";
  return $cs::DBI::_cachedQuery{$stkey}
	if defined $cs::DBI::_cachedQuery{$stkey};

  $cs::DBI::_cachedQuery{$stkey} = $dbh->prepare($sql);
}

# as above, but then perfomrs the statement handle's execute() method
#	dosql(dbh,sql,execute-args)
sub dosql
{ my($dbh,$sql)=(shift,shift);

  my $sth = sql($dbh,$sql);
  return undef if ! defined $sth;

  $sth->execute(@_)
  ? $sth : undef;
}

# exact select on multiple fields
# returned statement handle for query to fetch specified records
sub query
{ my($dbh,$table)=(shift,shift);
  if (! defined $dbh)
  { my @c=caller;warn "dbh UNDEF from [@c]";
  }

  my $query = "SELECT * FROM $table";
  my $sep   = " WHERE ";

  for my $field (@_)
  { $query.="$sep$field = ?";
    $sep=" AND ";
  }

  sql($dbh, $query);
}

# execute statement handle and return all results as array of records
sub fetchall_hashref
{ my($sth)=shift;

  if (! wantarray)
  { my(@c)=caller;
    die "$0: cs::DBI::fetchall_hashref not in array context from [@c]";
  }

  return () if ! $sth->execute(@_);

  my $h;
  my @rows = ();
  while (defined ($h=$sth->fetchrow_hashref()))
  { push(@rows,$h);
  }

  @rows;
}

# return records WHERE $field == $key
sub find($$$$)
{ my($dbh,$table,$field,$key)=@_;

  if (! wantarray)
  { my(@c)=caller;
    die "$0: cs::DBI::find not in array context from [@c]"; 
  }

  my $sth = query($dbh,$table,$field);
  if (! defined $sth)
  { warn "$::cmd: can't make query on $dbh.$table where $field = ?";
    return ();
  }

  fetchall_hashref($sth,$key);
}

# return records WHERE $field == $key and the START/END_DATEs overlap $when
sub findWhen($$$$;$)
{ my($dbh,$table,$field,$key,$when)=@_;
  $when = isodate() if ! defined $when;

  my $sth = sql($dbh,"SELECT * FROM $table where $field = ? AND START_DATE <= ? AND (ISNULL(END_DATE) OR END_DATE >= ?)");
  return () if ! defined $sth;

  fetchall_hashref($sth,$key,$when,$when);
}

# return records WHERE the START/END_DATEs overlap $when
sub when($$;$)
{ my($dbh,$table,$when)=@_;
  $when = isodate() if ! defined $when;

  my $sth = sql($dbh,"SELECT * FROM $table where START_DATE <= ? AND (ISNULL(END_DATE) OR END_DATE >= ?)");
  return () if ! defined $sth;

  fetchall_hashref($sth,$when,$when);
}

# return a cs::DBI object which accepts the ExecuteWithRec method,
# used to insert records
sub insert	# dbh,table[,dfltok],fields...
{ my($dbh,$table)=(shift,shift);
  my $dfltok=0;
  if (@_ && $_[0] =~ /^[01]$/)
  { $dfltok=shift(@_)+0;
  }
  my @fields = @_;

  my $sql = "INSERT INTO $table ("
	  . join(',',@fields)
	  . ") VALUES ("
	  . join(',',map('?',@fields))
	  . ")";

  ## warn "SQL is [$sql]";
  bless [ $dbh, $table, $sql, sql($dbh,$sql), $dfltok, @fields ];
}

# takes an "insert" sql query and inserts some records
# return is undef on failure or last insertid()
sub ExecuteWithRec
{ my($isth)=shift;

  my($dbh,$table,$sql,$sth,$dfltok,@fields)=@$isth;

  ## warn "stashing record:\n".cs::Hier::h2a($rec,1);
  ## warn "sth=$sth, \@isth=[ @$isth ]\n";
  ## warn "fields=[@fields]";

  my $ok = 1;

  # hack - lock the table if we're inserting 5 or more records,
  # for speed
  my $locked = (@_ > 5 && lock_table($dbh,$table));

  INSERT:
  while (@_)
  { my $rec = shift(@_);
    ## warn "INSERT rec = ".cs::Hier::h2a($rec,1);
    my @execargs=();

    for my $field (@fields)
    { if (! exists $rec->{$field})
      { if ($dfltok)
	{ $rec->{$field}=undef;
	}
	else
	{ ::need(cs::Hier);
	  die "$::cmd: ExecuteWithRec(): no field \"$field\": rec="
	      .cs::Hier::h2a($rec,1);
	}
      }
      elsif (! defined $rec->{$field})
      { ## warn "$field = UNDEF!";
	## $rec->{$field}='';
      }

      push(@execargs, $rec->{$field});
    }

    # for some reason, text fields can't be empty - very bogus
    ## for (@execargs) { $_=' ' if defined && ! length; }


    if (! $sth->execute(@execargs))
    { warn "$::cmd: ERROR with insert";
      my @c = caller;
      warn "called from [@c]\n";
      warn "execargs=".cs::Hier::h2a(\@execargs,0)."\n";
      $ok=0;
      last INSERT;
    }
    else
    { ## warn "INSERT OK, noting insertid";
      ## XXX: was 'insertid'; may break if we ever leave mysql
      $cs::DBI::_last_id=$sth->{'mysql_insertid'};
    }
  }

  unlock_tables($dbh) if $locked;

  $ok;
}

# update fields in a table
# extra values are (f,v) for conjunctive "WHERE f = v" pairs
sub updateField
{ my($dbh,$table,$field,$value)=(shift,shift,shift,shift);

  my $sql = "UPDATE $table SET $field = ?";
  ## warn "\@_=[@_]";
  my @args = $value;
  my $sep = " WHERE ";
  while (@_ >= 2)
  { my($f,$v)=(shift,shift);
    ## warn "f=$f, v=$v";
    $sql.=$sep."$f = ?";
    push(@args,$v);
    $sep=" AND ";
  }

  ## warn "sql=[$sql], args=[@args]";

  dosql($dbh,$sql,@args);
}

# return the id if the last record inserted by ExecuteWithRec
undef $cs::DBI::_last_id;
sub last_id()
{ $cs::DBI::_last_id;
}

sub lock_table($$)
{ my($dbh,$table)=@_;
  dosql($dbh,"LOCK TABLES $table WRITE");
}

sub unlock_tables($)
{ my($dbh)=@_;
  dosql($dbh,"UNLOCK TABLES");
}

# given a date (ISO form) select the entries from the given table
# which contain the date
sub SelectDateRanges($$$;$)
{ my($this,$table,$constraint,$when)=@_;
  $constraint=( defined $constraint && length $constraint
	     ? "$constraint AND "
	     : ''
	     );
  $when=isodate() if ! defined $when;

  my $dbh = $this->{DBH};

  $when=$dbh->quote($when);

  my $statement = "SELECT * FROM $table WHERE $constraint START_DATE <= $when AND ( ISNULL(END_DATE) OR END_DATE >= $when )";
  ## warn "statement=[$statement]";
  dosql($dbh,$statement);
}

# as above, but return the data
sub GetDateRanges
{ my($this)=shift;

  my $sth = $this->SelectDateRanges(@_);
  return () if ! defined $sth;
  fetchall_hashref($sth);
}

# return a statement handle with conjunctive WHERE constraints
# returns an ARRAY
#	empty on error
#	(sth, @args) if ok
sub sqlWhere
{ my($dbh,$sql,@w)=@_;

  my ($fullsql,@args) = sqlWhereText($sql,@w);
  my $sth = sql($dbh,$fullsql);
  return () if ! defined $sth;

  ($sth,@args);
}

sub sqlWhereText
{ my($sql,@w)=@_;

  my $sep = ' WHERE ';
  my @args = ();
  while (@w >= 2)
  { my($f,$v)=(shift(@w), shift(@w));
    push(@args,$v);
    $sql.=$sep."$f = ?";
    $sep=' AND ';
  }

  return ($sql, @args);
}

sub addDatedRecord
{ my($dbh,$table,$when,$rec,@delwhere)=@_;
  if (! defined $when)
  { my(@c)=caller;
    die "$::cmd: cs::DBI::addDatedRecord($table): \$when undefined from [@c]";
  }

  if (@delwhere)
  # delete old records first
  { my ($sth, @args) = sqlWhere($dbh,'DELETE FROM $table',@delwhere);
    if (! defined $sth)
    { warn "$::cmd: cs::DBI::addDatedRecord($table): can't make sql to delete old records";
      return undef;
    }

    $sth->execute(@args);
  }

  $rec->{START_DATE}=$when;
  cs::DBI::insert(MSQ::tsdb(),$table, keys %$rec)->ExecuteWithRec($rec);
}

sub delDatedRecord
{ my($dbh,$table,$when,@delwhere)=@_;
  if (! defined $when)
  { my(@c)=caller;
    die "$::cmd: cs::DBI::delDatedRecord($table): \$when undefined from [@c]";
  }
  if (@delwhere < 2)
  { my(@c)=caller;
    die "$::cmd: cs::DBI::delDatedRecord($table): no \@delwhere from [@c]";
  }

  # set closing date of the day before the deletion day
  my $today = new cs::Day $when;
  my $prevwhen = $today->Prev()->Code();

  my ($sql, @args) = sqlWhereText("UPDATE $table SET END_DATE = ?", @delwhere);
  $sql .= " AND START_DATE <= ? AND ISNULL(END_DATE)";

  my $sth = sql($dbh, $sql);
  if (! defined $sth)
  { warn "$::cmd: cs::DBI::delDatedRecord($table): can't make sql to delete old records";
    return undef;
  }

  $sth->execute($prevwhen,@args,$when);
}

1;
