#! /usr/bin/env bash
set -eux

source ./machine-setup.sh > /dev/null 2>&1
cwd=`pwd`

USE_PREINST_LIBS=${USE_PREINST_LIBS:-"true"}
if [ $USE_PREINST_LIBS = true ]; then
  export MOD_PATH=/scratch3/NCEPDEV/nwprod/lib/modulefiles
else
  export MOD_PATH=${cwd}/lib/modulefiles
fi

gsitarget=$target
[[ "$target" == wcoss_cray ]] && gsitarget=cray

# Check final exec folder exists
if [ ! -d "../exec" ]; then
  mkdir ../exec
fi

cd hafs_gsi.fd/ush/
./build_all_cmake.sh "PRODUCTION" "$cwd/hafs_gsi.fd"

# Build FV3 regional enkf executable
cd hafs_gsi.fd/ush/
./build_enkf_cmake.sh "PRODUCTION" "$cwd/hafs_gsi.fd"

exit

