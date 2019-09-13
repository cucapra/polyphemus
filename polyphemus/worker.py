import threading
import os
import time
import json
import glob
import re

from .stages_common import work
from . import state
from .db import ARCHIVE_NAME, CODE_DIR

# For executing on a Xilinx Zynq board.
ZYNQ_SSH_PREFIX = ['sshpass', '-p', 'root']  # Provide Zynq SSH password.
ZYNQ_HOST = 'zb1'
ZYNQ_DEST_DIR = '/mnt'
ZYNQ_REBOOT_DELAY = 40


def _task_config(task, config):
    """Interpret some configuration options on a task, and assign the
    task's `sdsflags` and `platform` fields so they can be used
    directly.
    """
    task['estimate'] = int(task['config'].get('estimate'))

    task['platform'] = task['config'].get('platform') or \
        config['DEFAULT_PLATFORM']
    task['mode'] = task['config'].get('mode') or \
        config['DEFAULT_F1_MODE']


class WorkThread(threading.Thread):
    """A base class for all our worker threads, which run indefinitely
    to process tasks in an appropriate state.

    The thread takes the database and configuration dictionaries as well
    as a function to which these will be passed. When the thread runs,
    the function is invoked repeatedly, indefinitely.
    """

    def __init__(self, db, config, func):
        self.db = db
        self.config = config
        self.func = func
        super(WorkThread, self).__init__(daemon=True)

    def run(self):
        while True:
            self.func(self.db, self.config)

def should_make(task):
    """Run stage_make if make was specified in the task config.
    """
    if task['config'].get('make'):
        return state.MAKE
    else:
        return state.UNPACK_FINISH

def get_make_conf(log, config):
    """Extract configuration variables from a make job and update the config
    object with them.
    """
    conf_str = r"^\s*({})\s*:?=\s*(.*)$".format('|'.join(config['MAKE_CONF_VARS']))
    conf_re = re.compile(conf_str, re.I)

    conf = {}
    for line in log.split('\n'):
        matches = conf_re.search(line.strip())
        if matches:
            conf[matches.group(1)] = matches.group(2)

    return conf

def stage_unpack(db, _):
    """Work stage: unpack source code.
    """
    with work(db, state.UPLOAD, state.UNPACK, should_make) as task:
        # Unzip the archive into the code directory.
        os.mkdir(task.code_dir)
        task.run(["unzip", "-d", task.code_dir, "{}.zip".format(ARCHIVE_NAME)])

        # Check for single-directory zip files: if the code directory
        # only contains one subdirectory now, "collapse" it.
        code_contents = os.listdir(task.code_dir)
        if len(code_contents) == 1:
            path = os.path.join(task.code_dir, code_contents[0])
            if os.path.isdir(path):
                for fn in os.listdir(path):
                    os.rename(os.path.join(path, fn),
                              os.path.join(task.code_dir, fn))
                task.log('collapsed directory {}'.format(code_contents[0]))


def stage_make(db, config):
    """Work stage: run make command. Assumes that at the end of the make
    command, work equivalent to the stage_hls is done, i.e., either
    estimation data has been generated or a bitstream has been
    generated.
    """
    # After this stage, transfer either to F1-style execution (i.e., AFI
    # generation) or ordinary execution (for the Zynq toolchain).
    def stage_after_make(task):
        if config['TOOLCHAIN'] == 'f1' and task['mode'] == 'hw':
            return state.AFI_START
        else:
            return state.HLS_FINISH

    prefix = config["HLS_COMMAND_PREFIX"]
    with work(db, state.MAKE, state.MAKE_PROGRESS, stage_after_make) as task:
        _task_config(task, config)

        if config['TOOLCHAIN'] == 'f1':
            # Get the AWS platform ID for F1 builds.
            platform_script = (
                'cd $AWS_FPGA_REPO_DIR; '
                'source ./sdaccel_setup.sh > /dev/null; '
                'echo $AWS_PLATFORM; '
            )
            proc = task.run([platform_script], capture=True, shell=True)
            aws_platform = proc.stdout.decode('utf8').strip()

            make = [
                'make',
                'MODE={}'.format(task['mode']),
                'DEVICE={}'.format(aws_platform),
            ]

        else:
            # Simple make invocation for SDSoC.
            make = [
                'make',
                'ESTIMATE={}'.format(task['estimate']),
                'PLATFORM={}'.format(task['platform']),
                'TARGET={}'.format(config['EXECUTABLE_NAME']),
            ]

        make_cmd = prefix + make
        if task['config']['directives']:
            make_cmd.append(
                'DIRECTIVES={}'.format(task['config']['directives'])
            )

        # Before running the make target, collect configuration information.
        proc = task.run(make_cmd + ['--dry-run', '--print-data-base'],
                        capture=True, cwd=CODE_DIR)
        make_conf = get_make_conf(proc.stdout.decode('utf8').strip(), config)
        # Update the job config with make_conf
        task.job['config']['make_conf'] = make_conf
        db.log(task.job['name'], 'make conf added {}'.format(make_conf))
        db._write(task.job)

        # Run the make target
        task.run(
            make_cmd,
            timeout=config["SYNTHESIS_TIMEOUT"],
            cwd=CODE_DIR,
        )

