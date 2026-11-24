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

COPY sandbox/triage sandbox/bits2bytes sandbox/ocr sandbox/diskscan sandbox/keyhunt sandbox/rsadec sandbox/hashcrack /usr/local/bin/
RUN sed -i 's/\r$//' /usr/local/bin/triage /usr/local/bin/bits2bytes /usr/local/bin/ocr /usr/local/bin/diskscan /usr/local/bin/keyhunt /usr/local/bin/rsadec /usr/local/bin/hashcrack && chmod 755 /usr/local/bin/triage /usr/local/bin/bits2bytes /usr/local/bin/ocr /usr/local/bin/diskscan /usr/local/bin/keyhunt /usr/local/bin/rsadec /usr/local/bin/hashcrack

COPY wordlists /usr/local/share/wordlists.bundled/
# Best-effort: pull the full rockyou list for real dictionary-attack coverage. Never fails the
# build if the network is unavailable at build time - hashcrack falls back to the bundled list.
RUN mkdir -p /usr/local/share/wordlists && cp /usr/local/share/wordlists.bundled/*.txt /usr/local/share/wordlists/ \
    && ( curl -fsSL --max-time 60 -o /usr/local/share/wordlists/rockyou.txt.gz \
         https://weakpass.com/download/90/rockyou.txt.gz && gunzip -f /usr/local/share/wordlists/rockyou.txt.gz \
         || echo "rockyou.txt download skipped (no network at build time); hashcrack will use the bundled list" )

RUN useradd -m -u 1000 agent

RUN mkdir -p /home/agent/.ssh && \
    echo "StrictHostKeyChecking no" > /home/agent/.ssh/config && \
    chown -R agent:agent /home/agent/.ssh

USER agent

WORKDIR /workspace
CMD ["sleep", "infinity"]