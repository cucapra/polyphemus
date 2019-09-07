Polyphemus
==========

A server for managing FPGA build and execution environments.

Running Polyphemus
------------------

To set things up, get [pipenv][], and then type `pipenv install`.

The server keeps the data, including the database and the file trees for each job, in an `instance` directory here.

### Configuration

To configure Polyphemus, create a file called `polyphemus.cfg` in the `instance` directory.
For an exhaustive list of options, see [their default values][defaults].
These ones are particularly important:

- `TOOLCHAIN`: Polyphemus supports two Xilinx HLS workflows: [SDAccel][] (on [Amazon F1][f1]) and [SDSoC][]. Set this to `"f1"` for deployment on F1. Set it to anything else to use the SDSoC workflow.
- `PARALLELISM_MAKE`: The number of jobs to process in parallel in the "make" stage. The default is 1 (no parallelism).
- `HLS_COMMAND_PREFIX`: A prefix to use for every command that requires invoking an HLS tool. Use this if you need to set up the environment before calling `make`, for example. This should be a list of strings.

[defaults]: https://github.com/cucapra/polyphemus/blob/master/polyphemus/config_default.py
[f1]: https://aws.amazon.com/ec2/instance-types/f1/
[sdaccel]: https://www.xilinx.com/products/design-tools/software-zone/sdaccel.html
[sdsoc]: https://www.xilinx.com/sdsoc

### Development

Run this command to get a development server, with [Flask][]'s debug mode enabled:

    $ FLASK_APP=polyphemus.server FLASK_ENV=development pipenv run flask run

You can also use `make dev` as a shortcut.
This route automatically starts the necessary worker threads in the same process as the development server.

[flask]: https://flask.palletsprojects.com/

### Deployment

For proper production, there are two differences from running the development version:
you'll want to use a proper web server, and Polyphemus will want to spawn a separate process just for the worker threads.

Use this command to start the workers:

    $ pipenv run python -m polyphemus.workproc

For the server, [Gunicorn][] is a good choice (and included in the dependencies). Here's how you might invoke it:

    $ pipenv run gunicorn polyphemus.server:app

The `make serve` target does that.

The two processes communicate through a Unix domain socket in the instance directory.
You can provide a custom instance directory path to the workproc invocation as an argument.

[gunicorn]: http://gunicorn.org
[pipenv]: http://pipenv.org
[yarn]: https://yarnpkg.com/en/
[npm]: http://npmjs.com


Using Polyphemus
----------------

There is a [browser interface](http://gorgonzola.cs.cornell.edu:8000/) that lets you view jobs and start new ones.
It's also possible to do everything from the command line using [curl][].

[curl]: https://curl.haxx.se

### Submitting Jobs

To submit a job, upload a file to the `/jobs` endpoint:

    $ curl -F file=@foo.zip $POLYPHEMUS/jobs

For example, you can zip up a directory and submit it like this:

    $ zip -r - . | curl -F file='@-;filename=code.zip' $POLYPHEMUS/jobs

If the directory contains data files with `.data` extension, they'll be copied over to the target FPGA.

### Job Options

When submitting a job, you can specify job configuration options as further POST parameters.
Some options are only relevant for a particular HLS workflow (see "Configuration," above).
The options are:

- For all workflows:
    - `skipexec`, to avoid actually trying to run the generated program. (Only necessary when `estimate` is false---estimated runs skip execution by default.)
    - `make`, to use a Makefile instead of the built-in compilation workflow (see "Makefiles," below).
    - `hwname`, which lets you provide a name for the job during Makefile flow.
- For SDSoC only:
    - `estimate`, to use the Xilinx toolchain's resource estimation facility. The job will skip synthesis and execution on the FPGA.
    - `directives`, which lets you provide the name of a TCL file with a set of HLS directives (pragmas) to use during compilation.
- For SDAccel (F1) only:
    - `mode`, which lets you choose between software emulation (`sw_emu`), hardware emulation (`hw_emu`) and full hardware synthesis (`hw`).

Use `-F <option>=<value>` to specify these options with `curl`.

### Viewing Jobs

To see a list of the current jobs, get `/jobs.csv`:

    $ curl $POLYPHEMUS/jobs.csv

To get details about a specific job, request `/jobs/<name>`:

    $ curl $POLYPHEMUS/jobs/d988ruiuAk4

You can also download output files from a job:

    $ curl -O $POLYPHEMUS/jobs/d988ruiuAk4/files/code/compiled.o

There is also a JSON list of all the files at `/jobs/$ID/files`.


Makefiles
---------

Larger projects that use multiple sources and need them to linked in a particular fashion should use the `make` configuration option. With this option, Polyphemus will run the provided Makefile instead of running its own commands and assume that the artifact is built when the command terminates successfully.

For estimation, Polyphemus supplies flags for `sds++` using the `SDSFLAGS` variable. In your Makefile, make sure that you pass in this option when building hardware targets:

```make
%.o: %.c
    sds++ $(SDSFLAGS) $< -o $@
```

For the job configuration options listed above, Polyphemus provides the additional variables `ESTIMATE` and `DIRECTIVES`. Make sure that your Makefile uses these variables to do the appropriate thing during compilation.
