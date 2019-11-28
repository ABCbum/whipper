FROM debian:buster

RUN apt-get update && apt-get install --no-install-recommends -y \
        autoconf \
        automake \
        bzip2 \
        libtool \
        make \
        pkgconf \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && curl -o - 'https://ftp.gnu.org/gnu/libcdio/libcdio-2.1.0.tar.bz2' | tar jxf - \
    && cd libcdio-2.1.0 \
    && autoreconf -fi \
    && ./configure --disable-dependency-tracking --disable-cxx --disable-example-progs --disable-static \
    && make install \
    && cd .. \
    && rm -rf libcdio-2.1.0
    && curl -o - 'https://ftp.gnu.org/gnu/libcdio/libcdio-paranoia-10.2+2.0.0.tar.bz2' | tar jxf - \
    && cd libcdio-paranoia-10.2+2.0.0 \
    && autoreconf -fi \
    && ./configure --disable-dependency-tracking --disable-example-progs --disable-static \
    && make install \
    && cd .. \
    && rm -rf libcdio-paranoia-10.2+2.0.0 \
    && ldconfig \
    && apt-get purge -y \
        autoconf \
        automake \
        bzip2 \
        libtool \
        make \
        pkgconf \
    && apt-get autoremove --purge -y \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# add user
RUN useradd -m worker -G cdrom \
    && mkdir -p /output /home/worker/.config/whipper \
    && chown worker: /output /home/worker/.config/whipper
VOLUME ["/home/worker/.config/whipper", "/output"]

# setup locales + cleanup
RUN apt-get install --no-install-recommends -y locales \
    && echo "LC_ALL=en_US.UTF-8" >> /etc/environment \
    && echo "en_US.UTF-8 UTF-8" >> /etc/locale.gen \
    && echo "LANG=en_US.UTF-8" > /etc/locale.conf \
    && locale-gen en_US.UTF-8 \
    && apt-get purge -y locales \
    && apt-get autoremove --purge -y \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# install whipper
RUN mkdir /whipper
COPY . /whipper/
RUN cd /whipper \
    && apt-get install --no-install-recommends -y \
        cdrdao \
        eject \
        flac \
        gir1.2-glib-2.0 \
        git \
        libiso9660-dev \
        libsndfile1 \
        libsndfile1-dev \
        python3-dev \
        python3-gi \
        python3-musicbrainzngs \
        python3-mutagen \
        python3-pip \
        python3-requests \
        python3-ruamel.yaml \
        python3-setuptools \
        sox \
        swig \
    && pip3 --no-cache-dir install pycdio==2.1.0 \
    && python3 setup.py install \
    && rm -rf /whipper \
    && apt-get purge -y \
        gir1.2-glib-2.0 \
        git \
        libiso9660-dev \
        libsndfile1-dev \
        python3-dev \
        python3-pip \
        python3-setuptools \
        swig \
    && apt-get autoremove -y --purge \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && whipper -v

ENV LC_ALL=en_US.UTF-8
ENV LANG=en_US
ENV LANGUAGE=en_US.UTF-8
ENV PYTHONIOENCODING=utf-8

USER worker
WORKDIR /output
ENTRYPOINT ["whipper"]
