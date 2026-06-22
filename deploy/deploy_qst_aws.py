#!/usr/bin/env python3
"""
deploy_qst_aws.py - One-shot AWS deployment for QST (Quant Swarm Terminal),
preserving the *frozen Golden State* and keeping all background loops OFF.

----------------------------------------------------------------------------
WHAT THIS SCRIPT DOES (pipeline)
----------------------------------------------------------------------------
  1. PROVISION   - boto3 launches an Ubuntu 22.04 EC2 instance (t3.xlarge by
                   default) with a 50 GB gp3 root volume and a *strict* security
                   group (SSH + dashboard from YOUR IP only; nothing else).
  2. GOLDEN STATE- exports the two Docker *named volumes* that actually hold the
                   frozen data and restores them on the instance 1:1.
  3. SYNC        - uploads the project source (minus node_modules / venv /
                   __pycache__ / logs) and a hardened copy of `.env`.
  4. RUN         - installs Docker + Compose v2, restores the volumes, then
                   `docker compose up -d --build`, and verifies health.
  5. ACCESS      - prints the exact SSH local-port-forward command for the
                   internal tools (Langfuse / n8n / Jaeger / Phoenix / Grafana).

----------------------------------------------------------------------------
[!]  CRITICAL CORRECTION TO THE NAIVE "copy ./data" APPROACH
----------------------------------------------------------------------------
The QST Golden State is NOT in the host `./data` folder (that copy is stale).
`docker-compose.yml` mounts the live SQLite DBs into **named Docker volumes**:

    trading-desk_agent_memory  ->  /srv/data      (synthesis_reports.db = the
                                                    Golden-Run reports, ingestion
                                                    cache, briefing, users.db...)
    trading-desk_chroma_data   ->  /srv/chroma_db (RAG vector store, ~119 MB)

So this script tars those *volumes* and restores them into identically-named
volumes on EC2. Because compose pins `name: trading-desk`, the restored volume
names line up automatically and `docker compose up` reuses them (with data)
instead of creating empty ones. Copying `./data` would silently lose the run.

----------------------------------------------------------------------------
SECURITY DESIGN (zero-trust, defense-in-depth)
----------------------------------------------------------------------------
- The Security Group opens ONLY:
      - tcp/22   (SSH)       from <your-ip>/32
      - tcp/3002 (dashboard) from <your-ip>/32   (set HTTP_OPEN_TO_WORLD=True
                                                   to widen to 0.0.0.0/0)
- Every internal/management port (8000-8004, 3003, 16686, 6006, 5678, ...) is
  deliberately NEVER added to the SG. Docker still publishes them on the host,
  but with no SG rule they are unreachable from the internet - you reach them
  ONLY through the SSH tunnel (Local Port Forwarding) printed at the end.
- Egress is left open (instance needs to pull images / call the Gemini API).
- The private key is written 0600 and never leaves your machine; `.env` (with
  the API keys) is transferred over the encrypted SSH/SFTP channel only.

----------------------------------------------------------------------------
PREREQUISITES (local machine)
----------------------------------------------------------------------------
    pip install boto3 paramiko
    # AWS credentials configured (aws configure  /  env vars  /  IAM role)
    # Docker Desktop running locally (needed to export the named volumes)

USAGE
    python deploy/deploy_qst_aws.py                 # full deploy
    python deploy/deploy_qst_aws.py --teardown      # delete instance + SG + key
    python deploy/deploy_qst_aws.py --instance-type t3.large
    python deploy/deploy_qst_aws.py --region eu-west-1 --open-http
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import os
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

# Windows consoles default to cp1252 and crash printing non-ASCII. Force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("ERROR: boto3 not installed ->  pip install boto3 paramiko")

try:
    import paramiko
except ImportError:
    sys.exit("ERROR: paramiko not installed ->  pip install boto3 paramiko")


# ============================================================================
# CONFIGURATION  (override most of these via CLI flags)
# ============================================================================
PROJECT_ROOT   = Path(__file__).resolve().parents[1]      # repo root (.../QST)
COMPOSE_PROJECT = "trading-desk"                            # MUST match `name:` in compose
REMOTE_DIR      = "/home/ubuntu/qst"                        # where the project lands
REMOTE_USER     = "ubuntu"

# AWS
DEFAULT_REGION  = os.environ.get("AWS_REGION", "us-east-1")
INSTANCE_TYPE   = "t3.xlarge"            # 4 vCPU / 16 GB - headroom for the image builds
ROOT_VOLUME_GB  = 50                     # gp3 root, prevents "No space left on device"
KEY_NAME        = "qst-demo-key"
SG_NAME         = "qst-demo-sg"
TAG_NAME        = "qst-golden-state"
CANONICAL_OWNER = "099720109477"         # Canonical's AWS account (official Ubuntu AMIs)
UBUNTU_AMI_PATTERN = "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"

# Ports the dashboard listens on (the ONLY app port we expose in the SG)
DASHBOARD_PORT  = 3002

# Internal tools reachable ONLY through the SSH tunnel (host localhost ports).
# (port, human label) - used to build the printed `ssh -L ...` command.
TUNNEL_PORTS = [
    (3002,  "QST Dashboard"),
    (3003,  "Langfuse"),
    (16686, "Jaeger"),
    (6006,  "Phoenix"),
    (5678,  "n8n"),
    (8003,  "Agentic API (analysis)"),
]

# Docker NAMED VOLUMES that hold the frozen Golden State. These are the real
# data - NOT ./data. Each entry is (LOCAL_volume_name, REMOTE_volume_name).
# They are identical for the compose-managed volumes, but DIFFER for n8n: the
# local n8n is a standalone `docker run` on the bare `n8n_data` volume, whereas
# compose (name: trading-desk) creates/expects `trading-desk_n8n_data`. So we
# export the local volume and restore it under the compose-prefixed name.
GOLDEN_VOLUMES = [
    (f"{COMPOSE_PROJECT}_agent_memory", f"{COMPOSE_PROJECT}_agent_memory"),  # reports/ingestion/briefing/users
    (f"{COMPOSE_PROJECT}_chroma_data",  f"{COMPOSE_PROJECT}_chroma_data"),   # RAG vector store
    ("n8n_data",                        f"{COMPOSE_PROJECT}_n8n_data"),       # n8n workflows + encryption key
    (f"{COMPOSE_PROJECT}_langfuse_db",  f"{COMPOSE_PROJECT}_langfuse_db"),    # Langfuse trace history (Postgres)
    (f"{COMPOSE_PROJECT}_phoenix_data", f"{COMPOSE_PROJECT}_phoenix_data"),   # Phoenix eval/trace history
    # pg_run_store is intentionally NOT migrated: RUN_STORE_BACKEND=memory, so
    # the main Postgres holds no app data (a fresh one on EC2 is equivalent).
]

# Compose profiles to activate on the remote `up` so the FULL stack starts:
# observability/orchestration tools that are otherwise opt-in.
COMPOSE_PROFILES = "--profile langfuse --profile phoenix --profile observability --profile n8n"

# Source-sync excludes (kept out of the project tarball - never the data!)
SOURCE_EXCLUDES = [
    "node_modules", "venv", ".venv", "__pycache__", ".git", ".pytest_cache",
    "dist", "build", ".mypy_cache", ".ruff_cache", "*.log", "*.pyc",
    ".env",          # transferred separately (hardened copy)
    ".stage",        # the deploy scratch dir (volume tarballs) - never re-archive
    "*.pem",         # NEVER ship the private key inside the source bundle
    "*.tar.gz",      # exported volume archives / stale bundles
]

# The frozen-state guarantees + the working LLM tiering for the manual
# Analysis tab. These KEYS are force-overridden in the uploaded .env.
ENV_OVERRIDES = {
    "AGENTIC_SYNTHESIS_LOOP_ENABLED": "false",   # background loop stays OFF
    "INGESTION_ENABLED":              "false",   # 1-min ingestion stays OFF
    "LLM_PROVIDER_CHAIN":             "gemini,gemini_flash,github,openai",
    "RAG_LLM_PROVIDER_CHAIN":         "gemini,gemini_flash,github,openai",
    "GEMINI_MODEL":                   "gemini/gemini-3.5-flash",
}

LOCAL_STAGE = PROJECT_ROOT / "deploy" / ".stage"   # scratch dir for tarballs


# ============================================================================
# small helpers
# ============================================================================
def log(msg: str) -> None:
    print(f"\033[36m[deploy]\033[0m {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"\033[33m[warn]\033[0m  {msg}", flush=True)


def die(msg: str) -> None:
    print(f"\033[31m[fatal]\033[0m {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def run_local(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Run a local command, raising with a useful message on failure."""
    return subprocess.run(cmd, check=True, **kw)


