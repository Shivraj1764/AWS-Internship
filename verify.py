"""
Comprehensive Verification and Audit Script for EWB VPC Peering project.
Performs an automated architectural audit of all AWS resources, route tables,
peering states, security group rules, and EC2 instance configurations.
"""

import sys

# Configure UTF-8 encoding on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

import config
from utils import get_boto3_session

console = Console(safe_box=True)

class EWBVerifier:
    def __init__(self):
        self.session = get_boto3_session()
        self.ec2 = self.session.client("ec2", region_name=config.REGION)
        self.results = []

    def check(self, category, item, status, details=""):
        self.results.append({
            "category": category,
            "item": item,
            "status": status,
            "details": details
        })

    def run_checks(self):
        console.print("[bold cyan]Starting Architectural Audit against Specs...[/bold cyan]\n")

        # 1. Audit VPCs
        vpcs = self.ec2.describe_vpcs()["Vpcs"]
        vpc_map = {}
        for v in vpcs:
            for tag in v.get("Tags", []):
                if tag["Key"] == "Name":
                    vpc_map[tag["Value"]] = v

        for expected_vpc, cidr in [("ewb-vpc-a", "10.10.0.0/16"), ("ewb-vpc-b", "10.20.0.0/16")]:
            if expected_vpc in vpc_map:
                v = vpc_map[expected_vpc]
                if v["CidrBlock"] == cidr and v["State"] == "available":
                    self.check("VPC", expected_vpc, "PASS", f"ID: {v['VpcId']}, CIDR: {v['CidrBlock']}")
                else:
                    self.check("VPC", expected_vpc, "FAIL", f"Found with incorrect CIDR {v['CidrBlock']} or state {v['State']}")
            else:
                self.check("VPC", expected_vpc, "FAIL", "VPC not found")

        # 2. Audit Subnets
        subnets = self.ec2.describe_subnets()["Subnets"]
        subnet_map = {}
        for s in subnets:
            for tag in s.get("Tags", []):
                if tag["Key"] == "Name":
                    subnet_map[tag["Value"]] = s

        expected_subnets = [
            ("ewb-a-public", "10.10.1.0/24", "ewb-vpc-a", True),
            ("ewb-a-private", "10.10.2.0/24", "ewb-vpc-a", False),
            ("ewb-b-private", "10.20.1.0/24", "ewb-vpc-b", False)
        ]
        for name, cidr, expected_vpc_name, pub_ip in expected_subnets:
            if name in subnet_map:
                s = subnet_map[name]
                expected_vpc_id = vpc_map.get(expected_vpc_name, {}).get("VpcId")
                vpc_match = s["VpcId"] == expected_vpc_id
                cidr_match = s["CidrBlock"] == cidr
                if vpc_match and cidr_match:
                    self.check("Subnet", name, "PASS", f"ID: {s['SubnetId']}, CIDR: {s['CidrBlock']}, PublicIPOnLaunch: {s['MapPublicIpOnLaunch']}")
                else:
                    self.check("Subnet", name, "FAIL", f"Mismatch: VPC {s['VpcId']} vs {expected_vpc_id}, CIDR {s['CidrBlock']} vs {cidr}")
            else:
                self.check("Subnet", name, "FAIL", "Subnet not found")

        # 3. Audit Internet Gateway
        igws = self.ec2.describe_internet_gateways()["InternetGateways"]
        igw_a = None
        for igw in igws:
            for tag in igw.get("Tags", []):
                if tag["Key"] == "Name" and tag["Value"] == "ewb-igw-a":
                    igw_a = igw

        if igw_a:
            vpc_a_id = vpc_map.get("ewb-vpc-a", {}).get("VpcId")
            attached = any(a.get("VpcId") == vpc_a_id for a in igw_a.get("Attachments", []))
            if attached:
                self.check("IGW", "ewb-igw-a", "PASS", f"ID: {igw_a['InternetGatewayId']} attached to ewb-vpc-a")
            else:
                self.check("IGW", "ewb-igw-a", "FAIL", "IGW not attached to ewb-vpc-a")
        else:
            self.check("IGW", "ewb-igw-a", "FAIL", "IGW not found")

        # 4. Audit Peering Connection
        peering_conns = self.ec2.describe_vpc_peering_connections()["VpcPeeringConnections"]
        target_pcx = None
        for pcx in peering_conns:
            if pcx["Status"]["Code"] not in ["deleted", "rejected"]:
                for tag in pcx.get("Tags", []):
                    if tag["Key"] == "Name" and tag["Value"] == "ewb-a-b-peer":
                        target_pcx = pcx

        if target_pcx and target_pcx["Status"]["Code"] == "active":
            self.check("Peering", "ewb-a-b-peer", "PASS", f"ID: {target_pcx['VpcPeeringConnectionId']}, Status: ACTIVE")
            pcx_id = target_pcx["VpcPeeringConnectionId"]
        else:
            status = target_pcx["Status"]["Code"] if target_pcx else "NOT FOUND"
            self.check("Peering", "ewb-a-b-peer", "FAIL", f"Status: {status}")
            pcx_id = None

        # 5. Audit Route Tables
        rts = self.ec2.describe_route_tables()["RouteTables"]
        rt_map = {}
        for rt in rts:
            for tag in rt.get("Tags", []):
                if tag["Key"] == "Name":
                    rt_map[tag["Value"]] = rt

        # ewb-rt-a-public: 0.0.0.0/0 -> IGW, 10.20.0.0/16 -> PCX
        if "ewb-rt-a-public" in rt_map:
            rt = rt_map["ewb-rt-a-public"]
            routes = {r.get("DestinationCidrBlock"): r for r in rt.get("Routes", [])}
            has_igw = "0.0.0.0/0" in routes and routes["0.0.0.0/0"].get("GatewayId", "").startswith("igw-")
            has_peer = "10.20.0.0/16" in routes and routes["10.20.0.0/16"].get("VpcPeeringConnectionId") == pcx_id
            if has_igw and has_peer:
                self.check("Route Table", "ewb-rt-a-public", "PASS", f"Contains 0.0.0.0/0 -> IGW and 10.20.0.0/16 -> {pcx_id}")
            else:
                self.check("Route Table", "ewb-rt-a-public", "FAIL", f"Missing routes: IGW({has_igw}), Peering({has_peer})")
        else:
            self.check("Route Table", "ewb-rt-a-public", "FAIL", "Route Table not found")

        # ewb-rt-a-private: 10.20.0.0/16 -> PCX
        if "ewb-rt-a-private" in rt_map:
            rt = rt_map["ewb-rt-a-private"]
            routes = {r.get("DestinationCidrBlock"): r for r in rt.get("Routes", [])}
            has_peer = "10.20.0.0/16" in routes and routes["10.20.0.0/16"].get("VpcPeeringConnectionId") == pcx_id
            if has_peer:
                self.check("Route Table", "ewb-rt-a-private", "PASS", f"Contains 10.20.0.0/16 -> {pcx_id}")
            else:
                self.check("Route Table", "ewb-rt-a-private", "FAIL", f"Missing 10.20.0.0/16 peering route")
        else:
            self.check("Route Table", "ewb-rt-a-private", "FAIL", "Route Table not found")

        # ewb-rt-b-private: 10.10.0.0/16 -> PCX
        if "ewb-rt-b-private" in rt_map:
            rt = rt_map["ewb-rt-b-private"]
            routes = {r.get("DestinationCidrBlock"): r for r in rt.get("Routes", [])}
            has_peer = "10.10.0.0/16" in routes and routes["10.10.0.0/16"].get("VpcPeeringConnectionId") == pcx_id
            if has_peer:
                self.check("Route Table", "ewb-rt-b-private", "PASS", f"Contains 10.10.0.0/16 -> {pcx_id}")
            else:
                self.check("Route Table", "ewb-rt-b-private", "FAIL", f"Missing 10.10.0.0/16 peering route")
        else:
            self.check("Route Table", "ewb-rt-b-private", "FAIL", "Route Table not found")

        # 6. Audit Security Groups
        sgs = self.ec2.describe_security_groups()["SecurityGroups"]
        sg_map = {s["GroupName"]: s for s in sgs}

        for sg_name in ["ewb-sg-bastion", "ewb-sg-app-a", "ewb-sg-app-b"]:
            if sg_name in sg_map:
                sg = sg_map[sg_name]
                perms = sg.get("IpPermissions", [])
                self.check("Security Group", sg_name, "PASS", f"ID: {sg['GroupId']} with {len(perms)} ingress rule sets")
            else:
                self.check("Security Group", sg_name, "FAIL", "Security Group not found")

        # 7. Audit EC2 Instances
        instances_res = self.ec2.describe_instances(
            Filters=[
                {"Name": "tag:Name", "Values": ["ewb-bastion", "ewb-app-a", "ewb-app-b"]},
                {"Name": "instance-state-name", "Values": ["pending", "running", "stopped"]}
            ]
        )
        found_instances = {}
        for r in instances_res.get("Reservations", []):
            for inst in r.get("Instances", []):
                for tag in inst.get("Tags", []):
                    if tag["Key"] == "Name":
                        found_instances[tag["Value"]] = inst

        expected_insts = [
            ("ewb-bastion", "10.10.1.10", True),
            ("ewb-app-a", "10.10.2.10", False),
            ("ewb-app-b", "10.20.1.10", False)
        ]
        for name, expected_ip, requires_pub in expected_insts:
            if name in found_instances:
                inst = found_instances[name]
                state = inst["State"]["Name"]
                actual_ip = inst.get("PrivateIpAddress")
                pub_ip = inst.get("PublicIpAddress")
                ip_match = actual_ip == expected_ip
                pub_ok = (pub_ip is not None) if requires_pub else (pub_ip is None)
                if state == "running" and ip_match and pub_ok:
                    self.check("EC2 Instance", name, "PASS", f"ID: {inst['InstanceId']} | State: running | PrivIP: {actual_ip} | PubIP: {pub_ip or 'None'}")
                else:
                    self.check("EC2 Instance", name, "FAIL", f"State: {state}, PrivIP: {actual_ip} (expected {expected_ip}), PubIP: {pub_ip}")
            else:
                self.check("EC2 Instance", name, "FAIL", "Instance not found")

        # Print Audit Results Table
        table = Table(title="EWB VPC Peering Audit & Verification Report", show_header=True, header_style="bold magenta")
        table.add_column("Category", style="cyan", width=18)
        table.add_column("Specification Item", style="yellow", width=20)
        table.add_column("Status", width=10)
        table.add_column("Audit Findings / Details", style="white")

        all_passed = True
        for res in self.results:
            status_style = "[bold green]PASS[/bold green]" if res["status"] == "PASS" else "[bold red]FAIL[/bold red]"
            if res["status"] != "PASS":
                all_passed = False
            table.add_row(res["category"], res["item"], status_style, res["details"])

        console.print(table)
        console.print("\n")
        if all_passed:
            console.print(Panel("[bold green][OK] ALL ARCHITECTURAL SPECIFICATIONS VERIFIED AND MATCH 100%![/bold green]", border_style="green"))
        else:
            console.print(Panel("[bold red][!] Some architectural checks failed. Review the table above.[/bold red]", border_style="red"))

if __name__ == "__main__":
    try:
        verifier = EWBVerifier()
        verifier.run_checks()
    except Exception as e:
        console.print(f"[bold red]Verification encountered an error:[/bold red] {e}")
        sys.exit(1)
