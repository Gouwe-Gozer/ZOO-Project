# Fresh-clone handoff: ZOO-Project local Docker fixes

Use this guide on a fresh clone of this repository. Keep the changes limited to the files listed below. There are two independent fixes:

1. The OGC API `/processes` response is truncated because ZOO prints serialized JSON as a `printf` format string.
2. Asynchronous jobs need the repository startup script mounted into `zoofpm`, plus correct RabbitMQ and writable runtime paths.

If only `/ogc-api/processes` must be fixed, apply sections 1–3 and the `/processes` validations. The `startUp.sh` changes in section 4 are for asynchronous execution and are not part of the JSON truncation root cause.

## 1. Fix the ZOO response output

In `zoo-project/zoo-kernel/zoo_service_loader.c`, change:

```c
printf(pmResponseObject->value);
```

to:

```c
printf("%s",pmResponseObject->value);
```

Why: `response_print.c` calculates `Content-Length` from the literal serialized JSON. The unsafe call interprets `%` sequences in that JSON and emits more bytes. Apache then correctly stops at the declared length, leaving invalid JSON. Printing with `"%s"` makes the emitted body match the declared length and removes a format-string vulnerability.

Do not solve this with a low default `limit`, an Apache buffer change, or by removing `Content-Length`.

## 2. Add a focused compatible build

The published `zooproject/zoo-project:latest` image uses Ubuntu 18.04 and does not contain the checkout's source. Building the repository's main `Dockerfile` currently gets past compiling ZOO but later fails in an unrelated `npm install gdal-async` step. Do not change or pin that unrelated dependency for this fix.

Create `docker/zookernel-local.Dockerfile`:

```dockerfile
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
```

This preserves the published image's Apache, Python 3.6, SpiderMonkey, MapServer, OTB 7, SAGA 7, and library ABI. Only the rebuilt ZOO CGI/FPM executables are copied into the runtime image.

## 3. Make Compose use the patched image

In `docker-compose.yml`, change `zookernel` to:

```yaml
  zookernel:
    platform: linux/amd64
    image: zoo-project:local
    build:
      context: .
      dockerfile: docker/zookernel-local.Dockerfile
```

Change `zoofpm` from:

```yaml
image: zooproject/zoo-project:latest
```

to:

```yaml
image: zoo-project:local
```

Both services should use the same patched executable build.

## 4. Apply the separate asynchronous-worker startup fix

Under the `zoofpm` volumes in `docker-compose.yml`, mount the repository script:

```yaml
- ./docker/startUp.sh:/startUp.sh:ro
```

In `docker/startUp.sh`, immediately after creating `statusInfos`, add:

```sh
mkdir -p /tmp/zTmp/statusInfos
chmod 0777 /tmp/zTmp
chown www-data:www-data /tmp/zTmp/statusInfos
```

Use the Compose RabbitMQ hostname instead of the obsolete hard-coded container name:

```sh
CMD="curl -o /tmp/toto.out http://${ZOO_RABBITMQ_HOST}:15672"
```

After creating the FPM log, make it writable by the worker:

```sh
touch /var/log/zoofpm.log
chown www-data:www-data /var/log/zoofpm.log
```

Do **not** run `chmod -R 777 docker` on the host. The only broad permission change in the working setup is `chmod 0777 /tmp/zTmp` inside the container startup script; it is not recursive and targets only the runtime bind mount root.

## 5. Recreate before testing

Do not test an already-running container. It will still use the previous image.

```bash
docker compose down
docker compose up -d --build
docker compose ps
```

Confirm both `zookernel` and `zoofpm` show `zoo-project:local`.

## 6. Required validation

Full collection:

```bash
curl -sS http://localhost/ogc-api/processes \
  -o /tmp/zoo-processes-fixed.json

jq empty /tmp/zoo-processes-fixed.json
jq '.processes | length' /tmp/zoo-processes-fixed.json
jq '.numberTotal' /tmp/zoo-processes-fixed.json
tail -c 150 /tmp/zoo-processes-fixed.json
```

Expected for the current process set:

```text
703
703
```

The tail must contain the completed top-level `links` array and `"numberTotal":703}`.

Limited collection:

```bash
curl -sS 'http://localhost/ogc-api/processes?limit=50' \
  -o /tmp/zoo-processes-limit-50.json

jq empty /tmp/zoo-processes-limit-50.json
jq '.processes | length' /tmp/zoo-processes-limit-50.json
```

Expected length: `50`.

HTML representation:

```bash
curl -sS -o /tmp/zoo-processes.html \
  -w '%{http_code}\n' \
  http://localhost/ogc-api/processes.html
```

Expected: HTTP `200`, with no `Unterminated string` error.

For the asynchronous-worker fix, submit a `Prefer: respond-async` request and poll its `Location` URL until `status` is `successful`.

## 7. Keep out of the patch

- Do not add or commit `docker/tmp/`; it is runtime output.
- Do not modify Apache, FastCGI, proxy, or pagination limits for the truncation issue.
- Do not commit temporary response files from `/tmp`.
- Do not carry over experimental full-image build changes or npm dependency pins.
- Do not revert the API source fix after rebuilding; the Dockerfile deliberately compiles that checked-out source.

Final expected source changes are therefore:

- `zoo-project/zoo-kernel/zoo_service_loader.c`
- `docker/zookernel-local.Dockerfile`
- `docker-compose.yml`
- `docker/startUp.sh` only when asynchronous execution must work

