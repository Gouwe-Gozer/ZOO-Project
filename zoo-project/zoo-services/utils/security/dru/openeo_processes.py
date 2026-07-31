#
# Author : Gérald Fenoy
#
# Copyright 2026 GeoLabs SARL. All rights reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including with
# out limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
# OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch processes from an OGC API - Processes server and convert them to openEO."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import urllib.parse
from pathlib import Path

from openeo_ogc_converter import build_processes_output_from_source
import zoo

INTERNAL_SUFFIXES = (".svc", ".svc.cluster.local", ".cluster.local", ".local")


def is_internal_host(host: str) -> bool:
    """Return True for hosts that should bypass an HTTP proxy."""
    if not host:
        return False
    if host in {"localhost"}:
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if "." not in host:  # bare service name, e.g. "zoo-project-dru-service"
        return True
    return any(host.endswith(suffix) for suffix in INTERNAL_SUFFIXES)


def bypass_proxy_for(url: str) -> None:
    """Add the URL host to ``no_proxy``/``NO_PROXY`` so urllib skips the proxy."""
    host = urllib.parse.urlparse(url).hostname or ""
    if not host:
        return
    for var in ("no_proxy", "NO_PROXY"):
        current = os.environ.get(var, "")
        entries = [e.strip() for e in current.split(",") if e.strip()]
        if host not in entries:
            entries.append(host)
            os.environ[var] = ",".join(entries)
    zoo.debug(f"Proxy bypassed for host {host} (no_proxy={os.environ.get('no_proxy')})")


def resolve_auth_headers(conf) -> dict | None:
    """Forward the incoming Authorization header to the OGC API requests.

    The token used to call this ZOO service is reused to list ``/processes`` and
    fetch each ``/processes/{id}`` description, so protected processes remain
    accessible. Returns ``None`` when no Authorization header is present.
    """
    renv = conf.get("renv") if isinstance(conf, dict) else None
    if isinstance(renv, dict):
        for key, value in renv.items():
            if "HTTP_AUTHORIZATION" in key and isinstance(value, str) and value.strip():
                return {"Authorization": value.strip()}
    return None


def openeo_converter(conf,inputs,outputs) -> int:
    try:
        source=f"{conf['openapi']['realRootUrl']}{conf['lenv']['fpm_user']}/{conf['openapi']['rootPath']}"
    except Exception as e:
        zoo.error(f"Error constructing source URL: {e}")
        return zoo.SERVICE_FAILED

    host = urllib.parse.urlparse(source).hostname or ""
    if is_internal_host(host):
        bypass_proxy_for(source)
        zoo.info(f"Internal host detected ({host}): HTTP proxy bypassed.")

    headers = resolve_auth_headers(conf)
    if headers:
        zoo.info("Authorization header found: forwarded to OGC API requests.")

    output = build_processes_output_from_source(
        source=source,
        timeout=10,
        delay=0,
        limit=None,
        max_processes=None,
        skip_full_fetch=False,
        split_multi_outputs=False,
        version="1.2.0",
        accept_single_process_description=False,
        headers=headers,
    )
    output["links"] = [
        {
            "rel": "alternate",
            "href": f"{conf['openapi']['rootHost']}/{conf['lenv']['fpm_user']}/{conf['openapi']['rootPath']}/processes",
            "type": "application/json"
        },
        {
            "rel": "profile",
            "href": "https://www.opengis.net/dev/profile/OGC/0/openeo-process-list",
        }
    ]
    conf["lenv"]["response"] = json.dumps(output, ensure_ascii=False, indent=True)
    conf["headers"]["Link"] = (
        f"<https://www.opengis.net/dev/profile/OGC/0/openeo-process-list>; rel=\"profile\", "
        f"<{conf['openapi']['rootHost']}/{conf['lenv']['fpm_user']}/{conf['openapi']['rootPath']}/processes>; rel=\"alternate\"; "
        f"type=\"application/json\"; format=\"https://www.opengis.net/dev/profile/OGC/0/ogc-process-list\""
    )
    if not(conf["renv"]["FAKE_HTTP_ACCEPT_PROFILE"]):
        conf["headers"]["Content-Type"] = "application/json; profile=\"https://www.opengis.net/dev/profile/OGC/0/openeo-process-list\""
    else:
        conf["headers"]["Content-Type"] = "application/json"
    conf["headers"]["status"] = "200"

    return zoo.SERVICE_SUCCEEDED
