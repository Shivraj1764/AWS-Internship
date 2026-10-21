# EWB VPC Peering Project - Proof-of-Work Evidence Report

**Generated:** 2026-10-21 05:15:02 UTC  
**AWS Region:** `ap-southeast-2`  
**Project Identifier:** `EWB-VPC-Peering`  
**Automation Engine:** Python 3.13 / Boto3 / OpenSSH  

---

## 1. Executive Summary

This report documents the live integration tests, latency benchmarks, and security enforcement validations conducted across the **EWB VPC Peering connection (`pcx-0bf20e1df91fab1f4`)**. 

| Test ID | Test Scenario | Expected Outcome | Actual Result | Status |
| :--- | :--- | :--- | :--- | :---: |
| **TEST-1** | Baseline Connectivity (No Route) | 100% packet loss / Destination Unreachable | 100% packet loss (Confirmed Unreachable) | **PASS** |
| **TEST-2A** | Cross-VPC Ping (App-A -> App-B) | 0% packet loss across VPC peering | 0% packet loss (verified 4/4 packets) | **PASS** |
| **TEST-2B** | Cross-VPC Ping (App-B -> App-A) | 0% packet loss across VPC peering | 0% packet loss (verified 4/4 packets) | **PASS** |
| **TEST-3A** | Security Group Drop (ICMP Revoked) | 100% packet loss enforced by SG | 100% packet loss confirmed | **PASS** |
| **TEST-3B** | Security Group Restore (ICMP Re-added) | 0% packet loss restored | 0% packet loss confirmed (verified 4/4) | **PASS** |

---

## 2. Infrastructure Inventory & Topology Map

```
+---------------------------------------------------------------------------------------------------------+
|                                    LIVE AWS INFRASTRUCTURE TOPOLOGY                                     |
+---------------------------------------------------------------------------------------------------------+
| VPC A: ewb-vpc-a (vpc-0a4cb0748834cb862) | CIDR: 10.10.0.0/16                                          |
|   |-- Public Subnet:  ewb-a-public  (subnet-0c169429bf5f026c4) | CIDR: 10.10.1.0/24                     |
|   |   `-- Instance:   ewb-bastion   (i-0914610f77ccec77e) | PrivIP: 10.10.1.10 | PubIP: 13.236.1.227    |
|   |-- Private Subnet: ewb-a-private (subnet-0abf6365371ea43ab) | CIDR: 10.10.2.0/24                     |
|   |   `-- Instance:   ewb-app-a     (i-0e9845278c43d5f7f) | PrivIP: 10.10.2.10 | PubIP: None           |
|   |-- Gateway:        ewb-igw-a     (igw-04df15110373f247f)                                             |
|   |-- Route Table:    ewb-rt-a-pub  (rtb-01fd1c71f44443cae) -> 0.0.0.0/0 (IGW), 10.20.0.0/16 (PCX)     |
|   `-- Route Table:    ewb-rt-a-priv (rtb-042f333f4275555d2) -> 10.20.0.0/16 (PCX)                       |
+---------------------------------------------------------------------------------------------------------+
                                                  ||
                                [VPC Peering: pcx-0bf20e1df91fab1f4 | ACTIVE]
                                                  ||
+---------------------------------------------------------------------------------------------------------+
| VPC B: ewb-vpc-b (vpc-0d9716147a7bfc61f) | CIDR: 10.20.0.0/16                                          |
|   |-- Private Subnet: ewb-b-private (subnet-09babb6754136174a) | CIDR: 10.20.1.0/24                     |
|   |   `-- Instance:   ewb-app-b     (i-06d9c825540943530) | PrivIP: 10.20.1.10 | PubIP: None           |
|   `-- Route Table:    ewb-rt-b-priv (rtb-034929ac6d721edd7) -> 10.10.0.0/16 (PCX)                       |
+---------------------------------------------------------------------------------------------------------+
```

---

## 3. Raw Terminal Logs & Test Evidence

### 3.1 Test 1: Baseline Ping (Before Peering Route)
*Verifies that without the route pointing to the peering connection, packets fail with 100% packet loss.*

```text
=== TEST 1: BASELINE PING BEFORE ROUTE INJECTION ===
Timestamp: 2026-10-21T05:14:01.685577+00:00
Source: ewb-app-a (10.10.2.10 in VPC A)
Target: ewb-app-b (10.20.1.10 in VPC B)
Route State: 10.20.0.0/16 -> PCX REMOVED from ewb-rt-a-private
Exit Code: 1

PING 10.20.1.10 (10.20.1.10) 56(84) bytes of data.

--- 10.20.1.10 ping statistics ---
4 packets transmitted, 0 received, 100% packet loss, time 3133ms

