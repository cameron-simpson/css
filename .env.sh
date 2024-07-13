#!/bin/sh
#
# Additional environment actions for the css project.
# - Cameron Simpson <cs@cskk.id.au>
#

# this is parallels $(VIRTUAL_ENV) in Mykefile
VIRTUAL_ENV=${TMPDIR:-/tmp}/venv--$( basename "$ENV_DEV_DIR" )--$ARCH
export VIRTUAL_ENV

export PYTHON3=$VIRTUAL_ENV/bin/python3
export PYTHON_EXE=$PYTHON3
export PATH=$VIRTUAL_ENV/bin:$PATH

export PATH=$ENV_DEV_DIR/bin-cs:$ENV_DEV_DIR/bin:$PATH

export PYTHONPATH=$ENV_DEV_DIR/lib/python:$PYTHONPATH
##export PYTHONWARNINGS=default