def my_public_ip() -> str:
    """Detect this machine's public IP for the /32 security-group rules."""
    ip = urllib.request.urlopen("https://checkip.amazonaws.com", timeout=10).read().decode().strip()
    if not ip or ip.count(".") != 3:
        die(f"Could not determine public IP (got {ip!r})")
    return ip


# ============================================================================
# 0. LOCAL PREP - export Golden-State volumes + project source + hardened .env
# ============================================================================
def docker_available() -> bool:
    try:
        run_local(["docker", "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def export_golden_volumes() -> list[Path]:
    """Stream-export each named volume to a local .tar.gz (no host bind-mount,
    so it works identically on Windows / macOS / Linux)."""
    if not docker_available():
        die("Docker is not available locally - cannot export the Golden-State volumes.")
    LOCAL_STAGE.mkdir(parents=True, exist_ok=True)
    existing = subprocess.run(["docker", "volume", "ls", "--format", "{{.Name}}"],
                              capture_output=True, text=True).stdout.split()
    tarballs: list[Path] = []
    for local_vol, remote_vol in GOLDEN_VOLUMES:
        if local_vol not in existing:
            warn(f"volume {local_vol} not found locally - skipping (is the stack up?)")
            continue
        # Name the tarball after the REMOTE volume so the restore step recreates
        # it under the name compose expects (matters when local != remote, e.g. n8n).
        out = LOCAL_STAGE / f"{remote_vol}.tar.gz"
        note = "" if local_vol == remote_vol else f"  (-> remote {remote_vol})"
        log(f"exporting volume {local_vol} -> {out.name}{note}")
        # `tar ... -C /from .` streamed to stdout; we capture it into the file.
        with open(out, "wb") as fh:
            proc = subprocess.run(
                ["docker", "run", "--rm", "-v", f"{local_vol}:/from:ro", "alpine",
                 "tar", "czf", "-", "-C", "/from", "."],
                stdout=fh, stderr=subprocess.PIPE,
            )
        if proc.returncode != 0:
            die(f"failed to export {local_vol}: {proc.stderr.decode()[:300]}")
        tarballs.append(out)
    if not tarballs:
        die("No Golden-State volumes exported - refusing to deploy an empty desk.")
    return tarballs


def _is_excluded(rel: str) -> bool:
    """True if any path segment (or the whole relative path) matches an exclude
    glob - keeps node_modules/venv/__pycache__/logs/.git out of the archive."""
    parts = Path(rel).parts
    for pat in SOURCE_EXCLUDES:
        if fnmatch.fnmatch(rel, pat) or any(fnmatch.fnmatch(p, pat) for p in parts):
            return True
    return False


def build_source_tarball() -> Path:
    """Tar the repo with Python's stdlib tarfile (no dependency on a system
    `tar`, fully portable across Windows/macOS/Linux), excluding heavy dirs.
    The frozen data lives in Docker volumes, NOT here, so it is never included."""
    LOCAL_STAGE.mkdir(parents=True, exist_ok=True)
    out = LOCAL_STAGE / "qst_source.tar.gz"
    log("building project source tarball (excluding node_modules/venv/__pycache__/logs)...")
    with tarfile.open(out, "w:gz") as tar:
        for root, dirs, files in os.walk(PROJECT_ROOT):
            rel_root = os.path.relpath(root, PROJECT_ROOT)
            if rel_root == ".":
                rel_root = ""
            # prune excluded directories in-place so os.walk skips them
            dirs[:] = [d for d in dirs if not _is_excluded(os.path.join(rel_root, d))]
            for f in files:
                rel = os.path.join(rel_root, f) if rel_root else f
                if _is_excluded(rel):
                    continue
                tar.add(os.path.join(root, f), arcname=rel)
    log(f"source tarball ready ({out.stat().st_size/1e6:.1f} MB)")
    return out


def harden_env() -> Path:
    """Copy local .env, force the frozen-state flags + LLM tiering, write to stage.
    Secrets are preserved verbatim; only the control keys are overridden."""
    src = PROJECT_ROOT / ".env"
    if not src.exists():
        die(".env not found at repo root - cannot deploy without it.")
    lines = src.read_text(encoding="utf-8").splitlines()
    seen = set()
    out_lines = []
    for ln in lines:
        if "=" in ln and not ln.lstrip().startswith("#"):
            key = ln.split("=", 1)[0].strip()
            if key in ENV_OVERRIDES:
                out_lines.append(f"{key}={ENV_OVERRIDES[key]}")
                seen.add(key)
                continue
        out_lines.append(ln)
    # append any override that wasn't already present
    for key, val in ENV_OVERRIDES.items():
        if key not in seen:
            out_lines.append(f"{key}={val}")
    out = LOCAL_STAGE / ".env.remote"
    out.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    log("hardened .env prepared (loop OFF, ingestion OFF, Gemini 3.5-flash primary)")
    return out


# ============================================================================
# 1. AWS PROVISIONING
# ============================================================================
def ensure_key_pair(ec2) -> Path:
    """Create the key pair if absent; return the local .pem path (0600)."""
    pem = PROJECT_ROOT / "deploy" / f"{KEY_NAME}.pem"
    try:
        ec2.describe_key_pairs(KeyNames=[KEY_NAME])
        if pem.exists():
            log(f"reusing key pair {KEY_NAME} ({pem})")
            return pem
        die(f"Key pair {KEY_NAME} exists in AWS but {pem} is missing locally. "
            f"Delete the AWS key pair or restore the .pem, then re-run.")
    except ClientError as e:
        if "InvalidKeyPair.NotFound" not in str(e):
            raise
    log(f"creating key pair {KEY_NAME}")
    resp = ec2.create_key_pair(KeyName=KEY_NAME)
    pem.write_text(resp["KeyMaterial"], encoding="utf-8")
    try:
        os.chmod(pem, stat.S_IRUSR | stat.S_IWUSR)  # 0600 (best-effort on Windows)
    except Exception:
        pass
    log(f"private key saved -> {pem}")
    return pem


def ensure_security_group(ec2, my_ip: str, open_http: bool) -> str:
    """Create/refresh a strict SG: SSH+dashboard from your IP only, nothing else."""
    cidr_me = f"{my_ip}/32"
    try:
        sg_id = ec2.create_security_group(
            GroupName=SG_NAME,
            Description="QST demo - SSH + dashboard from a single IP; tunnel for the rest",
        )["GroupId"]
        log(f"created security group {SG_NAME} ({sg_id})")
    except ClientError as e:
        if "InvalidGroup.Duplicate" not in str(e):
            raise
        sg_id = ec2.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [SG_NAME]}]
        )["SecurityGroups"][0]["GroupId"]
        log(f"reusing security group {SG_NAME} ({sg_id})")

    http_cidr = "0.0.0.0/0" if open_http else cidr_me
    rules = [
        # SSH - always locked to your IP. This is also the tunnel entry point.
        {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
         "IpRanges": [{"CidrIp": cidr_me, "Description": "SSH from my IP"}]},
        # Dashboard - your IP by default (or 0.0.0.0/0 with --open-http).
        {"IpProtocol": "tcp", "FromPort": DASHBOARD_PORT, "ToPort": DASHBOARD_PORT,
         "IpRanges": [{"CidrIp": http_cidr, "Description": "QST dashboard"}]},
    ]
    # NOTE: we intentionally add NO rules for 8000-8004 / 3003 / 16686 / 6006 /
    # 5678. Those stay internet-unreachable and are accessed via the SSH tunnel.
    for rule in rules:
        try:
            ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[rule])
        except ClientError as e:
            if "InvalidPermission.Duplicate" not in str(e):
                raise
    log(f"ingress: tcp/22 <- {cidr_me} | tcp/{DASHBOARD_PORT} <- {http_cidr} | (internal ports closed)")
    return sg_id


