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
    foremost \
    sleuthkit \
    e2fsprogs \
    fdisk \
    testdisk \
    pngcheck \
    imagemagick \
    zbar-tools \
    sox \
    tesseract-ocr \
    netcat-openbsd \
    curl \
    git \
    openssh-client \
    sshpass \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir requests pycryptodome pillow numpy

COPY sandbox/triage sandbox/bits2bytes sandbox/ocr sandbox/diskscan /usr/local/bin/
RUN sed -i 's/\r$//' /usr/local/bin/triage /usr/local/bin/bits2bytes /usr/local/bin/ocr /usr/local/bin/diskscan && chmod 755 /usr/local/bin/triage /usr/local/bin/bits2bytes /usr/local/bin/ocr /usr/local/bin/diskscan

RUN useradd -m -u 1000 agent

RUN mkdir -p /home/agent/.ssh && \
    echo "StrictHostKeyChecking no" > /home/agent/.ssh/config && \
    chown -R agent:agent /home/agent/.ssh

USER agent

WORKDIR /workspace
CMD ["sleep", "infinity"]