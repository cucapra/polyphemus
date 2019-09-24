import os
import glob
import json
import time

from . import state
from .stages_common import work, task_config, update_make_conf
from .db import CODE_DIR

def stage_f1_make(db, config):
    """Work stage: run make command. Assumes that at the end of the make
    command, work equivalent to the stage_hls is done, i.e., either
    estimation data has been generated or a bitstream has been
    generated.
    """

    prefix = config["HLS_COMMAND_PREFIX"]
    with work(db, state.MAKE, state.MAKE_PROGRESS, state.AFI_START) as task:
        task_config(task, config)

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

        make_cmd = prefix + make
        if task['config']['directives']:
            make_cmd.append(
                'DIRECTIVES={}'.format(task['config']['directives'])
            )

        # Dry run the make command and extract relevant conf variables.
        update_make_conf(make_cmd, task, db, config)

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
        # sw_emu and hw_emu do not require AFI
        if task['mode'] != 'hw': 
            task.log('skipping AFI stage for {}'.format(task['mode']))
            return
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


def stage_f1_fpga_execute(db, config):
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

        # On F1, use the run either the real hardware-augmented binary or the
        # emulation executable.
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