def latest_ubuntu_ami(ec2) -> tuple[str, str]:
    """Resolve the newest official Ubuntu 22.04 amd64 AMI + its root device name."""
    imgs = ec2.describe_images(
        Owners=[CANONICAL_OWNER],
        Filters=[
            {"Name": "name", "Values": [UBUNTU_AMI_PATTERN]},
            {"Name": "state", "Values": ["available"]},
            {"Name": "architecture", "Values": ["x86_64"]},
            {"Name": "root-device-type", "Values": ["ebs"]},
        ],
    )["Images"]
    if not imgs:
        die("No Ubuntu 22.04 AMI found in this region.")
    newest = sorted(imgs, key=lambda i: i["CreationDate"])[-1]
    root_dev = newest.get("RootDeviceName", "/dev/sda1")
    log(f"AMI {newest['ImageId']} ({newest['Name']}), root device {root_dev}")
    return newest["ImageId"], root_dev


def launch_instance(ec2, ami: str, root_dev: str, sg_id: str, instance_type: str) -> dict:
    """Launch the EC2 instance with a 50 GB gp3 root volume."""
    log(f"launching {instance_type} ...")
    resp = ec2.run_instances(
        ImageId=ami,
        InstanceType=instance_type,
        KeyName=KEY_NAME,
        MaxCount=1, MinCount=1,
        SecurityGroupIds=[sg_id],
        # -- EBS: 50 GB gp3 root volume (prevents "No space left on device"
        #    during the multi-service image build) --------------------------
        BlockDeviceMappings=[{
            "DeviceName": root_dev,
            "Ebs": {
                "VolumeSize": ROOT_VOLUME_GB,
                "VolumeType": "gp3",
                "DeleteOnTermination": True,
            },
        }],
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": TAG_NAME},
                     {"Key": "project", "Value": "qst"}],
        }],
    )
    iid = resp["Instances"][0]["InstanceId"]
    log(f"instance {iid} launching - waiting for running + status checks...")
    ec2.get_waiter("instance_running").wait(InstanceIds=[iid])
    ec2.get_waiter("instance_status_ok").wait(InstanceIds=[iid])
    desc = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
    ip = desc["PublicIpAddress"]
    log(f"instance up: {iid} @ {ip}")
    return {"id": iid, "ip": ip}


