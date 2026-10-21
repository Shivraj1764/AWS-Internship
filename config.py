"""
Configuration specifications for the EWB VPC Peering project.
Adheres strictly to the architectural requirements in region ap-south-1.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load local environment variables if .env file exists
env_path = Path(__file__).resolve().parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

PROJECT_TAG = "EWB-VPC-Peering"
COMMON_TAGS = [
    {"Key": "Project", "Value": PROJECT_TAG},
    {"Key": "ManagedBy", "Value": "Boto3-Automation"}
]

REGION = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-2")
KEY_PAIR_NAME = "ewb-lab"
KEY_FILE_NAME = "ewb-lab.pem"
INSTANCE_TYPE = "t3.micro"

# Amazon Linux 2023 SSM parameter path for latest x86_64 AMI
AL2023_SSM_PARAM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"

# VPC Configurations
VPC_A = {
    "name": "ewb-vpc-a",
    "cidr": "10.10.0.0/16",
    "igw_name": "ewb-igw-a",
    "subnets": {
        "public": {
            "name": "ewb-a-public",
            "cidr": "10.10.1.0/24",
            "map_public_ip": True,
            "route_table": "ewb-rt-a-public"
        },
        "private": {
            "name": "ewb-a-private",
            "cidr": "10.10.2.0/24",
            "map_public_ip": False,
            "route_table": "ewb-rt-a-private"
        }
    },
    "route_tables": {
        "ewb-rt-a-public": {
            "has_igw_route": True
        },
        "ewb-rt-a-private": {
            "has_igw_route": False
        }
    }
}

VPC_B = {
    "name": "ewb-vpc-b",
    "cidr": "10.20.0.0/16",
    "subnets": {
        "private": {
            "name": "ewb-b-private",
            "cidr": "10.20.1.0/24",
            "map_public_ip": False,
            "route_table": "ewb-rt-b-private"
        }
    },
    "route_tables": {
        "ewb-rt-b-private": {
            "has_igw_route": False
        }
    }
}

# Security Groups
SECURITY_GROUPS = {
    "ewb-sg-bastion": {
        "description": "Bastion Security Group allowing SSH from current public IP",
        "vpc": "ewb-vpc-a",
        # Ingress rules populated dynamically with public IP
    },
    "ewb-sg-app-a": {
        "description": "App A SG allowing SSH from Bastion and ICMP Echo from App B",
        "vpc": "ewb-vpc-a",
        "ingress": [
            {
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [{"CidrIp": "10.10.1.10/32", "Description": "SSH from ewb-bastion"}]
            },
            {
                "IpProtocol": "icmp",
                "FromPort": 8,  # Echo Request
                "ToPort": 0,    # ICMP Code 0
                "IpRanges": [{"CidrIp": "10.20.1.10/32", "Description": "ICMP Echo Request from ewb-app-b"}]
            }
        ]
    },
    "ewb-sg-app-b": {
        "description": "App B SG allowing SSH from Bastion and ICMP Echo from App A",
        "vpc": "ewb-vpc-b",
        "ingress": [
            {
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [{"CidrIp": "10.10.1.10/32", "Description": "SSH from ewb-bastion"}]
            },
            {
                "IpProtocol": "icmp",
                "FromPort": 8,  # Echo Request
                "ToPort": 0,    # ICMP Code 0
                "IpRanges": [{"CidrIp": "10.10.2.10/32", "Description": "ICMP Echo Request from ewb-app-a"}]
            }
        ]
    }
}

# Compute Instances
INSTANCES = {
    "ewb-bastion": {
        "vpc": "ewb-vpc-a",
        "subnet": "ewb-a-public",
        "security_group": "ewb-sg-bastion",
        "private_ip": "10.10.1.10",
        "associate_public_ip": True
    },
    "ewb-app-a": {
        "vpc": "ewb-vpc-a",
        "subnet": "ewb-a-private",
        "security_group": "ewb-sg-app-a",
        "private_ip": "10.10.2.10",
        "associate_public_ip": False
    },
    "ewb-app-b": {
        "vpc": "ewb-vpc-b",
        "subnet": "ewb-b-private",
        "security_group": "ewb-sg-app-b",
        "private_ip": "10.20.1.10",
        "associate_public_ip": False
    }
}

# Peering Connection
PEERING = {
    "name": "ewb-a-b-peer",
    "requester_vpc": "ewb-vpc-a",
    "accepter_vpc": "ewb-vpc-b",
    "routes": [
        {
            "route_table": "ewb-rt-a-public",
            "destination_cidr": "10.20.0.0/16"
        },
        {
            "route_table": "ewb-rt-a-private",
            "destination_cidr": "10.20.0.0/16"
        },
        {
            "route_table": "ewb-rt-b-private",
            "destination_cidr": "10.10.0.0/16"
        }
    ]
}
