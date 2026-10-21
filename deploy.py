"""
Main idempotent deployment script for EWB VPC Peering project.
Provisions VPCs, Subnets, Gateways, Route Tables, Security Groups,
EC2 Instances (AL2023 t3.micro), VPC Peering Connection, and Peering Routes.
"""

import sys
import time

# Configure UTF-8 encoding on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from botocore.exceptions import ClientError
from rich.console import Console
from rich.panel import Panel

import config
from utils import (
    get_boto3_session,
    get_my_public_ip,
    get_latest_al2023_ami,
    ensure_key_pair,
    print_summary_table
)

console = Console(safe_box=True)

class EWBDeployer:
    def __init__(self):
        self.session = get_boto3_session()
        self.ec2 = self.session.client("ec2", region_name=config.REGION)
        self.resources = {
            "vpcs": {},
            "subnets": {},
            "igws": {},
            "route_tables": {},
            "security_groups": {},
            "peering": {},
            "instances": {}
        }

    def _tag_spec(self, resource_type, name):
        tags = [{"Key": "Name", "Value": name}] + config.COMMON_TAGS
        return [{"ResourceType": resource_type, "Tags": tags}]

    # -------------------------------------------------------------
    # 1. VPC Management
    # -------------------------------------------------------------
    def ensure_vpc(self, name, cidr):
        console.print(f"[bold cyan][>] Checking VPC [yellow]{name}[/yellow] ({cidr})...[/bold cyan]")
        response = self.ec2.describe_vpcs(
            Filters=[
                {"Name": "tag:Name", "Values": [name]},
                {"Name": "cidr-block-association.cidr-block", "Values": [cidr]}
            ]
        )
        vpcs = [v for v in response.get("Vpcs", []) if v["State"] != "deleted"]
        if vpcs:
            vpc_id = vpcs[0]["VpcId"]
            console.print(f"  [green][OK] Existing VPC found:[/green] [cyan]{vpc_id}[/cyan]")
        else:
            console.print(f"  [yellow]Creating VPC {name}...[/yellow]")
            vpc = self.ec2.create_vpc(
                CidrBlock=cidr,
                TagSpecifications=self._tag_spec("vpc", name)
            )["Vpc"]
            vpc_id = vpc["VpcId"]
            waiter = self.ec2.get_waiter("vpc_available")
            waiter.wait(VpcIds=[vpc_id])
            console.print(f"  [bold green][OK] Created VPC:[/bold green] [cyan]{vpc_id}[/cyan]")

        # Enable DNS support and DNS hostnames
        self.ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
        self.ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
        self.resources["vpcs"][name] = vpc_id
        return vpc_id

    # -------------------------------------------------------------
    # 2. Subnet Management
    # -------------------------------------------------------------
    def ensure_subnet(self, name, cidr, vpc_id, map_public_ip=False):
        console.print(f"[bold cyan][>] Checking Subnet [yellow]{name}[/yellow] ({cidr})...[/bold cyan]")
        response = self.ec2.describe_subnets(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "cidr-block", "Values": [cidr]},
                {"Name": "tag:Name", "Values": [name]}
            ]
        )
        subnets = response.get("Subnets", [])
        if subnets:
            subnet_id = subnets[0]["SubnetId"]
            console.print(f"  [green][OK] Existing Subnet found:[/green] [cyan]{subnet_id}[/cyan]")
        else:
            console.print(f"  [yellow]Creating Subnet {name}...[/yellow]")
            subnet = self.ec2.create_subnet(
                VpcId=vpc_id,
                CidrBlock=cidr,
                TagSpecifications=self._tag_spec("subnet", name)
            )["Subnet"]
            subnet_id = subnet["SubnetId"]
            console.print(f"  [bold green][OK] Created Subnet:[/bold green] [cyan]{subnet_id}[/cyan]")

        # Update MapPublicIpOnLaunch setting
        self.ec2.modify_subnet_attribute(
            SubnetId=subnet_id,
            MapPublicIpOnLaunch={"Value": map_public_ip}
        )
        self.resources["subnets"][name] = subnet_id
        return subnet_id

    # -------------------------------------------------------------
    # 3. Internet Gateway Management
    # -------------------------------------------------------------
    def ensure_igw(self, name, vpc_id):
        console.print(f"[bold cyan][>] Checking Internet Gateway [yellow]{name}[/yellow]...[/bold cyan]")
        response = self.ec2.describe_internet_gateways(
            Filters=[{"Name": "tag:Name", "Values": [name]}]
        )
        igws = response.get("InternetGateways", [])
        if igws:
            igw_id = igws[0]["InternetGatewayId"]
            console.print(f"  [green][OK] Existing IGW found:[/green] [cyan]{igw_id}[/cyan]")
            attachments = igws[0].get("Attachments", [])
            attached_vpcs = [a["VpcId"] for a in attachments if a["State"] == "available"]
            if vpc_id not in attached_vpcs:
                console.print(f"  [yellow]Attaching IGW {igw_id} to VPC {vpc_id}...[/yellow]")
                self.ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
                console.print(f"  [bold green][OK] IGW attached to VPC {vpc_id}[/bold green]")
        else:
            console.print(f"  [yellow]Creating IGW {name}...[/yellow]")
            igw = self.ec2.create_internet_gateway(
                TagSpecifications=self._tag_spec("internet-gateway", name)
            )["InternetGateway"]
            igw_id = igw["InternetGatewayId"]
            self.ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
            console.print(f"  [bold green][OK] Created and attached IGW:[/bold green] [cyan]{igw_id}[/cyan]")

        self.resources["igws"][name] = igw_id
        return igw_id

    # -------------------------------------------------------------
    # 4. Route Table Management
    # -------------------------------------------------------------
    def ensure_route_table(self, name, vpc_id, subnet_id, has_igw_route=False, igw_id=None):
        console.print(f"[bold cyan][>] Checking Route Table [yellow]{name}[/yellow]...[/bold cyan]")
        response = self.ec2.describe_route_tables(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "tag:Name", "Values": [name]}
            ]
        )
        rts = response.get("RouteTables", [])
        if rts:
            rt_id = rts[0]["RouteTableId"]
            console.print(f"  [green][OK] Existing Route Table found:[/green] [cyan]{rt_id}[/cyan]")
            rt = rts[0]
        else:
            console.print(f"  [yellow]Creating Route Table {name}...[/yellow]")
            rt = self.ec2.create_route_table(
                VpcId=vpc_id,
                TagSpecifications=self._tag_spec("route-table", name)
            )["RouteTable"]
            rt_id = rt["RouteTableId"]
            console.print(f"  [bold green][OK] Created Route Table:[/bold green] [cyan]{rt_id}[/cyan]")

        # Check Subnet Association
        associations = rt.get("Associations", [])
        is_associated = any(a.get("SubnetId") == subnet_id for a in associations)
        if not is_associated:
            console.print(f"  [yellow]Associating {name} ({rt_id}) with Subnet {subnet_id}...[/yellow]")
            self.ec2.associate_route_table(RouteTableId=rt_id, SubnetId=subnet_id)
            console.print(f"  [bold green][OK] Subnet associated[/bold green]")
        else:
            console.print(f"  [green][OK] Subnet {subnet_id} already associated with {name}[/green]")

        # Default route to IGW if public
        if has_igw_route and igw_id:
            routes = rt.get("Routes", [])
            default_route = [r for r in routes if r.get("DestinationCidrBlock") == "0.0.0.0/0"]
            if not default_route:
                console.print(f"  [yellow]Adding default route 0.0.0.0/0 -> {igw_id}...[/yellow]")
                self.ec2.create_route(
                    RouteTableId=rt_id,
                    DestinationCidrBlock="0.0.0.0/0",
                    GatewayId=igw_id
                )
                console.print(f"  [bold green][OK] Default route added[/bold green]")
            elif default_route[0].get("GatewayId") != igw_id:
                console.print(f"  [yellow]Replacing route 0.0.0.0/0 -> {igw_id}...[/yellow]")
                self.ec2.replace_route(
                    RouteTableId=rt_id,
                    DestinationCidrBlock="0.0.0.0/0",
                    GatewayId=igw_id
                )
                console.print(f"  [bold green][OK] Default route updated[/bold green]")

        self.resources["route_tables"][name] = rt_id
        return rt_id

    # -------------------------------------------------------------
    # 5. Security Group Management
    # -------------------------------------------------------------
    def ensure_security_group(self, name, description, vpc_id, ingress_rules):
        console.print(f"[bold cyan][>] Checking Security Group [yellow]{name}[/yellow]...[/bold cyan]")
        response = self.ec2.describe_security_groups(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "group-name", "Values": [name]}
            ]
        )
        sgs = response.get("SecurityGroups", [])
        if sgs:
            sg_id = sgs[0]["GroupId"]
            console.print(f"  [green][OK] Existing Security Group found:[/green] [cyan]{sg_id}[/cyan]")
        else:
            console.print(f"  [yellow]Creating Security Group {name}...[/yellow]")
            sg = self.ec2.create_security_group(
                GroupName=name,
                Description=description,
                VpcId=vpc_id,
                TagSpecifications=self._tag_spec("security-group", name)
            )
            sg_id = sg["GroupId"]
            console.print(f"  [bold green][OK] Created Security Group:[/bold green] [cyan]{sg_id}[/cyan]")

        # Authorize Ingress Rules Idempotently
        for rule in ingress_rules:
            try:
                self.ec2.authorize_security_group_ingress(
                    GroupId=sg_id,
                    IpPermissions=[rule]
                )
                console.print(f"  [bold green][OK] Ingress rule authorized on {name}:[/bold green] proto {rule.get('IpProtocol')} port {rule.get('FromPort')}-{rule.get('ToPort')}")
            except ClientError as e:
                if "InvalidPermission.Duplicate" in str(e):
                    console.print(f"  [green][OK] Ingress rule already exists on {name}[/green]")
                else:
                    raise

        self.resources["security_groups"][name] = sg_id
        return sg_id

    # -------------------------------------------------------------
    # 6. VPC Peering Connection Management
    # -------------------------------------------------------------
    def ensure_peering_connection(self, name, requester_vpc_id, accepter_vpc_id):
        console.print(f"[bold cyan][>] Checking VPC Peering Connection [yellow]{name}[/yellow]...[/bold cyan]")
        response = self.ec2.describe_vpc_peering_connections(
            Filters=[
                {"Name": "requester-vpc-info.vpc-id", "Values": [requester_vpc_id]},
                {"Name": "accepter-vpc-info.vpc-id", "Values": [accepter_vpc_id]},
                {"Name": "status-code", "Values": ["pending-acceptance", "active", "provisioning"]}
            ]
        )
        connections = response.get("VpcPeeringConnections", [])
        if connections:
            pcx = connections[0]
            pcx_id = pcx["VpcPeeringConnectionId"]
            status = pcx["Status"]["Code"]
            console.print(f"  [green][OK] Existing Peering Connection found:[/green] [cyan]{pcx_id}[/cyan] (Status: [yellow]{status}[/yellow])")
        else:
            console.print(f"  [yellow]Requesting VPC Peering between {requester_vpc_id} and {accepter_vpc_id}...[/yellow]")
            pcx = self.ec2.create_vpc_peering_connection(
                VpcId=requester_vpc_id,
                PeerVpcId=accepter_vpc_id,
                TagSpecifications=self._tag_spec("vpc-peering-connection", name)
            )["VpcPeeringConnection"]
            pcx_id = pcx["VpcPeeringConnectionId"]
            status = pcx["Status"]["Code"]
            console.print(f"  [bold green][OK] Peering Request Created:[/bold green] [cyan]{pcx_id}[/cyan]")

        # Wait until active, accepting automatically if pending-acceptance
        console.print(f"  [cyan]Waiting for Peering Connection {pcx_id} to reach active state...[/cyan]")
        accepted = False
        while True:
            desc = self.ec2.describe_vpc_peering_connections(VpcPeeringConnectionIds=[pcx_id])["VpcPeeringConnections"][0]
            current_status = desc["Status"]["Code"]
            if current_status == "pending-acceptance" and not accepted:
                console.print(f"  [yellow]Accepting VPC Peering Connection {pcx_id}...[/yellow]")
                try:
                    self.ec2.accept_vpc_peering_connection(VpcPeeringConnectionId=pcx_id)
                    console.print(f"  [bold green][OK] Accepted VPC Peering Connection {pcx_id}[/bold green]")
                    accepted = True
                except ClientError as e:
                    if "InvalidState" not in str(e):
                        raise
            elif current_status == "active":
                console.print(f"  [bold green][OK] Peering Connection is ACTIVE:[/bold green] [cyan]{pcx_id}[/cyan]")
                break
            elif current_status in ["failed", "rejected", "deleted"]:
                raise RuntimeError(f"Peering connection failed with status: {current_status}")
            time.sleep(2)

        self.resources["peering"][name] = pcx_id
        return pcx_id

    # -------------------------------------------------------------
    # 7. Add Peering Routes to Route Tables
    # -------------------------------------------------------------
    def ensure_peering_routes(self, pcx_id):
        console.print("[bold cyan][>] Configuring Routes for VPC Peering...[/bold cyan]")
        for route_spec in config.PEERING["routes"]:
            rt_name = route_spec["route_table"]
            cidr = route_spec["destination_cidr"]
            rt_id = self.resources["route_tables"][rt_name]

            rt_desc = self.ec2.describe_route_tables(RouteTableIds=[rt_id])["RouteTables"][0]
            existing_routes = [
                r for r in rt_desc.get("Routes", [])
                if r.get("DestinationCidrBlock") == cidr
            ]

            if not existing_routes:
                console.print(f"  [yellow]Adding route {cidr} -> {pcx_id} in {rt_name} ({rt_id})...[/yellow]")
                self.ec2.create_route(
                    RouteTableId=rt_id,
                    DestinationCidrBlock=cidr,
                    VpcPeeringConnectionId=pcx_id
                )
                console.print(f"  [bold green][OK] Peering route added to {rt_name}[/bold green]")
            elif existing_routes[0].get("VpcPeeringConnectionId") != pcx_id:
                console.print(f"  [yellow]Updating route {cidr} -> {pcx_id} in {rt_name} ({rt_id})...[/yellow]")
                self.ec2.replace_route(
                    RouteTableId=rt_id,
                    DestinationCidrBlock=cidr,
                    VpcPeeringConnectionId=pcx_id
                )
                console.print(f"  [bold green][OK] Peering route updated in {rt_name}[/bold green]")
            else:
                console.print(f"  [green][OK] Route {cidr} -> {pcx_id} already present in {rt_name}[/green]")

    # -------------------------------------------------------------
    # 8. EC2 Instance Management
    # -------------------------------------------------------------
    def ensure_instance(self, name, inst_config, ami_id, key_name):
        console.print(f"[bold cyan][>] Checking EC2 Instance [yellow]{name}[/yellow]...[/bold cyan]")
        subnet_id = self.resources["subnets"][inst_config["subnet"]]
        sg_id = self.resources["security_groups"][inst_config["security_group"]]
        private_ip = inst_config["private_ip"]

        # Check existing instance
        response = self.ec2.describe_instances(
            Filters=[
                {"Name": "tag:Name", "Values": [name]},
                {"Name": "instance-state-name", "Values": ["pending", "running", "stopped", "stopping"]}
            ]
        )
        instances = []
        for res in response.get("Reservations", []):
            instances.extend(res.get("Instances", []))

        if instances:
            instance = instances[0]
            inst_id = instance["InstanceId"]
            state = instance["State"]["Name"]
            console.print(f"  [green][OK] Existing Instance found:[/green] [cyan]{inst_id}[/cyan] (State: [yellow]{state}[/yellow], IP: [yellow]{instance.get('PrivateIpAddress')}[/yellow])")

            if state == "stopped":
                console.print(f"  [yellow]Starting stopped instance {inst_id}...[/yellow]")
                self.ec2.start_instances(InstanceIds=[inst_id])
                state = "pending"
        else:
            console.print(f"  [yellow]Launching new instance {name} ({config.INSTANCE_TYPE}, IP {private_ip})...[/yellow]")
            network_interface = {
                "DeviceIndex": 0,
                "SubnetId": subnet_id,
                "PrivateIpAddress": private_ip,
                "Groups": [sg_id],
                "AssociatePublicIpAddress": inst_config["associate_public_ip"]
            }

            run_args = {
                "ImageId": ami_id,
                "InstanceType": config.INSTANCE_TYPE,
                "KeyName": key_name,
                "MinCount": 1,
                "MaxCount": 1,
                "NetworkInterfaces": [network_interface],
                "TagSpecifications": self._tag_spec("instance", name)
            }

            run_res = self.ec2.run_instances(**run_args)
            instance = run_res["Instances"][0]
            inst_id = instance["InstanceId"]
            state = instance["State"]["Name"]
            console.print(f"  [bold green][OK] Instance launched:[/bold green] [cyan]{inst_id}[/cyan]")

        # Wait until running
        if state != "running":
            console.print(f"  [cyan]Waiting for {name} ({inst_id}) to reach running state...[/cyan]")
            waiter = self.ec2.get_waiter("instance_running")
            waiter.wait(InstanceIds=[inst_id])
            console.print(f"  [bold green][OK] Instance {name} is running[/bold green]")

        # Refresh instance attributes
        refreshed = self.ec2.describe_instances(InstanceIds=[inst_id])["Reservations"][0]["Instances"][0]
        pub_ip = refreshed.get("PublicIpAddress", "N/A")
        priv_ip = refreshed.get("PrivateIpAddress", "N/A")

        self.resources["instances"][name] = {
            "id": inst_id,
            "private_ip": priv_ip,
            "public_ip": pub_ip,
            "subnet": inst_config["subnet"]
        }
        return self.resources["instances"][name]

    # -------------------------------------------------------------
    # Main Orchestrator
    # -------------------------------------------------------------
    def run(self):
        console.print(Panel.fit(
            "[bold white on blue] EWB VPC PEERING AUTOMATION DEPLOYMENT [/bold white on blue]\n"
            f"[dim]Region: {config.REGION} | Target: VPC A & VPC B Peering[/dim]",
            border_style="cyan"
        ))

        # Phase 1: Key Pair & AMI
        key_name = ensure_key_pair(self.ec2)
        ami_id = get_latest_al2023_ami(self.session)
        bastion_ingress_ip = get_my_public_ip()

        # Phase 2: VPCs
        vpc_a_id = self.ensure_vpc(config.VPC_A["name"], config.VPC_A["cidr"])
        vpc_b_id = self.ensure_vpc(config.VPC_B["name"], config.VPC_B["cidr"])

        # Phase 3: Subnets
        sub_a_pub = self.ensure_subnet(
            config.VPC_A["subnets"]["public"]["name"],
            config.VPC_A["subnets"]["public"]["cidr"],
            vpc_a_id,
            map_public_ip=True
        )
        sub_a_priv = self.ensure_subnet(
            config.VPC_A["subnets"]["private"]["name"],
            config.VPC_A["subnets"]["private"]["cidr"],
            vpc_a_id,
            map_public_ip=False
        )
        sub_b_priv = self.ensure_subnet(
            config.VPC_B["subnets"]["private"]["name"],
            config.VPC_B["subnets"]["private"]["cidr"],
            vpc_b_id,
            map_public_ip=False
        )

        # Phase 4: Internet Gateway for VPC A
        igw_a_id = self.ensure_igw(config.VPC_A["igw_name"], vpc_a_id)

        # Phase 5: Route Tables
        self.ensure_route_table("ewb-rt-a-public", vpc_a_id, sub_a_pub, has_igw_route=True, igw_id=igw_a_id)
        self.ensure_route_table("ewb-rt-a-private", vpc_a_id, sub_a_priv, has_igw_route=False)
        self.ensure_route_table("ewb-rt-b-private", vpc_b_id, sub_b_priv, has_igw_route=False)

        # Phase 6: Security Groups
        bastion_rules = [{
            "IpProtocol": "tcp",
            "FromPort": 22,
            "ToPort": 22,
            "IpRanges": [{"CidrIp": bastion_ingress_ip, "Description": "SSH from admin workstation"}]
        }]
        self.ensure_security_group("ewb-sg-bastion", config.SECURITY_GROUPS["ewb-sg-bastion"]["description"], vpc_a_id, bastion_rules)
        self.ensure_security_group("ewb-sg-app-a", config.SECURITY_GROUPS["ewb-sg-app-a"]["description"], vpc_a_id, config.SECURITY_GROUPS["ewb-sg-app-a"]["ingress"])
        self.ensure_security_group("ewb-sg-app-b", config.SECURITY_GROUPS["ewb-sg-app-b"]["description"], vpc_b_id, config.SECURITY_GROUPS["ewb-sg-app-b"]["ingress"])

        # Phase 7: VPC Peering Connection & Peering Routes
        pcx_id = self.ensure_peering_connection(config.PEERING["name"], vpc_a_id, vpc_b_id)
        self.ensure_peering_routes(pcx_id)

        # Phase 8: EC2 Instances
        for inst_name, inst_cfg in config.INSTANCES.items():
            self.ensure_instance(inst_name, inst_cfg, ami_id, key_name)

        # Print Consolidated Summary Table
        summary_rows = [
            ("VPC", "ewb-vpc-a", f"{vpc_a_id} ({config.VPC_A['cidr']})"),
            ("VPC", "ewb-vpc-b", f"{vpc_b_id} ({config.VPC_B['cidr']})"),
            ("Subnet (Public)", "ewb-a-public", f"{sub_a_pub} (10.10.1.0/24)"),
            ("Subnet (Private)", "ewb-a-private", f"{sub_a_priv} (10.10.2.0/24)"),
            ("Subnet (Private)", "ewb-b-private", f"{sub_b_priv} (10.20.1.0/24)"),
            ("Internet Gateway", "ewb-igw-a", f"{igw_a_id} attached to ewb-vpc-a"),
            ("Route Table", "ewb-rt-a-public", f"{self.resources['route_tables']['ewb-rt-a-public']} (0.0.0.0/0 -> IGW, 10.20.0.0/16 -> PCX)"),
            ("Route Table", "ewb-rt-a-private", f"{self.resources['route_tables']['ewb-rt-a-private']} (10.20.0.0/16 -> PCX)"),
            ("Route Table", "ewb-rt-b-private", f"{self.resources['route_tables']['ewb-rt-b-private']} (10.10.0.0/16 -> PCX)"),
            ("VPC Peering", "ewb-a-b-peer", f"{pcx_id} (Active, VPC-A <-> VPC-B)"),
            ("Security Group", "ewb-sg-bastion", f"{self.resources['security_groups']['ewb-sg-bastion']} (SSH from {bastion_ingress_ip})"),
            ("Security Group", "ewb-sg-app-a", f"{self.resources['security_groups']['ewb-sg-app-a']} (SSH from 10.10.1.10/32, ICMP from 10.20.1.10/32)"),
            ("Security Group", "ewb-sg-app-b", f"{self.resources['security_groups']['ewb-sg-app-b']} (SSH from 10.10.1.10/32, ICMP from 10.10.2.10/32)"),
            ("EC2 Instance", "ewb-bastion", f"ID: {self.resources['instances']['ewb-bastion']['id']} | Priv: {self.resources['instances']['ewb-bastion']['private_ip']} | Pub: {self.resources['instances']['ewb-bastion']['public_ip']}"),
            ("EC2 Instance", "ewb-app-a", f"ID: {self.resources['instances']['ewb-app-a']['id']} | Priv: {self.resources['instances']['ewb-app-a']['private_ip']}"),
            ("EC2 Instance", "ewb-app-b", f"ID: {self.resources['instances']['ewb-app-b']['id']} | Priv: {self.resources['instances']['ewb-app-b']['private_ip']}")
        ]

        console.print("\n")
        print_summary_table("EWB VPC Peering Infrastructure Summary", summary_rows)

        bastion_pub_ip = self.resources['instances']['ewb-bastion']['public_ip']
        key_file_path = f".\\{config.KEY_FILE_NAME}"

        console.print(Panel(
            f"[bold green]Infrastructure Successfully Provisioned & Peered![/bold green]\n\n"
            f"[bold cyan]How to Test Peering & Verification:[/bold cyan]\n"
            f"1. Connect to Bastion host using SSH:\n"
            f"   [yellow]ssh -i {key_file_path} ec2-user@{bastion_pub_ip}[/yellow]\n\n"
            f"2. From your workstation, jump through Bastion to ewb-app-a:\n"
            f"   [yellow]ssh -i {key_file_path} -J ec2-user@{bastion_pub_ip} ec2-user@10.10.2.10[/yellow]\n\n"
            f"3. Inside ewb-app-a, ping ewb-app-b across the VPC Peering connection:\n"
            f"   [yellow]ping -c 4 10.20.1.10[/yellow]\n"
            f"   [dim](ICMP echo packets will traverse ewb-rt-a-private -> {pcx_id} -> ewb-vpc-b -> ewb-app-b)[/dim]\n\n"
            f"4. Run automated configuration verification:\n"
            f"   [yellow]python verify.py[/yellow]",
            title="Verification & Next Steps",
            border_style="green"
        ))

if __name__ == "__main__":
    try:
        deployer = EWBDeployer()
        deployer.run()
    except Exception as e:
        console.print(f"[bold red]Deployment failed:[/bold red] {e}")
        sys.exit(1)
