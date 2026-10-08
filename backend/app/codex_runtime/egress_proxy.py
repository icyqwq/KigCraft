"""Bounded CONNECT-only egress for an isolated model runtime.

Deploy only on a private per-tenant network, without publishing this port.
This is destination filtering, not TLS interception or a model-call budget.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from dataclasses import dataclass
import ipaddress
import re
import socket

HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
HEADER_LIMIT = 8192
CHUNK = 65536


class Denied(ValueError):
    pass


def hostname(value):
    if not isinstance(value, str) or len(value) > 253 or not HOST.fullmatch(value):
        raise Denied("invalid_destination")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise Denied("literal_address_denied")


def parse_hosts(value):
    hosts = value.split(",")
    if not 1 <= len(hosts) <= 64 or len(set(hosts)) != len(hosts):
        raise Denied("invalid_allowlist")
    return frozenset(hostname(host) for host in hosts)


def destination(header, allowed):
    if len(header) > HEADER_LIMIT or not header.endswith(b"\r\n\r\n"):
        raise Denied("invalid_connect")
    try:
        lines = header.decode("ascii").split("\r\n")
        method, authority, version = lines[0].split(" ")
    except (ValueError, UnicodeError):
        raise Denied("invalid_connect") from None
    if method != "CONNECT" or version != "HTTP/1.1" or not authority.endswith(":443"):
        raise Denied("connect_443_required")
    host = hostname(authority[:-4])
    if host not in allowed:
        raise Denied("destination_not_allowed")
    fields = {}
    for line in lines[1:-2]:
        if not line or line[0].isspace() or ":" not in line:
            raise Denied("invalid_connect")
        key, value = line.split(":", 1)
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key):
            raise Denied("invalid_connect")
        key = key.lower()
        if key in fields or any((ord(char) < 32 and char != chr(9)) or ord(char) == 127 for char in value):
            raise Denied("invalid_connect")
        fields[key] = value.strip(" \t")
    if fields.get("host", authority) != authority or fields.get("content-length", "0") != "0":
        raise Denied("invalid_connect")
    if "transfer-encoding" in fields or "proxy-authorization" in fields or "authorization" in fields:
        raise Denied("invalid_connect")
    return host


@dataclass(frozen=True)
class Limits:
    connections: int = 32
    header_seconds: float = 10
    connect_seconds: float = 15
    idle_seconds: float = 300
    lifetime_seconds: float = 3600
    tunnel_bytes: int = 256 * 1024 * 1024

    def __post_init__(self):
        if (not 1 <= self.connections <= 256 or not 1 <= self.header_seconds <= 60
                or not 1 <= self.connect_seconds <= 60 or not 1 <= self.idle_seconds <= 600
                or not 1 <= self.lifetime_seconds <= 7200
                or not 1 <= self.tunnel_bytes <= 1024 * 1024 * 1024):
            raise ValueError("invalid_proxy_limits")


async def public_addresses(host):
    # Resolve once, validate every result, then connect to a numeric address so a
    # second lookup cannot rebind the allowlisted hostname to a private service.
    records = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not records or len(records) > 32:
        raise Denied("destination_unavailable")
    addresses = []
    for family, kind, protocol, _, address in records:
        if family not in {socket.AF_INET, socket.AF_INET6} or kind != socket.SOCK_STREAM:
            raise Denied("invalid_resolution")
        value = ipaddress.ip_address(address[0])
        if (not value.is_global or value.is_multicast or
                isinstance(value, ipaddress.IPv6Address) and
                (value.ipv4_mapped is not None or value.sixtofour is not None or value.teredo is not None
                 or value in ipaddress.ip_network("64:ff9b::/96")
                 or value in ipaddress.ip_network("64:ff9b:1::/48"))):
            raise Denied("non_public_destination")
        pair = (family, str(value))
        if pair not in addresses:
            addresses.append(pair)
    return addresses


async def connect_public(host):
    addresses = await public_addresses(host)
    for family, address in addresses:
        try:
            return await asyncio.open_connection(address, 443, family=family, limit=CHUNK)
        except OSError:
            continue
    raise Denied("destination_unavailable")


async def close_writer(writer):
    if writer is None:
        return
    writer.close()
    with suppress(Exception):
        await asyncio.wait_for(writer.wait_closed(), 2)


class EgressProxy:
    def __init__(self, allowed, limits=None):
        self.allowed = parse_hosts(",".join(sorted(allowed)))
        self.limits = limits or Limits()
        self.active = 0
        self.tasks = set()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        admitted = self.active < self.limits.connections
        upstream = None
        established = False
        pumps = []
        if admitted:
            self.active += 1
        try:
            if not admitted:
                writer.write(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n")
                await asyncio.wait_for(writer.drain(), self.limits.header_seconds)
                return
            async with asyncio.timeout(self.limits.header_seconds):
                header = await reader.readuntil(b"\r\n\r\n")
                host = destination(header, self.allowed)
            async with asyncio.timeout(self.limits.connect_seconds):
                remote, upstream = await connect_public(host)
            established = True
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await asyncio.wait_for(writer.drain(), self.limits.header_seconds)
            transferred = 0
            activity = asyncio.get_running_loop().time()

            async def pump(source, target):
                nonlocal transferred, activity
                while data := await source.read(CHUNK):
                    transferred += len(data)
                    if transferred > self.limits.tunnel_bytes:
                        raise Denied("tunnel_limit")
                    target.write(data)
                    await target.drain()
                    activity = asyncio.get_running_loop().time()

            async def idle():
                while True:
                    remaining = self.limits.idle_seconds - (asyncio.get_running_loop().time() - activity)
                    if remaining <= 0:
                        raise Denied("idle_timeout")
                    await asyncio.sleep(remaining)

            async with asyncio.timeout(self.limits.lifetime_seconds):
                pumps = [asyncio.create_task(pump(reader, upstream)),
                         asyncio.create_task(pump(remote, writer)),
                         asyncio.create_task(idle())]
                done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
                for completed in done:
                    completed.result()
        except asyncio.CancelledError:
            raise
        except Exception:
            # No DNS errors, target URLs, tunnel bytes, tokens or auth material in
            # responses or logs. Once established, close rather than corrupt TLS.
            if not established:
                with suppress(Exception):
                    writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                    await asyncio.wait_for(writer.drain(), 2)
        finally:
            for pump_task in pumps:
                pump_task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            await close_writer(upstream)
            await close_writer(writer)
            if admitted:
                self.active -= 1
            self.tasks.discard(task)

    async def close(self):
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def serve(host, port, allowed):
    proxy = EgressProxy(allowed)
    server = await asyncio.start_server(proxy.handle, host=host, port=port, limit=HEADER_LIMIT)
    try:
        async with server:
            await server.serve_forever()
    finally:
        await proxy.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-hosts", required=True, help="Comma-separated exact lowercase DNS names; no wildcards")
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3128)
    args = parser.parse_args()
    try:
        allowed = parse_hosts(args.allow_hosts)
        if args.listen not in {"0.0.0.0", "127.0.0.1"} or not 1024 <= args.port <= 65535:
            raise Denied("invalid_listener")
        asyncio.run(serve(args.listen, args.port, allowed))
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit("egress_proxy_unavailable") from None


if __name__ == "__main__":
    main()
