FROM zooproject/zoo-project:latest AS builder

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       autoconf \
       bison \
       build-essential \
       flex \
       libaprutil1-dev \
       libfcgi-dev \
       libfftw3-dev \
       libgdal-dev \
       libjson-c-dev \
       libkrb5-dev \
       libotb-dev \
       libpq-dev \
       librabbitmq-dev \
       r-base-dev \
       libsaga-dev \
       libssh2-1-dev \
       libssl-dev \
       libtinyxml-dev \
       libwxgtk3.0-dev \
       libxml2-dev \
       libxslt1-dev \
       nlohmann-json-dev \
       python3-dev \
       uuid-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY thirds/cgic206 thirds/cgic206
COPY zoo-project/zoo-kernel zoo-project/zoo-kernel

RUN make -C thirds/cgic206 libcgic.a \
    && cd zoo-project/zoo-kernel \
    && autoconf \
    && ./configure \
       --with-rabbitmq=yes \
       --with-python=/usr \
       --with-pyvers=3.6 \
       --with-js=/usr \
       --with-mapserver=/usr \
       --with-ms-version=7 \
       --with-json=/usr \
       --with-r=/usr \
       --with-db-backend \
       --prefix=/usr \
       --with-otb=/usr \
       --with-itk=/usr \
       --with-otb-version=7.0 \
       --with-itk-version=4.12 \
       --with-saga=/usr \
       --with-saga-version=7.2 \
       --with-wx-config=/usr/bin/wx-config \
    && make -j4 zoo_loader.cgi zoo_loader_fpm

FROM zooproject/zoo-project:latest

COPY --from=builder /build/zoo-project/zoo-kernel/zoo_loader.cgi /usr/lib/cgi-bin/zoo_loader.cgi
COPY --from=builder /build/zoo-project/zoo-kernel/zoo_loader_fpm /usr/lib/cgi-bin/zoo_loader_fpm