```

---

### 3.2 Test 2A: Successful Ping from App-A (10.10.2.10) to App-B (10.20.1.10)
*Confirms 0% packet loss and sub-millisecond latency across VPC Peering.*

```text
=== TEST 2A: PING APP-A TO APP-B (PEERING ACTIVE) ===
Timestamp: 2026-10-21T05:14:17.036868+00:00
Source: ewb-app-a (10.10.2.10 in VPC A)
Target: ewb-app-b (10.20.1.10 in VPC B)
Peering ID: pcx-0bf20e1df91fab1f4
Exit Code: 0

PING 10.20.1.10 (10.20.1.10) 56(84) bytes of data.
64 bytes from 10.20.1.10: icmp_seq=1 ttl=127 time=0.791 ms
64 bytes from 10.20.1.10: icmp_seq=2 ttl=127 time=0.799 ms
64 bytes from 10.20.1.10: icmp_seq=3 ttl=127 time=0.784 ms
64 bytes from 10.20.1.10: icmp_seq=4 ttl=127 time=0.795 ms

--- 10.20.1.10 ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 3091ms
rtt min/avg/max/mdev = 0.784/0.792/0.799/0.005 ms

```

---

### 3.3 Test 2B: Successful Ping from App-B (10.20.1.10) to App-A (10.10.2.10)
*Confirms bidirectional return routing and asymmetric packet handling across VPC Peering.*

```text
=== TEST 2B: PING APP-B TO APP-A (PEERING ACTIVE) ===
Timestamp: 2026-10-21T05:14:29.080456+00:00
Source: ewb-app-b (10.20.1.10 in VPC B)
Target: ewb-app-a (10.10.2.10 in VPC A)
Peering ID: pcx-0bf20e1df91fab1f4
Exit Code: 0

PING 10.10.2.10 (10.10.2.10) 56(84) bytes of data.
64 bytes from 10.10.2.10: icmp_seq=1 ttl=127 time=0.790 ms
64 bytes from 10.10.2.10: icmp_seq=2 ttl=127 time=0.786 ms
64 bytes from 10.10.2.10: icmp_seq=3 ttl=127 time=0.806 ms
64 bytes from 10.10.2.10: icmp_seq=4 ttl=127 time=0.809 ms

--- 10.10.2.10 ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 3104ms
rtt min/avg/max/mdev = 0.786/0.797/0.809/0.009 ms

```

---

### 3.4 Test 3A: Security Group Ingress Enforcement (ICMP Revoked)
*Demonstrates that removing the ICMP rule causes immediate packet dropping (100% loss).*

```text
=== TEST 3A: SECURITY GROUP ENFORCEMENT - ICMP BLOCKED ===
Timestamp: 2026-10-21T05:14:45.361676+00:00
Security Group: ewb-sg-app-b (sg-010a3c67752a85558)
Action: Revoked ICMP Echo rule from 10.10.2.10/32
Exit Code: 1

PING 10.20.1.10 (10.20.1.10) 56(84) bytes of data.

--- 10.20.1.10 ping statistics ---
3 packets transmitted, 0 received, 100% packet loss, time 2074ms

```

---

### 3.5 Test 3B: Security Group Ingress Restoration (ICMP Re-added)
*Demonstrates immediate recovery of connectivity upon re-authorizing the ICMP rule.*

```text
=== TEST 3B: SECURITY GROUP ENFORCEMENT - ICMP RESTORED ===
Timestamp: 2026-10-21T05:15:02.164633+00:00
Security Group: ewb-sg-app-b (sg-010a3c67752a85558)
Action: Re-authorized ICMP Echo rule from 10.10.2.10/32
Exit Code: 0

PING 10.20.1.10 (10.20.1.10) 56(84) bytes of data.
64 bytes from 10.20.1.10: icmp_seq=1 ttl=127 time=0.794 ms
64 bytes from 10.20.1.10: icmp_seq=2 ttl=127 time=0.776 ms
64 bytes from 10.20.1.10: icmp_seq=3 ttl=127 time=0.785 ms
64 bytes from 10.20.1.10: icmp_seq=4 ttl=127 time=0.782 ms

--- 10.20.1.10 ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 3093ms
rtt min/avg/max/mdev = 0.776/0.784/0.794/0.006 ms

```

---

## 4. Evidence Artifact Inventory

The following evidence artifacts have been generated and archived in `evidence/`:
1. `evidence/logs/01_baseline_ping_no_route.log`
2. `evidence/logs/02_ping_app_a_to_app_b_success.log`
3. `evidence/logs/03_ping_app_b_to_app_a_success.log`
4. `evidence/logs/04_sg_enforcement_icmp_blocked.log`
5. `evidence/logs/05_sg_enforcement_icmp_restored.log`
6. `evidence/json_state/vpc_peering_state.json`
7. `evidence/json_state/route_tables_state.json`
8. `evidence/json_state/security_groups_state.json`
9. `evidence/json_state/ec2_instances_state.json`
10. `evidence/json_state/vpcs_and_subnets_state.json`