# ============================================================================
# 2. SSH / SFTP
# ============================================================================
def _load_key(pem: Path):
    """Load the private key, trying each algorithm (AWS default is RSA)."""
    for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
        try:
            return cls.from_private_key_file(str(pem))
        except paramiko.SSHException:
            continue
    die(f"Could not parse private key {pem}")


def ssh_connect(ip: str, pem: Path, retries: int = 30, delay: int = 10) -> paramiko.SSHClient:
    """Wait for sshd, then return a connected client."""
    key = _load_key(pem)
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    for i in range(1, retries + 1):
        try:
            cli.connect(ip, username=REMOTE_USER, pkey=key, timeout=15, banner_timeout=15)
            log("SSH connected")
            return cli
        except Exception as e:
            log(f"  waiting for SSH ({i}/{retries})... {type(e).__name__}")
            time.sleep(delay)
    die("SSH never became reachable.")


def run_remote(cli: paramiko.SSHClient, cmd: str, sudo: bool = False, check: bool = True) -> str:
    """Execute a remote command, streaming output; raise on non-zero exit.

    The command (which may be multi-line) is base64-encoded and decoded on the
    far side - this avoids ALL shell-quoting pitfalls (newlines, quotes, $...)
    that would otherwise mangle multi-line scripts like the Docker installer."""
    b64 = base64.b64encode(cmd.encode()).decode()
    prefix = "sudo " if sudo else ""
    wrapped = f"echo {b64} | base64 -d | {prefix}bash -l"
    stdout = cli.exec_command(wrapped, get_pty=True)[1]
    out_lines = []
    for line in iter(stdout.readline, ""):
        out_lines.append(line)
        print(f"   | {line.rstrip()}")
    rc = stdout.channel.recv_exit_status()
    if check and rc != 0:
        die(f"remote command failed (rc={rc}). Last output:\n" + "".join(out_lines[-15:]))
    return "".join(out_lines)


