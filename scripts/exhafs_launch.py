#! /usr/bin/env python3
################################################################################
# Script Name: exhafs_launch.py
# Authors: NECP/EMC Hurricane Project Team and UFS Hurricane Application Team
# Abstract:
#   This script creates the initial HAFS directory structure and configurations
#   for executing a specific forecast cycle.
################################################################################
##@namespace scripts.exhafs_launch
# Creates the initial HAFS directory structure for executing a
# single HAFS cycle.  This script must be run before any other.
#
# This script is executed as follows:
# @code{.sh}
# exhafs_launch.py YYYYMMDDHH STID CASE_ROOT /path/to/parm [options]
# @endcode
#
# @note When NCEP Central Operations (NCO) runs this job, the $RUN_ENVIR
#   environment variable must be set to "nco" to trigger NCO-specific rules
#
# Command line argument meanings:
#
# * YYYYMMDDHH --- a ten digit cycle date and hour
# * STID --- a three character storm identifier: storm number and one
#   letter basin.  For example, 12L for Katrina.
# * CASE_ROOT --- HISTORY for a retrospective run and FORECAST for
#   a real-time run
# * /path/to/parm --- path to the parm/ directory for locating the
#   standard *.conf files
#
# The [options] can be:
#
# * file.conf --- a configuration file to read
# * section.Option=VALUE --- after reading all configuration files,
#   set option "Option" in section "section" to the given value.
#
# This is the order in which configuration files and options are
# processed:
#
# 1. Internal initial options from the startdata variable (which does almost nothing).
# 2. parm/hafs_input.conf --- input file locations
# 3. parm/hafs.conf --- detailed HAFS configuration settings
# 4. parm/hafs_holdvars.conf --- for generating the com/storm*.holdvars.txt file
# 5. parm/hafs_basic.conf --- basic, high-level HAFS configuration settings
# 6. Configuration files listed in the [options] to exhafs_launch
# 7. Configuration section.Option=VALUE settings in exhafs_launch
# 8. The hafs.launch.prelaunch() function is called to set per-cycle or per-basin settings.
#
# See the hafs.launch.prelaunch() function and the hafs.prelaunch module
# for details on the prelaunch functionality.
#
# After configuration information is determined, the sanity checks are
# run.  If the sanity checks succeed, the initial directory structure is
# created and the com/storm*.conf file is generated.  The database is
# filled with products and tasks generated by the hafs_expt module, and
# then the script exits.

import os, sys, re, logging, collections, getopt

if 'USHhafs' in os.environ:
    sys.path.append(os.environ['USHhafs'])
elif 'HOMEhafs' in os.environ:
    sys.path.append(os.path.join(os.environ['HOMEhafs'],'ush'))
else:
    guess_HOMEhafs=os.path.dirname(os.path.dirname(
            os.path.realpath(__file__)))
    guess_USHhafs=os.path.join(guess_HOMEhafs,'ush')
    sys.path.append(guess_USHhafs)

import produtil.setup, produtil.log, produtil.dbnalert
import hafs.launcher
from produtil.numerics import to_datetime
from produtil.ecflow import set_ecflow_event

## The logging.Logger for log messages
logger=None

## Initial configuration data to be inserted to the
# hafs.launcher.HAFSLauncher before reading configuration files.
startdata='''
# Holdvars file with ksh variables:
holdvars="{holdvars}"

# Main conf file:
CONFhafs="{CONFhafs}"

# Cycle being run:
cycle={YMDH}

# Three character storm ID -- just number and basin letter:
stormid3="{vit[stormid3]}"

# Long storm ID:
longstormid="{vit[longstormid]}"
'''  # Don't forget the end of line before the '''

def usage(logger):
    logger.critical('Invalid arguments to exhafs_launch.py.  Aborting.')
    print('''
Usage: exhafs_launch.py 2014062400 95E case_root /path/to/parm [options]

Mandatory arguments:
  2014062400 -- the cycle to run
  95E -- storm id
  case_root -- FORECAST = real-time mode, HISTORY = retrospective mod
  /path/to/parm -- location of parm directory where standard conf files
      reside

Optional arguments:
section.option=value -- override conf options on the command line
/path/to/file.conf -- additional conf files to parse

Aborting due to incorrect arguments.''')
    sys.exit(2)

