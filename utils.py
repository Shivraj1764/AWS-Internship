"""
Utility helpers for AWS Boto3 operations, credential validation,
IP detection, AMI resolution, and key-pair management.
"""

import os
import sys
import stat
import urllib.request
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# Configure UTF-8 encoding on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config

console = Console(safe_box=True)

def get_boto3_session():
    """
    Initializes a Boto3 Session using credentials from environment variables,
    .env, or ~/.aws/credentials. Validates credentials via STS.
    """
    try:
        session = boto3.Session(region_name=config.REGION)
        sts = session.client("sts")
        caller = sts.get_caller_identity()
        console.print(f"[bold green][OK] AWS Credentials Authenticated[/bold green]: Account [cyan]{caller['Account']}[/cyan], ARN [yellow]{caller['Arn']}[/yellow], Region [magenta]{config.REGION}[/magenta]")
        return session
    except NoCredentialsError:
        console.print(Panel(
            "[bold red]AWS Credentials Not Found![/bold red]\n\n"
            "Please configure your credentials using one of the following methods:\n"
            "1. Create/edit the [cyan].env[/cyan] file in this directory with:\n"
            "   [yellow]AWS_ACCESS_KEY_ID=...\n   AWS_SECRET_ACCESS_KEY=...\n   AWS_DEFAULT_REGION=ap-southeast-2[/yellow]\n"
            "2. Set standard environment variables in your terminal.\n"
            "3. Run [yellow]aws configure[/yellow] if AWS CLI is installed.",
            title="Authentication Error",
            border_style="red"
        ))
        raise
    except ClientError as e:
        console.print(f"[bold red]AWS Client Error during authentication: {e}[/bold red]")
        raise

def get_my_public_ip():
    """
    Determines caller's current public IP address for secure Bastion ingress.
    Supports manual override via BASTION_INGRESS_CIDR environment variable.
    """
    override = os.getenv("BASTION_INGRESS_CIDR")
    if override:
        console.print(f"[bold blue][i] Using Bastion Ingress CIDR Override:[/bold blue] [cyan]{override}[/cyan]")
        return override

    urls = [
        "https://checkip.amazonaws.com",
        "https://api.ipify.org",
        "https://ifconfig.me/ip"
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as response:
                ip = response.read().decode("utf-8").strip()
                if ip:
                    cidr = f"{ip}/32"
                    console.print(f"[bold green][OK] Detected Public IP for Bastion Ingress:[/bold green] [cyan]{cidr}[/cyan]")
                    return cidr
        except Exception:
            continue

    console.print("[yellow][!] Could not automatically detect public IP. Defaulting to 0.0.0.0/0[/yellow]")
    return "0.0.0.0/0"

def get_latest_al2023_ami(session):
    """
    Retrieves the latest Amazon Linux 2023 x86_64 AMI ID using AWS SSM Parameter Store.
    Falls back to ec2.describe_images if SSM parameter is unavailable.
    """
    ssm = session.client("ssm", region_name=config.REGION)
    try:
        response = ssm.get_parameter(Name=config.AL2023_SSM_PARAM)
        ami_id = response["Parameter"]["Value"]
        console.print(f"[bold green][OK] Resolved Amazon Linux 2023 AMI via SSM:[/bold green] [cyan]{ami_id}[/cyan]")
        return ami_id
    except Exception as e:
        console.print(f"[yellow]SSM lookup failed ({e}), falling back to EC2 describe_images...[/yellow]")
        ec2 = session.client("ec2", region_name=config.REGION)
        images = ec2.describe_images(
            Owners=["amazon"],
            Filters=[
                {"Name": "name", "Values": ["al2023-ami-2023.*-x86_64"]},
                {"Name": "state", "Values": ["available"]}
            ]
        )["Images"]
        images.sort(key=lambda x: x["CreationDate"], reverse=True)
        if images:
            ami_id = images[0]["ImageId"]
            console.print(f"[bold green][OK] Resolved Amazon Linux 2023 AMI via EC2 search:[/bold green] [cyan]{ami_id}[/cyan] ({images[0]['Name']})")
            return ami_id
        raise RuntimeError("Could not resolve any valid Amazon Linux 2023 AMI")

def ensure_key_pair(ec2_client):
    """
    Idempotently checks if the key pair exists in AWS.
    If not, creates it and saves ewb-lab.pem locally with read-only permissions.
    """
    key_name = config.KEY_PAIR_NAME
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.KEY_FILE_NAME)

    try:
        ec2_client.describe_key_pairs(KeyNames=[key_name])
        console.print(f"[bold green][OK] Key Pair [cyan]{key_name}[/cyan] already exists in AWS EC2.[/bold green]")
        if not os.path.exists(key_file):
            console.print(f"[yellow][!] Warning: Local key file '{config.KEY_FILE_NAME}' not found on disk. If needed for SSH, ensure you have the private key.[/yellow]")
        return key_name
    except ClientError as e:
        if "InvalidKeyPair.NotFound" not in str(e):
            raise

    # Create new key pair
    console.print(f"[cyan]Creating new EC2 Key Pair [bold]{key_name}[/bold]...[/cyan]")
    response = ec2_client.create_key_pair(
        KeyName=key_name,
        KeyType="rsa",
        KeyFormat="pem",
        TagSpecifications=[{
            "ResourceType": "key-pair",
            "Tags": [{"Key": "Name", "Value": key_name}] + config.COMMON_TAGS
        }]
    )

    key_material = response["KeyMaterial"]
    with open(key_file, "w", encoding="utf-8") as f:
        f.write(key_material)

    try:
        os.chmod(key_file, stat.S_IRUSR)
    except Exception:
        pass

    console.print(f"[bold green][OK] Key Pair created and private key saved to: [cyan]{key_file}[/cyan][/bold green]")
    return key_name

def print_summary_table(title, data):
    """
    Prints a rich summary table for clear visibility.
    """
    table = Table(title=title, show_header=True, header_style="bold magenta")
    table.add_column("Resource Type", style="cyan", width=22)
    table.add_column("Resource Name / Key", style="yellow", width=24)
    table.add_column("ID / Details", style="green")

    for row in data:
        table.add_row(*row)

    console.print(table)
