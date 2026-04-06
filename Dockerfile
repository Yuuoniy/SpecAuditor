FROM ubuntu:22.04

ARG OH_MY_ZSH_COMMIT=887a864aba396c0e6dcf7c0254f455676f830daa
ARG TMUX_CONF_COMMIT=af33f07134b76134acca9d01eacbdecca9c9cda6
ARG WEGGLI_COMMIT=bf6453b03517a3ca3eec23e3be9f12cf60c0c614

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHON_VERSION=3.13.2
ENV LANG=en_US.UTF-8
ENV LANGUAGE=en_US:en
ENV LC_ALL=en_US.UTF-8
ENV SHELL=/bin/zsh
ENV ZSH=/root/.oh-my-zsh

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    ca-certificates \
    cargo \
    curl \
    git \
    libbz2-dev \
    libffi-dev \
    libgdbm-dev \
    libgdbm-compat-dev \
    libmagic1 \
    liblzma-dev \
    libncurses5-dev \
    libnss3-dev \
    libreadline-dev \
    libsqlite3-dev \
    libssl-dev \
    locales \
    rustc \
    tk-dev \
    tmux \
    uuid-dev \
    wget \
    xz-utils \
    zlib1g-dev \
    zsh \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /tmp/python-build && \
    cd /tmp/python-build && \
    curl -fsSLO https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz && \
    tar -xzf Python-${PYTHON_VERSION}.tgz && \
    cd Python-${PYTHON_VERSION} && \
    ./configure --prefix=/usr/local --with-ensurepip=install && \
    make -j"$(nproc)" && \
    make altinstall && \
    ln -sf /usr/local/bin/python3.13 /usr/local/bin/python3 && \
    ln -sf /usr/local/bin/python3.13 /usr/local/bin/python && \
    python3 -m pip install --upgrade pip && \
    cd / && \
    rm -rf /tmp/python-build

RUN python3 --version && \
    python --version && \
    python3 -c "import sys; assert sys.version_info[:3] == (3, 13, 2), sys.version" && \
    python -c "import sys; assert sys.version_info[:3] == (3, 13, 2), sys.version"

RUN sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen

# set the proxy if needed
# ENV http_proxy=http://XX.XX.XX.XX:XX
# ENV https_proxy=http://XX.XX.XX.XX:XX

RUN git clone https://github.com/robbyrussell/oh-my-zsh.git /tmp/oh-my-zsh && \
    cd /tmp/oh-my-zsh && \
    git checkout "${OH_MY_ZSH_COMMIT}" && \
    cp -r /tmp/oh-my-zsh /root/.oh-my-zsh && \
    cp /root/.oh-my-zsh/templates/zshrc.zsh-template /root/.zshrc && \
    sed -i 's/ZSH_THEME="robbyrussell"/ZSH_THEME="ys"/g' /root/.zshrc && \
    chsh -s /bin/zsh root && \
    rm -rf /tmp/oh-my-zsh

RUN git clone https://github.com/gpakosz/.tmux.git /root/.tmux && \
    cd /root/.tmux && \
    git checkout "${TMUX_CONF_COMMIT}" && \
    ln -s -f /root/.tmux/.tmux.conf /root/.tmux.conf && \
    cp /root/.tmux/.tmux.conf.local /root/.tmux.conf.local

WORKDIR /root/tools
RUN git clone https://github.com/Yuuoniy/weggli.git && \
    cd weggli && \
    git checkout log-var && \
    git checkout "${WEGGLI_COMMIT}"

WORKDIR /root/tools/weggli
RUN cargo build --release
RUN ln -s /root/tools/weggli/target/release/weggli /usr/bin/weggli

WORKDIR /workspace/SpecAuditor

COPY requirements.txt ./requirements.txt
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install -r requirements.txt

COPY . .

RUN rm -rf scripts/tree-sitter-c scripts/build && \
    mkdir -p scripts/build && \
    git clone https://github.com/tree-sitter/tree-sitter-c scripts/tree-sitter-c && \
    cd scripts/tree-sitter-c && \
    git checkout e348e8ec5efd3aac020020e4af53d2ff18f393a9 && \
    cd /workspace/SpecAuditor

ENV PYTHONPATH=/workspace/SpecAuditor

CMD ["/bin/zsh"]
