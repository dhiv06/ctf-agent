FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    binutils \
    file \
    binwalk \
    libimage-exiftool-perl \
    steghide \
    xxd \
    unzip \
    p7zip-full \
    netcat-openbsd \
    curl \
    git \
    openssh-client \
    sshpass \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir requests pycryptodome

RUN useradd -m -u 1000 agent

RUN mkdir -p /home/agent/.ssh && \
    echo "StrictHostKeyChecking no" > /home/agent/.ssh/config && \
    chown -R agent:agent /home/agent/.ssh

USER agent

WORKDIR /workspace
CMD ["sleep", "infinity"]