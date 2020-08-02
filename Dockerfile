FROM python:3
MAINTAINER Adrian Sampson <asampson@cs.cornell.edu>
MAINTAINER Rachit Nigam <rnigam@cs.cornell.edu>

# Add OpenSSH and the sshpass utility for Zynq execution. Add curl to enable
# communication with other containers.
RUN apt-get update && \
    apt-get install -y openssh-client sshpass curl

# We use pipenv for our setup.
RUN pip3 install pipenv

# Volume, port, and command for running Polyphemus.
VOLUME /polyphemus/instance
EXPOSE 8000
CMD ["pipenv", "run", \
     "gunicorn", "--bind", "0.0.0.0:8000", \
     "polyphemus.server:app"]

# Add source.
ADD . /polyphemus
WORKDIR /polyphemus

# Set up Polyphemus.
RUN pipenv install
