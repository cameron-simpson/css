#!/bin/sh
#
# Additional environment actions for the css project.
# - Cameron Simpson <cs@cskk.id.au>
#

export PYTHON3=$ENV_DEV_DIR/venv/bin/python3
export PATH=$ENV_DEV_DIR/bin-cs:$ENV_DEV_DIR/bin:$ENV_DEV_DIR/venv/bin:$PATH
##export PYTHONWARNINGS=default