def main():
    """!Processes configuration information and passes on to the
    hafs.launcher module to create the initial directory structure and
    conf file."""
    logger=logging.getLogger('exhafs_launch')
    PARAFLAG = ( os.environ.get('RUN_ENVIR','DEV').upper() != 'NCO' )
    logger.info('Top of exhafs_launch.')

    short_opts = "m:M:n"
    long_opts  = ["multistorms=",
                  "multibasins=",
                  "renumber="]
    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], short_opts, long_opts)
    except getopt.GetoptError as err:
        print(str(err))
        usage('SCRIPT IS ABORTING DUE TO UNRECOGNIZED ARGUMENT')

    # Check the initial arguments passed in.
    if len(args)<4: usage(logger)

    # Find cycle: Same for  all storms in a multistorm.
    cycle=to_datetime(args[0])

    logger.info('All OPTS AND ARGS: %s %s',opts, args)

    mslist = list()
    mblist = list()
    renumber = True

    for k, v in opts:
        if  k in ('-m', '--multistorms'):
            mslist.extend(v.split(","))
        elif  k in ('-M', '--multibasins'):
            mblist.extend(v.split(","))
        elif  k in ('-n', '--renumber'):
            renumber = False
        else:
            assert False, "UNHANDLED OPTION"

    multi_sids = list()
    logger.info('ARGS: %s'% (args[1:]))
    logger.info('storm mslist, basin mblist: %s %s'% (mslist,mblist))

    # Parse the options and arguments.

    # Multistorm
    fake_stid = None
    go_since_multistorm_sids = False

    if mslist:
         multi_sids = mslist
         go_since_multistorm_sids = True

    if mblist:
        basins=mblist
        go_since_multistorm_sids = True
        if multi_sids:
            renumber=True
        logger.info('Looks like this is rocoto, running a multistorm with basins: %s'%(basins))
        # call storm priority
        bstorms = hafs.launcher.multistorm_priority(args, basins, logger, usage,renumber=renumber)
        logger.info('Priority found the following storms: ' +repr(bstorms))
        for s in bstorms:
            if s not in multi_sids:
                multi_sids.append(s)

    logger.info('MS LIST: ' +repr(multi_sids))

    fakestorm_conf = None
    global_storm_num = 0

    if go_since_multistorm_sids:
        logger.info('Parsing input arguments for a multistorm with ids: %s.'% (multi_sids))
        (case_root, parm, infiles, stids, fake_stid, priority_stid, moreopts) = \
                hafs.launcher.multistorm_parse_args(multi_sids, args[1:], logger, usage)

        # sets fake storm global_storm_num.
        global_storm_num = 1
        # Make sure you pass the last elements of the moreopts list, since it is the options
        # for the fake storm, namely the correct config.startfile.
        fakestorm_conf=hafs.launcher.launch(infiles,cycle,fake_stid,moreopts[-1],case_root,
                                  prelaunch=hafs.launcher.prelaunch,
                                  fakestorm=True,storm_num=global_storm_num)
    else:
        (case_root,parm,infiles,stid,moreopt) = \
            hafs.launcher.parse_launch_args(args[1:],logger,usage)
        stids = [stid]
        moreopts = [moreopt]

        logger.info('Requested storm %s cycle %s case root %s'
                    %(stid,cycle.strftime('%Y%m%d%H'),case_root))

    # Note: First pass in loop, global_storm_num
    # will be 1 if this is a region hafs run or 2
    # if this is multistorm hafs run.
    for i,stid in enumerate(stids):
        global_storm_num += 1
        if stid != fake_stid:
            conf=hafs.launcher.launch(infiles,cycle,stid,moreopts[i],case_root,
                                      prelaunch=hafs.launcher.prelaunch,
                                      fakestorm_conf=fakestorm_conf,
                                      storm_num=global_storm_num)
        else:
            conf=fakestorm_conf

        conf.sanity_check()

        if os.environ.get('RUN_ENVIR','DEV').upper()=='NCO':
            message=conf.strinterp('wcoss_fcst_nco','{messages}/message{storm_num}')
            alert_type=conf.strinterp('config','{RUN}_MESSAGE').upper()
            if os.path.exists(message):
                alert=produtil.dbnalert.DBNAlert(['MODEL',alert_type,'{job}',message])
                alert()

        holdvars=conf.strinterp('dir','{com}/{stormlabel}.holdvars.txt')
        logger.info(holdvars+': write holdvars here')
        with open(holdvars,'wt') as f:
            f.write(conf.make_holdvars())

        holdvars2=conf.strinterp('dir','{com}/{out_prefix}.{RUN}.holdvars.txt')
        logger.info(holdvars2+': write holdvars here as well')
        with open(holdvars2,'wt') as f:
            f.write(conf.make_holdvars())

        if conf.has_option('config','startfile'):
            startfile=conf.getstr('config','startfile')
            logger.info(startfile+': Write holdvars and conf location here.')
            startcontents=conf.strinterp('config',startdata,holdvars=holdvars)
            with open(startfile,'wt') as f:
                f.write(startcontents)

    Gsi=conf.getbool('config','run_gsi')
    #c.alter(ecf_name,'change','event','Gsi','set' if Gsi else 'clear')
    if Gsi: set_ecflow_event('Analysis',logger)

    Hycom=conf.getbool('config','run_ocean') and \
         conf.getstr('config','ocean_model').upper()=='HYCOM'
    #c.alter(ecf_name,'change','event','Hycom','set' if Hycom else 'clear')
    if Hycom: set_ecflow_event('Ocean',logger)

    Wave=conf.getbool('config','run_wave') and \
         conf.getstr('config','wave_model').upper()=='WW3'
    #c.alter(ecf_name,'change','event','Wave','set' if Wave else 'clear')
    if Wave: set_ecflow_event('Wave',logger)

if __name__ == '__main__':
    try:
        produtil.setup.setup()
        produtil.log.postmsg('exhafs_launch is starting')
        main()
        produtil.log.postmsg('exhafs_launch completed')
    except Exception as e:
        produtil.log.jlogger.critical(
            'exhafs_launch failed: %s'%(str(e),),exc_info=True)
        sys.exit(2)