def sftp_put(cli: paramiko.SSHClient, local: Path, remote: str) -> None:
    size = local.stat().st_size
    log(f"upload {local.name} ({size/1e6:.1f} MB) -> {remote}")
    sftp = cli.open_sftp()
    last = [0]
    def cb(done, total):
        pct = int(done * 100 / total) if total else 100
        if pct >= last[0] + 10:
            last[0] = pct
            print(f"   | {pct}%")
    sftp.put(str(local), remote, callback=cb)
    sftp.close()


# ============================================================================
# 3. REMOTE PROVISIONING + LAUNCH
# ============================================================================
DOCKER_INSTALL = r"""
set -e
if ! command -v docker >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  usermod -aG docker ubuntu
fi
docker --version
docker compose version
"""


def remote_provision_and_run(cli: paramiko.SSHClient, vol_tarballs: list[Path]) -> None:
    # 3a. Docker + Compose v2
    log("installing Docker + Compose on the instance...")
    run_remote(cli, DOCKER_INSTALL, sudo=True)

    # 3b. lay out the project dir
    run_remote(cli, f"mkdir -p {REMOTE_DIR} {REMOTE_DIR}/_volumes")

    # 3c. unpack source
    log("extracting project source on the instance...")
    run_remote(cli, f"tar xzf {REMOTE_DIR}/qst_source.tar.gz -C {REMOTE_DIR}")
    run_remote(cli, f"mv -f {REMOTE_DIR}/.env.remote {REMOTE_DIR}/.env")

    # 3d. RESTORE the Golden-State volumes BEFORE compose runs, so `up` reuses
    #     the pre-populated, identically-named volumes instead of empty ones.
    for tb in vol_tarballs:
        vol = tb.name[:-len(".tar.gz")]            # e.g. trading-desk_agent_memory
        log(f"restoring Golden-State volume {vol}")
        run_remote(cli, f"docker volume create {vol}", sudo=True)
        run_remote(
            cli,
            f"cat {REMOTE_DIR}/_volumes/{tb.name} | "
            f"docker run --rm -i -v {vol}:/to alpine tar xzf - -C /to",
            sudo=True,
        )

    # 3e. build + start the FULL stack (all profiles: langfuse/phoenix/
    #     observability/n8n). Frozen: synthesis loop + ingestion stay OFF via the
    #     hardened .env; the profiles only add observability + orchestration UIs.
    log("docker compose up -d --build with all profiles (~15-30 min build)...")
    run_remote(cli, f"cd {REMOTE_DIR} && docker compose {COMPOSE_PROFILES} up -d --build", sudo=True)

    # 3f. health check (include profiles so `ps` lists every service)
    log("verifying container health...")
    run_remote(cli, f"cd {REMOTE_DIR} && docker compose {COMPOSE_PROFILES} ps", sudo=True)
    # give services a moment, then probe the agentic API + dashboard on the host
    run_remote(cli, "sleep 20", sudo=True, check=False)
    run_remote(cli,
               "for p in 8003 8001 8002 8004; do "
               "printf 'port %s -> ' $p; "
               "curl -s -o /dev/null -w '%{http_code}\\n' http://localhost:$p/health "
               "|| echo unreachable; done", sudo=True, check=False)
    run_remote(cli,
               f"printf 'dashboard {DASHBOARD_PORT} -> '; "
               f"curl -s -o /dev/null -w '%{{http_code}}\\n' http://localhost:{DASHBOARD_PORT}/",
               sudo=True, check=False)


