"""Immutable per-account sandbox layout and fail-closed Docker inspection policy."""
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from uuid import UUID

from app.codex_runtime.egress_proxy import parse_hosts
from app.codex_runtime.rpc import RpcError

IMAGE = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}")
ROLE = "com.kigcraft.v2.role"
OWNER = "com.kigcraft.v2.owner"
POLICY = "com.kigcraft.v2.sandbox"
IDLE = ["/usr/local/bin/python", "-c", "import signal; signal.pause()"]
BASE_ENV = {"PATH", "LANG", "GPG_KEY", "PYTHON_VERSION", "PYTHON_SHA256",
            "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED"}
HEALTH = ["CMD", "python", "-c", "import socket; s=socket.create_connection(('127.0.0.1',3128),2); s.sendall(b'GET / HTTP/1.1\\r\\n\\r\\n'); assert s.recv(64).startswith(b'HTTP/1.1 403'); s.close()"]


def refuse(condition):
    if not condition:
        raise RpcError("container_policy_mismatch")


@dataclass(frozen=True)
class ContainerSpec:
    owner: str
    base: str
    image: str
    egress_image: str
    hosts: tuple[str, ...]
    uid: int = 10001
    gid: int = 10001

    def __post_init__(self):
        try:
            valid_owner = str(UUID(self.owner)) == self.owner
        except (ValueError, TypeError, AttributeError):
            valid_owner = False
        path = PurePosixPath(self.base)
        refuse(valid_owner and path.is_absolute() and str(path) == self.base
               and len(path.parts) >= 4 and ".." not in path.parts
               and not any(char in self.base for char in ",:\\\n\r\x00")
               and (self.base.startswith("/srv/kigcraft/") or self.base.startswith("/var/lib/kigcraft/")))
        refuse(bool(IMAGE.fullmatch(self.image)) and bool(IMAGE.fullmatch(self.egress_image)))
        refuse(type(self.uid) is int and type(self.gid) is int and 10000 <= self.uid <= 2147483647 and 10000 <= self.gid <= 2147483647)
        refuse(tuple(sorted(parse_hosts(",".join(self.hosts)))) == self.hosts)

    @property
    def prefix(self):
        return "kig-v2-" + self.owner.replace("-", "")

    @property
    def profile(self):
        return str(PurePosixPath(self.base) / self.owner / "profile")

    @property
    def work(self):
        return str(PurePosixPath(self.base) / self.owner / "work")

    @property
    def inputs(self):
        return str(PurePosixPath(self.base) / self.owner / "inputs")

    @property
    def runtime(self):
        return self.prefix + "-runtime"

    @property
    def proxy(self):
        return self.prefix + "-egress"

    @property
    def internal(self):
        return self.prefix + "-internal"

    @property
    def external(self):
        return self.prefix + "-outbound"

    def labels(self, role):
        return {ROLE: role, OWNER: self.owner, POLICY: "1"}

    def runtime_env(self):
        proxy = "http://egress:3128"
        return {"HOME": "/tmp", "CODEX_HOME": self.profile, "CODEX_MCP_NODE_PATH": "/opt/codex/node",
                "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "ALL_PROXY": proxy, "NO_PROXY": "",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}

    def compose(self):
        def service(role, image, memory, cpus, pids):
            return {"image": image, "pull_policy": "never", "container_name": self.runtime if role == "runtime" else self.proxy,
                "labels": self.labels(role), "user": f"{self.uid}:{self.gid}", "read_only": True,
                "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"], "init": True,
                "ipc": "private", "cgroup": "private", "restart": "no",
                "mem_limit": memory, "memswap_limit": memory, "cpus": cpus, "pids_limit": pids,
                "tmpfs": ["/tmp:rw,noexec,nosuid,nodev,size=64m"],
                "logging": {"driver": "none"}}
        runtime = service("runtime", self.image, 2147483648, 2.0, 128)
        runtime.update(profiles=["runtime"], entrypoint=IDLE, command=[], working_dir=self.work,
            environment=self.runtime_env(), dns=["127.0.0.1"], networks=[self.internal],
            volumes=[{"type": "bind", "source": path, "target": path, "read_only": path == self.inputs,
                      "bind": {"create_host_path": False, "propagation": "rprivate"}}
                     for path in (self.profile, self.work, self.inputs)])
        proxy = service("egress", self.egress_image, 134217728, .5, 64)
        proxy.update(entrypoint=["python", "/opt/egress/egress_proxy.py"],
            command=["--allow-hosts", ",".join(self.hosts)],
            environment={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"},
            healthcheck={"test": HEALTH, "interval": "10s", "timeout": "3s", "retries": 3},
            networks={self.internal: {"aliases": ["egress"]}, self.external: {}})
        return {"name": self.prefix, "services": {"runtime": runtime, "egress": proxy}, "networks": {
            self.internal: {"name": self.internal, "driver": "bridge", "internal": True,
                "enable_ipv6": False, "labels": self.labels("internal"),
                "driver_opts": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated",
                                "com.docker.network.bridge.gateway_mode_ipv6": "isolated"}},
            self.external: {"name": self.external, "driver": "bridge", "enable_ipv6": False,
                            "labels": self.labels("outbound")}}}

    def inspect_container(self, value, role):
        runtime = role == "runtime"
        config, host = value.get("Config", {}), value.get("HostConfig", {})
        refuse(value.get("Name") == "/" + (self.runtime if runtime else self.proxy))
        refuse(all(config.get("Labels", {}).get(k) == v for k, v in self.labels(role).items()))
        refuse(config.get("Image") == (self.image if runtime else self.egress_image))
        refuse(config.get("User") == f"{self.uid}:{self.gid}" and host.get("ReadonlyRootfs") is True)
        refuse(host.get("Privileged") is False and host.get("CapDrop") == ["ALL"] and not host.get("CapAdd"))
        refuse(host.get("SecurityOpt") in (["no-new-privileges:true"], ["no-new-privileges"]))
        refuse(host.get("Init") is True and host.get("IpcMode") == "private" and host.get("CgroupnsMode") == "private")
        refuse(host.get("PidMode", "") == "" and host.get("UTSMode", "") == "")
        refuse(not any(host.get(key) for key in ("Devices", "DeviceRequests", "VolumesFrom",
            "PortBindings", "ExtraHosts", "Links", "Sysctls")))
        refuse(host.get("RestartPolicy", {}).get("Name") == "no" and host.get("LogConfig", {}).get("Type") == "none")
        refuse(host.get("Memory") == host.get("MemorySwap") == (2147483648 if runtime else 134217728))
        refuse(host.get("NanoCpus") == (2000000000 if runtime else 500000000) and host.get("PidsLimit") == (128 if runtime else 64))
        refuse(host.get("Tmpfs") == {"/tmp": "rw,noexec,nosuid,nodev,size=64m"})
        expected_networks = {self.internal} if runtime else {self.internal, self.external}
        refuse(set(value.get("NetworkSettings", {}).get("Networks", {})) == expected_networks)
        refuse(not host.get("NetworkMode", "").startswith(("host", "container:")))
        env = {}
        for item in config.get("Env", []):
            key, sep, content = item.partition("=")
            refuse(bool(sep) and key not in env)
            env[key] = content
        expected = self.runtime_env() if runtime else {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
        refuse(all(env.get(key) == item for key, item in expected.items()) and not set(env) - set(expected) - BASE_ENV)
        mounts = value.get("Mounts", [])
        for mount in mounts:
            if mount.get("Type") == "tmpfs":
                refuse(mount.get("Destination") == "/tmp" and mount.get("RW") is True)
        refuse(sum(m.get("Type") == "tmpfs" for m in mounts) <= 1)
        mounts = [m for m in mounts if m.get("Type") != "tmpfs"]
        if runtime:
            refuse(config.get("Entrypoint") == IDLE and not config.get("Cmd") and config.get("WorkingDir") == self.work)
            refuse(host.get("Dns") == ["127.0.0.1"] and len(mounts) == 3)
            for path in (self.profile, self.work, self.inputs):
                matches = [m for m in mounts if m.get("Source") == path and m.get("Destination") == path]
                refuse(len(matches) == 1 and matches[0].get("Type") == "bind"
                       and matches[0].get("RW") is (path != self.inputs) and matches[0].get("Propagation") == "rprivate")
        else:
            refuse(not mounts and config.get("Entrypoint") == ["python", "/opt/egress/egress_proxy.py"]
                   and config.get("Cmd") == ["--allow-hosts", ",".join(self.hosts)]
                   and config.get("Healthcheck", {}).get("Test") == HEALTH)
            refuse(value.get("State", {}).get("Running") is True
                   and value.get("State", {}).get("Health", {}).get("Status") == "healthy")

    def inspect_network(self, value, runtime_id, proxy_id, *, internal):
        name, role = (self.internal, "internal") if internal else (self.external, "outbound")
        refuse(value.get("Name") == name and value.get("Driver") == "bridge"
               and value.get("Internal") is internal and value.get("EnableIPv6") is False)
        refuse(all(value.get("Labels", {}).get(k) == v for k, v in self.labels(role).items()))
        peers = set(value.get("Containers", {}))
        refuse(proxy_id in peers and peers <= ({runtime_id, proxy_id} if internal else {proxy_id}))
        options = value.get("Options", {})
        if internal:
            refuse(all(options.get("com.docker.network.bridge.gateway_mode_" + family) == "isolated"
                       for family in ("ipv4", "ipv6")))
        else:
            refuse(options.get("com.docker.network.bridge.gateway_mode_ipv4", "nat") == "nat")
