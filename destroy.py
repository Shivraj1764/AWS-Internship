"""
Resource Teardown / Destruction Script for EWB VPC Peering project.
Safely deprovisions all created cloud resources in reverse dependency order:
Instances -> Peering -> Route Tables -> Security Groups -> IGW -> Subnets -> VPCs.
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
from utils import get_boto3_session

console = Console(safe_box=True)

class EWBDestroyer:
    def __init__(self):
        self.session = get_boto3_session()
        self.ec2 = self.session.client("ec2", region_name=config.REGION)

    def terminate_instances(self):
        console.print("[bold cyan][>] Terminating EC2 Instances...[/bold cyan]")
        response = self.ec2.describe_instances(
            Filters=[
                {"Name": "tag:Project", "Values": [config.PROJECT_TAG]},
                {"Name": "instance-state-name", "Values": ["pending", "running", "stopped", "stopping"]}
            ]
        )
        instance_ids = []
        for r in response.get("Reservations", []):
            for i in r.get("Instances", []):
                instance_ids.append(i["InstanceId"])

        if instance_ids:
            console.print(f"  [yellow]Terminating instances: {instance_ids}...[/yellow]")
            self.ec2.terminate_instances(InstanceIds=instance_ids)
            console.print("  [cyan]Waiting for instances to terminate...[/cyan]")
            waiter = self.ec2.get_waiter("instance_terminated")
            waiter.wait(InstanceIds=instance_ids)
            console.print("  [bold green][OK] All instances terminated.[/bold green]")
        else:
            console.print("  [green][OK] No active instances found.[/green]")

    def delete_peering(self):
        console.print("[bold cyan][>] Deleting VPC Peering Connection...[/bold cyan]")
        response = self.ec2.describe_vpc_peering_connections(
            Filters=[
                {"Name": "tag:Project", "Values": [config.PROJECT_TAG]},
                {"Name": "status-code", "Values": ["active", "pending-acceptance", "provisioning"]}
            ]
        )
        for pcx in response.get("VpcPeeringConnections", []):
            pcx_id = pcx["VpcPeeringConnectionId"]
            console.print(f"  [yellow]Deleting Peering Connection {pcx_id}...[/yellow]")
            self.ec2.delete_vpc_peering_connection(VpcPeeringConnectionId=pcx_id)
            console.print(f"  [bold green][OK] Peering Connection {pcx_id} deleted.[/bold green]")

    def delete_route_tables(self):
        console.print("[bold cyan][>] Deleting Route Tables...[/bold cyan]")
        response = self.ec2.describe_route_tables(
            Filters=[{"Name": "tag:Project", "Values": [config.PROJECT_TAG]}]
        )
        for rt in response.get("RouteTables", []):
            rt_id = rt["RouteTableId"]
            for a in rt.get("Associations", []):
                if not a.get("Main", False):
                    assoc_id = a["RouteTableAssociationId"]
                    try:
                        self.ec2.disassociate_route_table(AssociationId=assoc_id)
                        console.print(f"  [green][OK] Disassociated route table {rt_id} from subnet[/green]")
                    except ClientError as e:
                        console.print(f"  [yellow]Failed to disassociate {assoc_id}: {e}[/yellow]")
            try:
                self.ec2.delete_route_table(RouteTableId=rt_id)
                console.print(f"  [bold green][OK] Deleted Route Table {rt_id}[/bold green]")
            except ClientError as e:
                console.print(f"  [yellow]Could not delete route table {rt_id}: {e}[/yellow]")

    def delete_security_groups(self):
        console.print("[bold cyan][>] Deleting Security Groups...[/bold cyan]")
        response = self.ec2.describe_security_groups(
            Filters=[{"Name": "tag:Project", "Values": [config.PROJECT_TAG]}]
        )
        sgs = response.get("SecurityGroups", [])
        for attempt in range(5):
            remaining = []
            for sg in sgs:
                sg_id = sg["GroupId"]
                try:
                    self.ec2.delete_security_group(GroupId=sg_id)
                    console.print(f"  [bold green][OK] Deleted Security Group {sg_id} ({sg.get('GroupName')})[/bold green]")
                except ClientError:
                    remaining.append(sg)
            sgs = remaining
            if not sgs:
                break
            time.sleep(2)

    def delete_igws(self):
        console.print("[bold cyan][>] Detaching & Deleting Internet Gateways...[/bold cyan]")
        response = self.ec2.describe_internet_gateways(
            Filters=[{"Name": "tag:Project", "Values": [config.PROJECT_TAG]}]
        )
        for igw in response.get("InternetGateways", []):
            igw_id = igw["InternetGatewayId"]
            for a in igw.get("Attachments", []):
                vpc_id = a["VpcId"]
                console.print(f"  [yellow]Detaching IGW {igw_id} from VPC {vpc_id}...[/yellow]")
                self.ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
            console.print(f"  [yellow]Deleting IGW {igw_id}...[/yellow]")
            self.ec2.delete_internet_gateway(InternetGatewayId=igw_id)
            console.print(f"  [bold green][OK] Deleted IGW {igw_id}[/bold green]")

    def delete_subnets(self):
        console.print("[bold cyan][>] Deleting Subnets...[/bold cyan]")
        response = self.ec2.describe_subnets(
            Filters=[{"Name": "tag:Project", "Values": [config.PROJECT_TAG]}]
        )
        for s in response.get("Subnets", []):
            subnet_id = s["SubnetId"]
            console.print(f"  [yellow]Deleting Subnet {subnet_id}...[/yellow]")
            for _ in range(10):
                try:
                    self.ec2.delete_subnet(SubnetId=subnet_id)
                    console.print(f"  [bold green][OK] Deleted Subnet {subnet_id}[/bold green]")
                    break
                except ClientError:
                    time.sleep(3)

    def delete_vpcs(self):
        console.print("[bold cyan][>] Deleting VPCs...[/bold cyan]")
        response = self.ec2.describe_vpcs(
            Filters=[{"Name": "tag:Project", "Values": [config.PROJECT_TAG]}]
        )
        for v in response.get("Vpcs", []):
            vpc_id = v["VpcId"]
            console.print(f"  [yellow]Deleting VPC {vpc_id}...[/yellow]")
            for _ in range(5):
                try:
                    self.ec2.delete_vpc(VpcId=vpc_id)
                    console.print(f"  [bold green][OK] Deleted VPC {vpc_id}[/bold green]")
                    break
                except ClientError:
                    time.sleep(3)

    def run(self):
        console.print(Panel.fit(
            "[bold white on red] EWB VPC PEERING TEARDOWN / DESTRUCTION [/bold white on red]\n"
            f"[dim]Region: {config.REGION} | Target: All resources tagged Project={config.PROJECT_TAG}[/dim]",
            border_style="red"
        ))

        self.terminate_instances()
        self.delete_peering()
        self.delete_route_tables()
        self.delete_security_groups()
        self.delete_igws()
        self.delete_subnets()
        self.delete_vpcs()

        console.print(Panel("[bold green][OK] All EWB VPC Peering resources successfully deprovisioned![/bold green]", border_style="green"))

if __name__ == "__main__":
    try:
        destroyer = EWBDestroyer()
        destroyer.run()
    except Exception as e:
        console.print(f"[bold red]Teardown encountered an error:[/bold red] {e}")
        sys.exit(1)