# ============================================================================
# teardown
# ============================================================================
def teardown(ec2) -> None:
    log("TEARDOWN: terminating instance(s), deleting SG + key pair...")
    res = ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": [TAG_NAME]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
    ])["Reservations"]
    ids = [i["InstanceId"] for r in res for i in r["Instances"]]
    if ids:
        ec2.terminate_instances(InstanceIds=ids)
        log(f"terminating {ids} ... waiting")
        ec2.get_waiter("instance_terminated").wait(InstanceIds=ids)
    for fn in (
        lambda: ec2.delete_security_group(GroupName=SG_NAME),
        lambda: ec2.delete_key_pair(KeyName=KEY_NAME),
    ):
        try:
            fn()
        except ClientError as e:
            warn(str(e)[:160])
    log("teardown complete.")


# ============================================================================
# final access banner
# ============================================================================
def print_access(ip: str, pem: Path, open_http: bool) -> None:
    L = " ".join(f"-L {p}:localhost:{p}" for p, _ in TUNNEL_PORTS)
    tunnel = f"ssh -i {pem} {L} {REMOTE_USER}@{ip}"
    bar = "=" * 74
    print(f"\n\033[32m{bar}\n  [OK] QST GOLDEN STATE DEPLOYED - {ip}\n{bar}\033[0m")
    print("\n  Dashboard:")
    if open_http:
        print(f"    http://{ip}:{DASHBOARD_PORT}/         (open to the world - --open-http)")
    else:
        print(f"    http://{ip}:{DASHBOARD_PORT}/         (your IP only; or via the tunnel below)")
    print("\n  [TUNNEL] SSH tunnel for the internal tools (Local Port Forwarding):")
    print(f"    {tunnel}\n")
    print("  With the tunnel open, browse on YOUR machine:")
    for p, label in TUNNEL_PORTS:
        print(f"    http://localhost:{p:<5}  ->  {label}")
    print("\n  Frozen-state reminder: synthesis loop + ingestion are OFF. The Live")
    print("  Desk serves the static Golden-Run data; Gemini fires only when you")
    print("  hit the Analysis tab (primary: gemini-3.5-flash).")
    print(f"\n  Teardown when done:  python {Path(__file__).name} --teardown\n")


