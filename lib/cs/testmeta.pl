#!/usr/bin/perl

use strict qw(vars);

use cs::Persist;

my($db,$meta);

$db=cs::Persist::db('testdb');
$meta=$db->{''}->Meta();

$meta->{FOO}=BAH;

my(@k);@k=keys %$db; warn "k=[@k]";

undef $meta;
undef $db;

cs::Persist::finish();