def stage_afi(db, config):
    """Work stage: create the AWS FPGA binary and AFI from the *.xclbin
    (Xilinx FPGA binary file).
    """
    with work(db, state.AFI_START, state.AFI, state.HLS_FINISH) as task:
        # Clean up any generated files from previous runs.
        task.run(
            ['rm -rf to_aws *afi_id.txt \
                *.tar *agfi_id.txt manifest.txt'],
            cwd=os.path.join(CODE_DIR, 'xclbin'),
            shell=True,
        )

        # Find *.xclbin file from hardware synthesis.
        xcl_dir = os.path.join(task.dir, 'code', 'xclbin')
        xclbin_file_path = glob.glob(
            os.path.join(xcl_dir, '*hw.*.xclbin'))
        xclbin_file = os.path.basename(xclbin_file_path[0])

        # Generate the AFI and AWS binary.
        afi_script = (
            'cur=`pwd` ; '
            'cd $AWS_FPGA_REPO_DIR ; '
            'source ./sdaccel_setup.sh > /dev/null ; '
            'cd $cur/xclbin ; '
            '$SDACCEL_DIR/tools/create_sdaccel_afi.sh '
            '-xclbin={} '
            '-s3_bucket={} '
            '-s3_dcp_key={} '
            '-s3_logs_key={}'.format(
                xclbin_file,
                config['S3_BUCKET'],
                config['S3_DCP'],
                config['S3_LOG'],
            )
        )
        task.run([afi_script], cwd=CODE_DIR, shell=True)

        # Get the AFI ID.
        afi_id_files = glob.glob(os.path.join(xcl_dir, '*afi_id.txt'))
        with open(afi_id_files[0]) as f:
            afi_id = json.loads(f.read())['FpgaImageId']

        # Every 5 minutes, check if the AFI is ready.
        while True:
            time.sleep(config['AFI_CHECK_INTERVAL'])

            # Check the status of the AFI.
            status_string = task.run(
                ['aws', 'ec2', 'describe-fpga-images',
                 '--fpga-image-ids', afi_id],
                cwd=CODE_DIR,
                capture=True
            )
            status_json = json.loads(status_string.stdout)

            # When the AFI becomes available, exit the loop and enter
            # execution stage.
            status = status_json['FpgaImages'][0]['State']['Code']
            task.log('AFI status: {}'.format(status))
            if status == 'available':
                break

def stage_fpga_execute(db, config):
    """Work stage: upload bitstream to the FPGA controller, run the
    program, and output the results.

    This stage currently assumes we want to execute on a Xilinx Zynq
    board, which is accessible via SSH. We require `sshpass` to provide
    the password for the board (because the OS that comes with ZedBoards
    hard-codes the root password as root---not terribly secure, so the
    board should clearly not be on a public network).
    """
    with work(db, state.HLS_FINISH, state.RUN, state.DONE) as task:

        # Do nothing in this stage if we're just running estimation.
        if task['config'].get('estimate') or task['config'].get('skipexec'):
            task.log('skipping FPGA execution stage')
            return

        if config['TOOLCHAIN'] == 'f1':
            # On F1, use the run either the real hardware-augmented
            # binary or the emulation executable.
            if task['mode'] == 'hw':
                exe_cmd = ['sudo', 'sh', '-c',
                           'source /opt/xilinx/xrt/setup.sh ;\
                            ./{}'.format(config['EXECUTABLE_NAME'])]
            else:
                exe_cmd = [
                    'sh', '-c',
                    'source $AWS_FPGA_REPO_DIR/sdaccel_setup.sh > /dev/null; '
                    'XCL_EMULATION_MODE={} ./{}'.format(
                        task['mode'],
                        config['EXECUTABLE_NAME']
                    )
                ]
            task.run(
                exe_cmd,
                cwd=CODE_DIR,
                timeout=9000
            )

        else:
            # Copy the compiled code (CPU binary + FPGA bitstream) to the
            # Zynq board.
            bin_dir = os.path.join(task.code_dir, 'sd_card')
            bin_files = [os.path.join(bin_dir, f) for f in os.listdir(bin_dir)]
            dest = '{}:{}'.format(ZYNQ_HOST, ZYNQ_DEST_DIR)
            task.run(
                ZYNQ_SSH_PREFIX + ['scp', '-r'] + bin_files + [dest],
                timeout=1200
            )

            # Restart the FPGA and wait for it to come back up.
            task.run(
                ZYNQ_SSH_PREFIX + ['ssh', ZYNQ_HOST, '/sbin/reboot'],
            )
            task.log('waiting {} seconds for reboot'.format(ZYNQ_REBOOT_DELAY))
            time.sleep(ZYNQ_REBOOT_DELAY)

            # Run the FPGA program and collect results
            task.run(
                ZYNQ_SSH_PREFIX + [
                    'ssh', ZYNQ_HOST,
                    'cd {}; ./{}'.format(ZYNQ_DEST_DIR,
                                         config['EXECUTABLE_NAME']),
                ],
                timeout=120
            )


STAGES = stage_unpack, stage_make, stage_fpga_execute

def work_threads(db, config):
    """Get a list of (unstarted) Thread objects for processing tasks.
    """
    stages = list(STAGES) + \
        [stage_make for i in range(config['PARALLELISM_MAKE'] - 1)]
    if config['TOOLCHAIN'] == 'f1':
        stages.append(stage_afi)
    return [WorkThread(db, config, stage) for stage in stages]