# ============================================================================
# main
# ============================================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="Deploy QST (frozen Golden State) to AWS EC2.")
    ap.add_argument("--region", default=DEFAULT_REGION)
    ap.add_argument("--instance-type", default=INSTANCE_TYPE, help="t3.large or t3.xlarge")
    ap.add_argument("--open-http", action="store_true",
                    help="expose dashboard 3002 to 0.0.0.0/0 (default: your IP only)")
    ap.add_argument("--teardown", action="store_true", help="delete all AWS resources and exit")
    args = ap.parse_args()

    ec2 = boto3.client("ec2", region_name=args.region)

    if args.teardown:
        teardown(ec2)
        return

    # -- 0. LOCAL PREP -------------------------------------------------------
    my_ip = my_public_ip()
    log(f"your public IP (for the /32 SG rules): {my_ip}")
    vol_tarballs = export_golden_volumes()      # the REAL Golden State (named volumes)
    source_tar   = build_source_tarball()
    env_file     = harden_env()

    # -- 1. PROVISION --------------------------------------------------------
    pem   = ensure_key_pair(ec2)
    sg_id = ensure_security_group(ec2, my_ip, args.open_http)
    ami, root_dev = latest_ubuntu_ami(ec2)
    inst  = launch_instance(ec2, ami, root_dev, sg_id, args.instance_type)

    # -- 2. CONNECT + UPLOAD -------------------------------------------------
    cli = ssh_connect(inst["ip"], pem)
    try:
        run_remote(cli, f"mkdir -p {REMOTE_DIR} {REMOTE_DIR}/_volumes")
        sftp_put(cli, source_tar, f"{REMOTE_DIR}/qst_source.tar.gz")
        sftp_put(cli, env_file,   f"{REMOTE_DIR}/.env.remote")
        for tb in vol_tarballs:
            sftp_put(cli, tb, f"{REMOTE_DIR}/_volumes/{tb.name}")

        # -- 3. PROVISION + RUN (frozen) -------------------------------------
        remote_provision_and_run(cli, vol_tarballs)
    finally:
        cli.close()

    # -- 4. ACCESS -----------------------------------------------------------
    print_access(inst["ip"], pem, args.open_http)


if __name__ == "__main__":
    main()
